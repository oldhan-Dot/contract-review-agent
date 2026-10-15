"""风险结构与证据一致性测试；不执行规则，也不连接数据库或模型。"""

import pytest
from pydantic import ValidationError

from app.graph.nodes.structure_document import structure_document
from app.parsing.ir import ParsedDocument
from app.review.context import RuleReviewInput
from app.review.models import RiskItem, RiskResult
from app.structuring.anchor import Anchor
from app.structuring.metadata_models import MetadataResult


def document(*texts):
    paragraphs, cursor = [], 0
    for index, text in enumerate(texts):
        paragraphs.append(
            dict(
                pid=f"file-12:p{index}",
                index=index,
                text=text,
                char_start=cursor,
                char_end=cursor + len(text),
            )
        )
        cursor += len(text) + 1
    full_text = "\n".join(texts)
    return ParsedDocument(
        doc_id="file-12",
        file_id=12,
        source_format="DOCX",
        channel="docx",
        paragraphs=paragraphs,
        full_text=full_text,
        quality=dict(precision_cap="EXACT", char_count=len(full_text), paragraph_count=len(texts)),
    )


def make_input(parsed):
    state = dict(
        task_id=1,
        file_id=12,
        review_version=1,
        document=parsed,
        contract_type="PURCHASE",
        our_party_role="A",
    )
    state.update(structure_document(state))
    return RuleReviewInput.from_state(state)


@pytest.fixture
def context():
    return make_input(
        document(
            "设备采购合同",
            "第一条 保密义务",
            "不得披露商业秘密。",
            "第二条 金额与付款",
            "合同金额：￥1000元",
        )
    )


def clause_risk(context, **updates):
    clause = next(item for item in context.clauses if item.clause_type == "CONFIDENTIALITY")
    start = context.document.full_text.index("商业秘密")
    data = dict(
        risk_name="测试条款风险",
        risk_type="CLAUSE_RISK",
        risk_level="HIGH",
        source="rule",
        rule_code="R-CON-01",
        clause_index=clause.index,
        clause_type=clause.clause_type,
        risk_reason="仅用于验证结构与证据，不表示真实规则命中",
        original_text="商业秘密",
        anchor=Anchor.from_document_range(context.document, start, start + 4, anchor_type="clause"),
    )
    return RiskItem(**{**data, **updates})


def metadata_risk(context, **updates):
    amount = context.metadata.get("amount")
    data = dict(
        risk_name="测试金额风险",
        risk_type="METADATA_RISK",
        risk_level="MEDIUM",
        source="rule",
        rule_code="R-AMT-01",
        metadata_keys=["amount"],
        risk_reason="测试字段引用",
        original_text=amount.anchor.matched_text,
        anchor=amount.anchor,
    )
    return RiskItem(**{**data, **updates})


def missing_risk(**updates):
    data = dict(
        risk_name="缺少知识产权条款",
        risk_type="MISSING_CLAUSE",
        clause_type="IP",
        risk_level="HIGH",
        source="rule",
        rule_code="R-MIS-01",
        risk_reason="测试缺项结构",
    )
    return RiskItem(**{**data, **updates})


def test_clause_evidence_and_default_basis(context):
    item = clause_risk(context).validate_against_input(context)
    assert item.legal_basis == "依据：待法务确认"
    assert item.clause_index > 0
    assert "clause_id" not in item.model_dump()


def test_missing_clause_has_no_invented_location(context):
    item = missing_risk().validate_against_input(context)
    assert item.anchor is None and item.original_text is None and item.clause_index is None


def test_observed_metadata_requires_related_evidence(context):
    metadata_risk(context).validate_against_input(context)
    with pytest.raises(ValueError, match="保留证据"):
        metadata_risk(context, anchor=None, original_text=None).validate_against_input(context)


def test_missing_metadata_without_anchor(context):
    item = metadata_risk(context, metadata_keys=["party_b_uscc"], anchor=None, original_text=None)
    item.validate_against_input(context)
    assert item.anchor is None


