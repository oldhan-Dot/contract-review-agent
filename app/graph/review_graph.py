"""主流程定义：先搭顺序节点，业务分支随真实模块逐步加入。"""

from collections.abc import Callable
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph import nodes as business_nodes
from app.graph.state import ReviewState

NodeFunction = Callable[[ReviewState], dict]


@dataclass(frozen=True)
class ReviewNodes:
    """允许测试替换处理函数，生产默认使用对应业务节点。"""

    parse_document: NodeFunction = business_nodes.parse_document
    structure_document: NodeFunction = business_nodes.structure_document
    rule_review: NodeFunction = business_nodes.rule_review
    ai_review: NodeFunction = business_nodes.ai_review
    save_results: NodeFunction = business_nodes.save_results


def build_review_graph(
    nodes: ReviewNodes | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """编译图但不执行；检查点实现由外部传入，本模块不创建数据库连接。"""
    handlers = nodes or ReviewNodes()
    graph = StateGraph(ReviewState)

    # 注册节点名与处理函数。函数负责业务逻辑，图负责执行顺序与共享状态。
    graph.add_node("parse_document", handlers.parse_document)
    graph.add_node("structure_document", handlers.structure_document)
    graph.add_node("rule_review", handlers.rule_review)
    graph.add_node("ai_review", handlers.ai_review)
    graph.add_node("save_results", handlers.save_results)

    # START / END 是 LangGraph 的流程标记，不等同于数据库 pending / completed。
    graph.add_edge(START, "parse_document")
    graph.add_edge("parse_document", "structure_document")
    graph.add_edge("structure_document", "rule_review")
    graph.add_edge("rule_review", "ai_review")
    graph.add_edge("ai_review", "save_results")
    graph.add_edge("save_results", END)

    # 默认不持久化检查点；传入 Saver 后仍需执行端配置 thread_id。
    return graph.compile(checkpointer=checkpointer)
