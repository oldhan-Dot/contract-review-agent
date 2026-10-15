"""验证真实 LangGraph 的执行顺序、字段传递和失败中止。"""

import pytest

from app.graph.review_graph import ReviewNodes, build_review_graph
from app.parsing.ir import ParsedDocument
from app.review.engine import CATALOG, ReviewUnavailable


def test_nodes_run_in_order_and_receive_prior_updates():
    calls = []

    def step(name):
        def handler(state):
            # 使用已有状态字段检查前一个节点的更新确实传递到下一个节点。
            assert state.get("review_summary", "") == "/".join(calls)
            assert state["task_id"] == 1 and state["file_id"] == 2
            calls.append(name)
            return {"review_summary": "/".join(calls)}

        return handler

    graph = build_review_graph(
        ReviewNodes(
            parse_document=step("parse"),
            structure_document=step("structure"),
            rule_review=step("rules"),
            ai_review=step("ai"),
            save_results=step("save"),
        )
    )
    result = graph.invoke({"task_id": 1, "file_id": 2, "review_version": 1})
    assert calls == ["parse", "structure", "rules", "ai", "save"]
    assert result["review_summary"] == "parse/structure/rules/ai/save"
    assert result["review_version"] == 1


def test_graph_keeps_rule_results_when_ai_is_unavailable(monkeypatch):
    # 默认结构化与规则节点真实执行；模型不可用时不得继续保存完整结果。
    from importlib import import_module

    module = import_module("app.graph.nodes.rule_review")
    monkeypatch.setattr(
        module,
        "load_rules",
        lambda: [{**rule, "enabled": True, "basis": "测试依据", "version": 0} for rule in CATALOG],
    )
    text = "设备采购合同"
    document = ParsedDocument(
        doc_id="file-2",
        file_id=2,
        source_format="DOCX",
        channel="docx",
        full_text=text,
        paragraphs=[dict(pid="file-2:p0", index=0, text=text, char_start=0, char_end=len(text))],
        quality=dict(precision_cap="EXACT", char_count=len(text), paragraph_count=1),
    )

    def unavailable(state):
        assert state["rule_risks"].items
        raise ReviewUnavailable("AI_NOT_CONFIGURED", "测试模型不可用")

    def must_not_save(state):
        pytest.fail("模型不可用时不应保存完整结果")

    graph = build_review_graph(
        ReviewNodes(
            parse_document=lambda state: {"document": document},
            ai_review=unavailable,
            save_results=must_not_save,
        )
    )
    with pytest.raises(ReviewUnavailable, match="测试模型不可用"):
        graph.invoke(
            {
                "task_id": 1,
                "file_id": 2,
                "review_version": 1,
                "contract_type": "PURCHASE",
                "our_party_role": "A",
            }
        )


def test_failed_node_prevents_later_steps():
    calls = []

    def parse(state):
        calls.append("parse")
        return {}

    def structure(state):
        raise ValueError("模拟条款切分失败")

    def later(state):
        calls.append("later")
        return {}

    graph = build_review_graph(ReviewNodes(parse, structure, later, later, later))
    with pytest.raises(ValueError, match="模拟条款切分失败"):
        graph.invoke({"task_id": 1, "file_id": 2, "review_version": 1})
    assert calls == ["parse"]
