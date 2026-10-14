"""验证真实 LangGraph 的执行顺序、字段传递和失败中止。"""

import pytest

from app.graph.nodes import NodeNotImplementedError
from app.graph.review_graph import ReviewNodes, build_review_graph


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


def test_graph_stops_at_next_unimplemented_node():
    # 解析节点已有真实实现；本测试替换解析，确认尚未实现的结构化节点明确中止。
    graph = build_review_graph(ReviewNodes(parse_document=lambda state: {}))
    with pytest.raises(NodeNotImplementedError, match="structure_document"):
        graph.invoke({"task_id": 1, "file_id": 2, "review_version": 1})


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
