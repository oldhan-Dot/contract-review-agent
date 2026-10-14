"""所有 ORM 模型共用的基类和时间戳字段。"""

from datetime import datetime

from sqlalchemy import FetchedValue, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """模型注册入口；本模块不调用 create_all，建表仍由 SQL 脚本负责。"""


class TimestampMixin:
    """时间由 MySQL 生成，与现有 DDL 的毫秒精度和更新行为一致。"""

    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), server_default=text("CURRENT_TIMESTAMP(3)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        # 告知 ORM 此字段会在数据库更新时变化，刷新对象时重新读取。
        server_onupdate=FetchedValue(),
    )
