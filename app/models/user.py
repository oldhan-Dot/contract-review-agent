"""预置用户表映射；账号注册和密码登录不在本项目范围内。"""

from sqlalchemy import Boolean, CheckConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AppUser(TimestampMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("username", name="uk_username"),
        Index("idx_role", "role_code"),
        CheckConstraint("role_code IN ('LEGAL','BIZ','ADMIN')", name="ck_user_role"),
    )

    # MySQL 的用户主键是无符号 BIGINT；外键必须使用相同类型。
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(64))
    role_code: Mapped[str] = mapped_column(String(16), comment="LEGAL/BIZ/ADMIN")
    company_name: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
