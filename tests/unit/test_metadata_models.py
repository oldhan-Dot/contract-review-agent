"""验证字段缺失状态、标准值和证据位置，尚不执行自动提取。"""

import pytest
from pydantic import ValidationError

from app.parsing.ir import ParsedDocument, Precision
from app.structuring.anchor import Anchor
from app.structuring.metadata_models import MetadataField, MetadataKey, MetadataResult


def make_document(texts, file_id=12):
    paragraphs = []
    cursor = 0
    for index, text in enumerate(texts):
        paragraphs.append(
            {
                "pid": f"file-{file_id}:p{index}",
                "index": index,
                "text": text,
                "char_start": cursor,
                "char_end": cursor + len(text),
            }
        )
        cursor += len(text) + 1
    full_text = "\n".join(texts)
    return ParsedDocument(
        doc_id=f"file-{file_id}",
        file_id=file_id,
        source_format="DOCX",
        channel="docx",
        paragraphs=paragraphs,
        full_text=full_text,
        quality={
            "precision_cap": "EXACT",
            "char_count": len(full_text),
            "paragraph_count": len(texts),
        },
    )


@pytest.fixture
def document():
    return make_document(["采购合同", "甲方：示例有限公司", "合同金额：￥1,200.00"])


@pytest.fixture
def amount_anchor(document):
    start = document.full_text.index("￥1,200.00")
    return Anchor.from_document_range(document, start, start + len("￥1,200.00"))


def test_empty_result_has_explicit_missing_fields():
    result = MetadataResult.empty(file_id=12)
    assert len(result.items) == len(MetadataKey) == 15
    assert all(
        item.is_missing and item.field_value is None and item.anchor is None
        for item in result.items
    )
    assert result.get("amount").field_label == "合同金额"
    assert result.get("amount").value_type == "decimal"
    assert result.get("sign_date").value_type == "date"
    assert result.get("not_defined") is None


def test_field_defaults_do_not_mutate_input():
    data = {"field_key": "party_a_name"}
    field = MetadataField.model_validate(data)
    assert field.field_label == "甲方名称"
    assert data == {"field_key": "party_a_name"}


def test_normalized_amount_keeps_original_writing_and_anchor(document, amount_anchor):
    field = MetadataField(
        field_key="amount",
        field_value="1200.00",
        field_value_raw="￥1,200.00",
        is_missing=False,
        confidence=0.9,
        anchor=amount_anchor,
    )
    result = MetadataResult(file_id=12, items=[field])
    assert result.validate_against_document(document) is result
    assert result.get("amount").field_value != result.get("amount").anchor.matched_text
    restored = MetadataResult.model_validate_json(result.model_dump_json())
    assert restored == result
    restored.validate_against_document(document)


@pytest.mark.parametrize(
    "changes",
    [
        {"field_key": "unknown"},
        {"field_value": "1000", "is_missing": True},
        {"confidence": 0.8, "is_missing": True},
        {"is_missing": False, "field_value": None},
        {"is_missing": False, "field_value": "   "},
        {"value_type": "date"},
        {"source": "llm"},
        {"confidence": float("nan")},
        {"unexpected_field": "value"},
    ],
)
def test_invalid_field_state_is_rejected(changes):
    with pytest.raises(ValidationError):
        MetadataField.model_validate({"field_key": "amount", **changes})


def test_missing_field_cannot_have_jump_target(amount_anchor):
    with pytest.raises(ValidationError, match="未识别字段"):
        MetadataField(field_key="amount", anchor=amount_anchor)


def test_manual_value_can_be_kept_without_inventing_evidence():
    field = MetadataField(
        field_key="party_a_name",
        field_value="人工确认的主体名称",
        is_missing=False,
        source="manual",
        confidence=1,
    )
    assert field.anchor is None


def test_invalid_business_candidate_is_not_silently_discarded():
    # 信用代码校验算法下一模块实现；模型允许保留校验失败的值供法务核对。
    field = MetadataField(
        field_key="party_a_uscc",
        field_value="123",
        field_value_raw="123",
        is_missing=False,
        confidence=0.3,
        warnings=["信用代码长度不符合要求"],
    )
    assert field.field_value == "123" and field.warnings


def test_duplicate_metadata_fields_are_rejected():
    field = MetadataField(field_key="amount")
    with pytest.raises(ValidationError, match="重复"):
        MetadataResult(file_id=12, items=[field, field])


