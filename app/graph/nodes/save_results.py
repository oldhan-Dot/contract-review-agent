"""审查结果保存节点。"""

from app.graph.errors import NodeNotImplementedError
from app.graph.state import ReviewState


def save_results(state: ReviewState) -> dict:
    """后续保存风险、建议和摘要，成功提交后才允许完成任务。"""
    raise NodeNotImplementedError("save_results：审查结果保存节点尚未实现")
