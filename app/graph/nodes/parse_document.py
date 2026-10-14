"""文档解析节点。"""

from sqlalchemy import select

from app.core.db import get_session_factory
from app.graph.state import ReviewState
from app.models import ContractFile, ContractTask
from app.parsing.docx_channel import parse_docx
from app.parsing.exceptions import ParseBlocked
from app.parsing.ir import ParsedDocument


def parse_document(state: ReviewState) -> dict[str, ParsedDocument]:
    """查询原件并解析，返回本节点更新的字段；不修改任务状态。"""
    # 1. 校验内部执行标识，再查询当前任务的文件记录。
    # TypedDict 只提供类型提示；后台调用同样需要拒绝非法 ID 和版本。
    for name, maximum in (
        ("task_id", 2**64 - 1),
        ("file_id", 2**64 - 1),
        ("review_version", 2**32 - 1),
    ):
        value = state.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
            raise ValueError(f"{name} 必须是数据库范围内的正整数")

    session_factory = get_session_factory()
    with session_factory() as db:
        record = db.execute(
            select(ContractFile.storage_path, ContractFile.file_ext, ContractTask.review_version)
            .join(ContractTask, ContractFile.task_id == ContractTask.id)
            .where(
                ContractFile.id == state["file_id"],
                ContractTask.id == state["task_id"],
                ContractTask.is_deleted.is_(False),
            )
        ).one_or_none()
        if record is None:
            raise ParseBlocked("ATTACHMENT_MISSING", "文件记录不存在或不属于当前有效任务")
        if record.review_version != state["review_version"]:
            raise ValueError("任务审查版本与执行版本不一致，请使用当前版本")
        storage_path, file_ext = record.storage_path, record.file_ext
    # 数据库会话到此关闭，读取文件期间不持有事务，也不把 Session 放进共享状态。

    # 2. 按格式调用解析器；未来 PDF/OCR 解析通道在这里分流。
    if file_ext.lower() != "docx":
        raise ParseBlocked("UNSUPPORTED_FORMAT", "当前解析节点仅支持 DOCX")
    document = parse_docx(storage_path, file_id=state["file_id"])

    # 3. 返回增量状态；LangGraph 合并后交给 structure_document 节点。
    # 解析失败时直接抛出 ParseBlocked，由未来执行器处理 blocked 和错误展示。
    return {"document": document}
