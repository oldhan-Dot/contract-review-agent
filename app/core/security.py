"""演示身份与权限依赖；请求头模拟身份，不代表正式登录认证。"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.models import AppUser, ContractTask
from app.schemas.user import UserRole

MAX_DATABASE_ID = 2**64 - 1


@dataclass(frozen=True)
class IdentityHeaders:
    user_id: int
    role: UserRole


def parse_identity_headers(
    user_id: Annotated[
        int | None,
        Header(
            alias="X-User-Id", gt=0, le=MAX_DATABASE_ID, description="演示用户 ID，必须为正整数"
        ),
    ] = None,
    role: Annotated[
        UserRole | None,
        Header(alias="X-User-Role", description="演示角色：LEGAL、BIZ 或 ADMIN"),
    ] = None,
) -> IdentityHeaders:
    """Header 先校验类型与范围；此函数本身不访问数据库。"""
    # 使用可选参数是为了把缺少身份的情况按 PRD 返回 401，而不是默认 422。
    if user_id is None or role is None:
        raise AppError(401, 40100, "请携带 X-User-Id 和 X-User-Role 身份信息")
    return IdentityHeaders(user_id=user_id, role=role)


def resolve_user(identity: IdentityHeaders, db: Session) -> AppUser:
    """格式通过后，以数据库记录确认用户状态及角色。"""
    user = db.get(AppUser, identity.user_id)
    if user is None or not user.is_active:
        raise AppError(401, 40100, "身份无效或用户已停用")
    # 不直接相信前端声明的角色，防止业务账号自称法务或管理员。
    if user.role_code != identity.role:
        raise AppError(403, 40300, "身份角色与用户记录不一致")
    return user


def get_current_user(
    identity: Annotated[IdentityHeaders, Depends(parse_identity_headers)],
    db: Annotated[Session, Depends(get_db)],
) -> AppUser:
    return resolve_user(identity, db)


def require_roles(*allowed_roles: UserRole):
    """生成角色限制依赖；管理员也必须符合接口声明的角色规则。"""

    def guard(user: Annotated[AppUser, Depends(get_current_user)]) -> AppUser:
        if user.role_code not in allowed_roles:
            raise AppError(403, 40300, "无权限操作该资源")
        return user

    return guard


def require_task_access(
    task_id: Annotated[int, Path(gt=0, le=MAX_DATABASE_ID)],
    identity: Annotated[IdentityHeaders, Depends(parse_identity_headers)],
    db: Annotated[Session, Depends(get_db)],
) -> ContractTask:
    """业务人员仅访问本人任务，法务和管理员可访问全部未删除任务。"""
    # 先完成本依赖的路径和请求头校验，再查询用户，避免非法任务 ID 触发查库。
    user = resolve_user(identity, db)
    statement = select(ContractTask).where(
        ContractTask.id == task_id, ContractTask.is_deleted.is_(False)
    )
    if user.role_code == "BIZ":
        # 所有权过滤在 SQL 层完成，不先读取其他人的完整任务再判断。
        statement = statement.where(ContractTask.applicant_id == user.id)
    task = db.scalar(statement)
    if task is not None:
        return task

    if user.role_code == "BIZ":
        # 只查询 ID，以区分“资源不存在”和 PRD 要求的“越权访问 403”。
        existing_id = db.scalar(
            select(ContractTask.id).where(
                ContractTask.id == task_id, ContractTask.is_deleted.is_(False)
            )
        )
        if existing_id is not None:
            raise AppError(403, 40300, "无权限操作该资源")
    raise AppError(404, 40401, "数据不存在或已删除")
