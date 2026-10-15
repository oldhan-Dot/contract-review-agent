"""条款切分与字段提取节点。"""

from app.graph.state import ReviewState
from app.parsing.exceptions import ParseBlocked
from app.parsing.ir import ParsedDocument, SourceFormat
from app.structuring.classifier import classify_clauses
from app.structuring.metadata import extract_metadata
from app.structuring.metadata_models import MetadataResult
from app.structuring.models import Clause
from app.structuring.splitter import split_clauses


def structure_document(state: ReviewState) -> dict[str, list[Clause] | MetadataResult]:
    """按切分、归类、字段提取的顺序处理，只返回本节点的增量状态。"""
    # 1. TypedDict 不做运行时校验：确认上游确实提供了当前原件的解析结果。
    document = state.get("document")
    if not isinstance(document, ParsedDocument):
        raise ValueError("structure_document 需要上游提供 ParsedDocument 类型的 document")
    file_id = state.get("file_id")
    if isinstance(file_id, bool) or not isinstance(file_id, int) or not 0 < file_id <= 2**64 - 1:
        raise ValueError("file_id 必须是数据库范围内的正整数")
    if document.file_id != file_id:
        raise ValueError("document 的文件 ID 与本次执行的 file_id 不一致")
    if document.source_format != SourceFormat.DOCX:
        raise ParseBlocked("UNSUPPORTED_FORMAT", "当前结构化节点仅支持 DOCX")
    task_name = state.get("task_name")
    if task_name is not None and not isinstance(task_name, str):
        raise ValueError("task_name 必须是字符串")

    # 2. 切分保留完整原文范围、父子关系和超长条款子段；此时尚未归类。
    clauses = split_clauses(document)

    # 3. 规则归类填入类型、置信度和判定依据；不调用尚未接入的真实模型。
    # 无法判定的条款保留 OTHER 及分类依据，不能当作合同无风险。
    clauses = classify_clauses(clauses)

    # 4. 字段提取读取完整原文，避免条款边界影响主体、金额和日期的证据。
    metadata = extract_metadata(document, task_name=task_name)

    # 5. 只有全部步骤成功后才返回；LangGraph 合并字段并交给 rule_review。
    # 两个字段使用覆盖更新，重跑不会追加重复条款；不修改输入或数据库状态。
    return {"clauses": clauses, "metadata": metadata}
