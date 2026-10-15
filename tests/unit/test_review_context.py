"""送审上下文从任务记录到图和规则输入的传递，无需 MySQL。"""

from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.graph.nodes.structure_document import structure_document
from app.graph.review_graph import ReviewNodes, build_review_graph
from app.parsing.ir import ParsedDocument
from app.review.context import RuleReviewInput

parse_node = import_module("app.graph.nodes.parse_document")


def document():
    text = "设备采购合同"
    return ParsedDocument(
        doc_id="file-12",
        file_id=12,
        source_format="DOCX",
        channel="docx",
        full_text=text,
        paragraphs=[dict(pid="file-12:p0", index=0, text=text, char_start=0, char_end=len(text))],
        quality=dict(precision_cap="EXACT", char_count=len(text), paragraph_count=1),
    )


def execution_state(**changes):
    return {"task_id": 1, "file_id": 12, "review_version": 1, **changes}


@pytest.fixture
def task_source(monkeypatch):
    record = SimpleNamespace(
        storage_path="not-read.docx",
        file_ext="docx",
        review_version=1,
        contract_type="PURCHASE",
        our_party_role="B",
    )
    calls = []

    @contextmanager
    def session():
        calls.append("open")
        try:
            yield SimpleNamespace(
                execute=lambda statement: SimpleNamespace(one_or_none=lambda: record)
            )
        finally:
            calls.append("close")

    def parse(path, *, file_id):
        # 真正进入文件读取前，任务查询事务必须已经关闭。
        assert calls[-1] == "close"
        assert path == record.storage_path and file_id == 12
        calls.append("parse")
        return document()

    monkeypatch.setattr(parse_node, "get_session_factory", lambda: session)
    monkeypatch.setattr(parse_node, "parse_docx", parse)
    return record, calls


def test_record_context_overrides_untrusted_initial_state(task_source):
    _, calls = task_source
    initial = execution_state(contract_type="LABOR", our_party_role="A")
    result = parse_node.parse_document(initial)
    assert result["contract_type"] == "PURCHASE"
    assert result["our_party_role"] == "B"
    assert set(result) == {"document", "contract_type", "our_party_role"}
    assert initial["contract_type"] == "LABOR" and initial["our_party_role"] == "A"
    assert calls == ["open", "close", "parse"]


@pytest.mark.parametrize("contract_type", ["PURCHASE", "SALES", "SERVICE", "LABOR"])
@pytest.mark.parametrize("role", ["A", "B"])
def test_supported_context_reaches_rule_input(task_source, contract_type, role):
    record, _ = task_source
    record.contract_type, record.our_party_role = contract_type, role
    state = execution_state()
    state.update(parse_node.parse_document(state))
    state.update(structure_document(state))
    context = RuleReviewInput.from_state(state)
    assert context.contract_type == contract_type
    assert context.our_party_role == role
    assert context.metadata.file_id == state["file_id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_type", "UNKNOWN"),
        ("contract_type", None),
        ("contract_type", "purchase"),
        ("contract_type", ""),
        ("contract_type", True),
        ("our_party_role", None),
        ("our_party_role", ""),
        ("our_party_role", "甲方"),
        ("our_party_role", "a"),
        ("our_party_role", True),
    ],
)
def test_invalid_stored_context_stops_before_reading_file(task_source, field, value):
    record, calls = task_source
    setattr(record, field, value)
    with pytest.raises(ValueError, match=field):
        parse_node.parse_document(execution_state())
    assert calls == ["open", "close"]


def test_stale_version_still_stops_before_file_read(task_source):
    record, calls = task_source
    record.review_version = 2
    with pytest.raises(ValueError, match="版本"):
        parse_node.parse_document(execution_state())
    assert calls == ["open", "close"]


def rule_state():
    state = execution_state(document=document(), contract_type="PURCHASE", our_party_role="B")
    state.update(structure_document(state))
    return state


@pytest.mark.parametrize("missing", ["contract_type", "our_party_role"])
def test_missing_rule_context_is_not_defaulted(missing):
    state = rule_state()
    state.pop(missing)
    with pytest.raises(ValidationError, match=missing):
        RuleReviewInput.from_state(state)


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_type", "UNKNOWN"),
        ("contract_type", None),
        ("contract_type", "purchase"),
        ("contract_type", True),
        ("our_party_role", None),
        ("our_party_role", "a"),
        ("our_party_role", "甲方"),
        ("our_party_role", 1),
    ],
)
def test_direct_rule_input_rejects_invalid_context(field, value):
    with pytest.raises(ValidationError, match=field):
        RuleReviewInput.from_state({**rule_state(), field: value})


def test_rule_context_json_roundtrip_and_no_input_mutation():
    state = rule_state()
    context = RuleReviewInput.from_state(state)
    assert RuleReviewInput.model_validate_json(context.model_dump_json()) == context
    assert state["contract_type"] == "PURCHASE" and state["our_party_role"] == "B"
    assert state["metadata"].get("amount").is_missing


def test_real_graph_preserves_context_until_rule_boundary(task_source):
    captured = []

    def rules(current):
        context = RuleReviewInput.from_state(current)
        captured.append((context.contract_type, context.our_party_role))
        return {"review_summary": "规则输入收到任务上下文"}

    graph = build_review_graph(
        ReviewNodes(rule_review=rules, ai_review=lambda state: {}, save_results=lambda state: {})
    )
    result = graph.invoke(execution_state(contract_type="LABOR", our_party_role="A"))
    assert captured == [("PURCHASE", "B")]
    assert result["review_summary"] == "规则输入收到任务上下文"
    assert result["our_party_role"] == "B"
