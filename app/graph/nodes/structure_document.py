"""条款切分与字段提取节点。"""

from app.graph.errors import NodeNotImplementedError
from app.graph.state import ReviewState


def structure_document(state: ReviewState) -> dict:
    """后续接条款切分和合同主体、金额等字段提取。"""
    raise NodeNotImplementedError("structure_document：条款切分与字段提取节点尚未实现")
