"""文档解析节点。"""

from app.graph.errors import NodeNotImplementedError
from app.graph.state import ReviewState


def parse_document(state: ReviewState) -> dict:
    """读取 file_id 对应原件，后续接 DOCX/PDF/OCR 解析模块。"""
    raise NodeNotImplementedError("parse_document：文档解析节点尚未实现")
