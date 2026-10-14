"""确定性规则审查节点。"""

from app.graph.errors import NodeNotImplementedError
from app.graph.state import ReviewState


def rule_review(state: ReviewState) -> dict:
    """后续接确定性规则，生成有原文证据的风险项。"""
    raise NodeNotImplementedError("rule_review：规则审查节点尚未实现")