@pytest.mark.parametrize(
    "changes",
    [
        {"risk_level": "NONE"},
        {"risk_level": "high"},
        {"source": "manual"},
        {"risk_name": "  "},
        {"risk_name": "名" * 129},
        {"risk_reason": "\n"},
        {"legal_basis": " "},
        {"rule_code": None},
        {"rule_code": "R-INVALID"},
        {"clause_index": True},
        {"clause_index": None},
        {"clause_type": None},
        {"original_text": None},
        {"anchor": None},
        {"original_text": "编造的原文"},
        {"metadata_keys": ["amount"]},
        {"source": "llm"},
    ],
)
def test_invalid_clause_shape_rejected(context, changes):
    with pytest.raises(ValidationError):
        clause_risk(context, **changes)


def test_pure_llm_does_not_claim_rule_hit(context):
    item = clause_risk(context, source="llm", rule_code=None)
    item.validate_against_input(context)


def test_merged_source_keeps_rule_code(context):
    clause_risk(context, source="rule+llm").validate_against_input(context)


@pytest.mark.parametrize(
    "changes",
    [
        {"clause_type": None},
        {"clause_type": "OTHER"},
        {"clause_index": 1},
        {"metadata_keys": ["amount"]},
    ],
)
def test_missing_clause_shape_rejected(changes):
    with pytest.raises(ValidationError):
        missing_risk(**changes)


def test_missing_clause_cannot_borrow_a_real_anchor(context):
    observed = clause_risk(context)
    with pytest.raises(ValidationError, match="缺项风险"):
        missing_risk(anchor=observed.anchor, original_text=observed.original_text)


@pytest.mark.parametrize(
    "changes",
    [
        {"metadata_keys": []},
        {"metadata_keys": ["amount", "amount"]},
        {"metadata_keys": ["unknown"]},
        {"clause_index": 1},
    ],
)
def test_metadata_shape_rejected(context, changes):
    with pytest.raises(ValidationError):
        metadata_risk(context, **changes)


def test_metadata_cannot_use_clause_anchor(context):
    observed = clause_risk(context)
    with pytest.raises(ValidationError, match="metadata"):
        metadata_risk(context, anchor=observed.anchor, original_text=observed.original_text)


def test_forged_evidence_rejected_against_document(context):
    item = clause_risk(context)
    forged = item.anchor.model_copy(update={"matched_text": "虚假证据"})
    item = clause_risk(context, original_text="虚假证据", anchor=forged)
    with pytest.raises(ValueError, match="证据文字"):
        item.validate_against_input(context)


@pytest.mark.parametrize("changes", [{"clause_index": 999}, {"clause_type": "PAYMENT"}])
def test_clause_reference_checked_against_input(context, changes):
    with pytest.raises(ValueError, match="条款"):
        clause_risk(context, **changes).validate_against_input(context)


def test_real_text_outside_referenced_clause_rejected(context):
    amount = context.metadata.get("amount")
    anchor = amount.anchor.model_copy(update={"anchor_type": "clause"})
    with pytest.raises(ValueError, match="超出"):
        clause_risk(
            context, anchor=anchor, original_text=anchor.matched_text
        ).validate_against_input(context)


def test_metadata_anchor_cannot_point_to_unrelated_title(context):
    title = context.metadata.get("contract_title")
    with pytest.raises(ValueError, match="无关"):
        metadata_risk(
            context, anchor=title.anchor, original_text=title.anchor.matched_text
        ).validate_against_input(context)


def test_unlisted_metadata_reference_rejected(context):
    context = context.model_copy(update={"metadata": MetadataResult(file_id=12)})
    with pytest.raises(ValueError, match="未包含"):
        metadata_risk_data = RiskItem(
            risk_name="字段缺失",
            risk_type="METADATA_RISK",
            risk_level="HIGH",
            source="rule",
            rule_code="R-AMT-01",
            metadata_keys=["amount"],
            risk_reason="测试",
        )
        metadata_risk_data.validate_against_input(context)


def test_empty_collection_is_not_completion_or_summary(context):
    result = RiskResult(task_id=1, file_id=12, review_version=1)
    result.validate_against_input(context)
    assert result.items == []
    assert "overall_risk_level" not in result.model_dump()


@pytest.mark.parametrize("field,value", [("task_id", 2), ("file_id", 13), ("review_version", 2)])
def test_result_scope_must_match_input(context, field, value):
    data = dict(task_id=1, file_id=12, review_version=1, items=[missing_risk()])
    with pytest.raises(ValueError, match="不一致"):
        RiskResult(**{**data, field: value}).validate_against_input(context)


