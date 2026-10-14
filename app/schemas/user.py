"""返回给前端的用户字段白名单。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

UserRole = Literal["LEGAL", "BIZ", "ADMIN"]


class UserOut(BaseModel):
    # 允许从 AppUser 对象读取字段；只输出这里声明的字段。
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role_code: UserRole
    company_name: str | None
    is_active: bool