def test_anchor_from_another_file_is_rejected(amount_anchor):
    field = MetadataField(
        field_key="amount", field_value="1200.00", is_missing=False, anchor=amount_anchor
    )
    with pytest.raises(ValidationError, match="同一原件"):
        MetadataResult(file_id=13, items=[field])


def test_clause_anchor_cannot_be_used_as_metadata_anchor(amount_anchor):
    other = Anchor.model_validate({**amount_anchor.model_dump(), "anchor_type": "clause"})
    with pytest.raises(ValidationError, match="metadata"):
        MetadataField(field_key="amount", field_value="1200.00", is_missing=False, anchor=other)


def test_result_must_match_document_file(document):
    with pytest.raises(ValueError, match="文件 ID"):
        MetadataResult.empty(file_id=13).validate_against_document(document)


def test_anchor_has_consistent_local_and_global_offsets(document, amount_anchor):
    assert amount_anchor.paragraph_index == 2
    assert amount_anchor.char_start == len("合同金额：")
    assert amount_anchor.char_end == len(document.paragraphs[2].text)
    assert amount_anchor.char_len() == len("￥1,200.00")
    assert amount_anchor.validate_against_document(document) is amount_anchor
    assert amount_anchor.page_no is None and amount_anchor.bbox is None


def test_anchor_supports_multi_paragraph_evidence(document):
    start = document.paragraphs[1].char_start + 3
    end = document.paragraphs[2].char_start + 2
    anchor = Anchor.from_document_range(document, start, end)
    assert (anchor.paragraph_index, anchor.paragraph_index_end) == (1, 2)
    assert (anchor.char_start, anchor.char_end) == (3, 2)
    assert anchor.matched_text == document.full_text[start:end]


def test_long_evidence_keeps_first_and_last_250_characters():
    document = make_document(["甲" * 300 + "乙" * 300])
    anchor = Anchor.from_document_range(document, 0, 600)
    assert anchor.matched_text_truncated is True
    assert anchor.matched_text == "甲" * 250 + "乙" * 250
    assert anchor.validate_against_document(document) is anchor


@pytest.mark.parametrize("start,end", [(-1, 2), (0, 999), (2, 2), (3, 1)])
def test_invalid_document_ranges_are_rejected(document, start, end):
    with pytest.raises(ValueError):
        Anchor.from_document_range(document, start, end)


@pytest.mark.parametrize(
    "changes",
    [
        {"matched_text": "原文不存在的证据"},
        {"file_id": 999},
        {"char_start": 1},
        {"paragraph_index": 1, "paragraph_index_end": 1},
        {"page_no": 1},
        {"bbox": {"x0": 0, "y0": 0, "x1": 10, "y1": 10, "page_width": 100, "page_height": 200}},
    ],
)
def test_anchor_must_be_checked_against_real_document(document, amount_anchor, changes):
    anchor = Anchor.model_validate({**amount_anchor.model_dump(), **changes})
    with pytest.raises(ValueError):
        anchor.validate_against_document(document)


def test_metadata_result_checks_anchor_text(document, amount_anchor):
    wrong = Anchor.model_validate({**amount_anchor.model_dump(), "matched_text": "虚构的证据"})
    field = MetadataField(field_key="amount", field_value="1200.00", is_missing=False, anchor=wrong)
    with pytest.raises(ValueError, match="证据文字"):
        MetadataResult(file_id=12, items=[field]).validate_against_document(document)


def test_exact_precision_cannot_be_claimed_without_local_offsets(amount_anchor):
    with pytest.raises(ValidationError, match="局部字符"):
        Anchor.model_validate({**amount_anchor.model_dump(), "char_start": None, "char_end": None})


def test_anchor_cannot_claim_precision_above_document_cap(document, amount_anchor):
    document.quality.precision_cap = Precision.PARAGRAPH
    with pytest.raises(ValueError, match="精度"):
        amount_anchor.validate_against_document(document)


def test_page_anchor_must_refer_to_known_page(document):
    # 仅验证数据结构，虚拟页码不代表已实现 PDF 读取通道。
    data = document.model_dump()
    data.update(source_format="PDF_TEXT", channel="pdf_text")
    for paragraph in data["paragraphs"]:
        paragraph["page_no"] = 1
    pdf_data = ParsedDocument.model_validate(data)
    anchor = Anchor(
        anchor_type="metadata",
        file_id=12,
        page_no=999,
        global_char_start=0,
        global_char_end=4,
        matched_text="采购合同",
        precision="PAGE",
        match_method="SEARCH",
    )
    with pytest.raises(ValueError, match="页码不存在"):
        anchor.validate_against_document(pdf_data)
