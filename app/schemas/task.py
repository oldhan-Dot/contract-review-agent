"""任务创建接口的最小响应。"""

from pydantic import BaseModel


class TaskCreated(BaseModel):
    task_id: int
    task_no: str
    status: str
    duplicated: bool = False
