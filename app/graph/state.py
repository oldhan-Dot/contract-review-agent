"""节点之间共享的状态，与数据库的任务状态字段分开。"""

from typing import NotRequired, TypedDict

from app.parsing.ir import ParsedDocument
from app.review.context import ContractType, OurPartyRole
from app.review.models import RiskResult
from app.structuring.metadata_models import MetadataResult
from app.structuring.models import Clause


class ReviewState(TypedDict):
    # 三个必填字段定位本次执行，不把数据库 Session 或上传文件句柄放进状态。
    task_id: int
    file_id: int
    review_version: int

    # 解析节点从已核验的任务记录载入；入图时可以只有三个执行标识。
    # 规则输入必须有这两个字段，结构化节点原样传递、不重新猜测。
    contract_type: NotRequired[ContractType]
    our_party_role: NotRequired[OurPartyRole]

    # 节点只需返回自己更新的字段，LangGraph 会合并到共享状态。
    # 解析节点提供原文，结构化节点提供条款列表与带证据的字段结果。
    document: NotRequired[ParsedDocument]
    clauses: NotRequired[list[Clause]]
    metadata: NotRequired[MetadataResult]
    rule_risks: NotRequired[RiskResult]
    ai_risks: NotRequired[RiskResult]
    suggestions: NotRequired[dict]
    rule_versions: NotRequired[dict]
    # 可由执行入口显式传入，用于正文无标题时兜底；结构化节点不为此查库。
    task_name: NotRequired[str]
    review_summary: NotRequired[str]
    review_degraded: NotRequired[bool]
    review_degraded_reason: NotRequired[str | None]