def test_result_rejects_cross_file_anchor(context):
    with pytest.raises(ValidationError, match="file_id"):
        RiskResult(task_id=1, file_id=13, review_version=1, items=[clause_risk(context)])


def test_repeated_same_rule_and_clause_rejected(context):
    item = clause_risk(context)
    with pytest.raises(ValidationError, match="重复"):
        RiskResult(task_id=1, file_id=12, review_version=1, items=[item, item])


def test_repeated_missing_rule_also_rejected():
    with pytest.raises(ValidationError, match="重复"):
        RiskResult(task_id=1, file_id=12, review_version=1, items=[missing_risk(), missing_risk()])


def test_different_rules_same_clause_are_separate(context):
    result = RiskResult(
        task_id=1,
        file_id=12,
        review_version=1,
        items=[clause_risk(context), clause_risk(context, rule_code="R-CON-02")],
    )
    result.validate_against_input(context)
    assert len(result.items) == 2


def test_input_from_state_ignores_unrelated_state_fields(context):
    state = {**context.model_dump(), "review_degraded": True, "review_summary": "已有说明"}
    parsed = RuleReviewInput.from_state(state)
    assert parsed.file_id == context.file_id
    assert "review_summary" not in parsed.model_dump()


@pytest.mark.parametrize(
    "changes",
    [
        {"file_id": True},
        {"task_id": 0},
        {"review_version": "1"},
        {"file_id": 13},
        {"metadata": MetadataResult.empty(13)},
        {"clauses": []},
    ],
)
def test_invalid_input_rejected(context, changes):
    with pytest.raises(ValidationError):
        RuleReviewInput.model_validate({**context.model_dump(), **changes})


def test_missing_input_field_is_not_treated_as_missing_contract_metadata(context):
    data = context.model_dump()
    data.pop("metadata")
    with pytest.raises(ValidationError, match="metadata"):
        RuleReviewInput.from_state(data)


def test_input_duplicate_clause_rejected(context):
    with pytest.raises(ValidationError, match="重复"):
        RuleReviewInput.model_validate(
            {**context.model_dump(), "clauses": [context.clauses[0], context.clauses[0]]}
        )


def test_long_clause_parent_not_reviewed_twice():
    context = make_input(document("第一条 保密义务", "不得披露商业秘密。" * 400))
    parents = {item.parent_index for item in context.clauses if item.part_no is not None}
    selected = context.review_clauses()
    assert parents
    assert not any(item.index in parents for item in selected)
    assert all(item.part_no is not None for item in selected)


def test_normal_hierarchy_parent_keeps_own_body():
    context = make_input(document("第一条 保密义务", "1.1 不得披露商业秘密。"))
    assert context.review_clauses() == context.clauses


def test_json_roundtrip_preserves_evidence(context):
    result = RiskResult(
        task_id=1,
        file_id=12,
        review_version=1,
        items=[clause_risk(context), metadata_risk(context), missing_risk()],
    )
    restored = RiskResult.model_validate_json(result.model_dump_json())
    assert restored == result
    restored.validate_against_input(context)


def test_truncated_anchor_is_not_contiguous_risk_evidence(context):
    parsed = document("保密事项。" * 120)
    anchor = Anchor.from_document_range(parsed, 0, len(parsed.full_text), anchor_type="clause")
    assert anchor.matched_text_truncated
    with pytest.raises(ValidationError, match="完整原文"):
        clause_risk(context, anchor=anchor, original_text=anchor.matched_text)


def test_input_rejects_orphan_parent(context):
    child = context.clauses[-1].model_copy(update={"parent_index": 999})
    with pytest.raises(ValidationError, match="父条款"):
        RuleReviewInput.model_validate({**context.model_dump(), "clauses": [child]})


def test_input_checks_clause_text_against_original(context):
    clause = context.clauses[0].model_copy(update={"text": "模型编造的正文"})
    with pytest.raises(ValidationError, match="条款正文"):
        RuleReviewInput.model_validate({**context.model_dump(), "clauses": [clause]})
