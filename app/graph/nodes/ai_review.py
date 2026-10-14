"""AI 审查与建议生成节点。"""

from app.graph.errors import NodeNotImplementedError
from app.graph.state import ReviewState


def ai_review(state: ReviewState) -> dict:
    """后续接模型研判与修改建议；服务异常的降级策略随该模块实现。"""
    raise NodeNotImplementedError("ai_review：AI 审查节点尚未实现")
