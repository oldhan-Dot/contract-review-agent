"""节点之间共享的状态，与数据库的任务状态字段分开。"""

from typing import NotRequired, TypedDict

from app.parsing.ir import ParsedDocument


class ReviewState(TypedDict):
    # 三个必填字段定位本次执行，不把数据库 Session 或上传文件句柄放进状态。
    task_id: int
    file_id: int
    review_version: int

    # 节点只需返回自己更新的字段，LangGraph 会合并到共享状态。
    # 解析节点生成统一 IR，后续条款与风险结构随对应模块加入。
    document: NotRequired[ParsedDocument]
    review_summary: NotRequired[str]
    review_degraded: NotRequired[bool]
    review_degraded_reason: NotRequired[str | None]
