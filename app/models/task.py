"""合同任务表映射；状态转移规则会在后续状态机模块实现。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import AppUser


class ContractTask(TimestampMixin, Base):
    __tablename__ = "contract_task"
    __table_args__ = (
        UniqueConstraint("task_no", name="uk_task_no"),
        # 同一审批事件只能创建一个任务，后续接入接口以此实现幂等。
        UniqueConstraint("source_event_id", name="uk_source_event"),
        Index("idx_status_risk_created", "status", "overall_risk_level", "created_at"),
        Index("idx_applicant", "applicant_id", "created_at"),
        Index("idx_approval_no", "approval_no"),
        Index("idx_writeback_status", "writeback_status"),
        Index("idx_blocked", "status", "block_reason"),
        CheckConstraint(
            "status IN ('pending','parsing','reviewing','blocked','completed','closed')",
            name="ck_task_status",
        ),
        CheckConstraint(
            "writeback_status IN ('not_written','writing','success','failed')",
            name="ck_task_writeback",
        ),
        CheckConstraint(
            "overall_risk_level IS NULL OR overall_risk_level IN ('HIGH','MEDIUM','LOW')",
            name="ck_task_risk",
        ),
        CheckConstraint(
            "our_party_role IS NULL OR our_party_role IN ('A','B')", name="ck_task_party_role"
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    task_no: Mapped[str] = mapped_column(String(32))
    contract_name: Mapped[str] = mapped_column(String(255))
    contract_type: Mapped[str] = mapped_column(String(16), server_default=text("'UNKNOWN'"))
    contract_no: Mapped[str | None] = mapped_column(String(64))
    applicant_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("app_user.id", name="fk_task_applicant")
    )
    applicant_dept: Mapped[str | None] = mapped_column(String(64))
    # 金额使用 Decimal 对应 DECIMAL(18,2)，避免 float 的二进制舍入误差。
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    our_party_role: Mapped[str | None] = mapped_column(CHAR(1))
    source_type: Mapped[str] = mapped_column(String(16), server_default=text("'UPLOAD'"))
    source_event_id: Mapped[str | None] = mapped_column(String(64))
    approval_no: Mapped[str | None] = mapped_column(String(64))

    # 任务进度与审批回写是两套独立状态，不能用一个字段替代。
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    block_reason: Mapped[str | None] = mapped_column(String(32))
    block_detail: Mapped[str | None] = mapped_column(String(512))
    retry_count: Mapped[int] = mapped_column(TINYINT(unsigned=True), server_default=text("0"))
    overall_risk_level: Mapped[str | None] = mapped_column(String(8))
    review_conclusion: Mapped[str | None] = mapped_column(String(16))
    review_summary: Mapped[str | None] = mapped_column(String(1024))
    review_degraded: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    review_degraded_reason: Mapped[str | None] = mapped_column(String(255))
    review_version: Mapped[int] = mapped_column(INTEGER(unsigned=True), server_default=text("1"))
    writeback_status: Mapped[str] = mapped_column(String(16), server_default=text("'not_written'"))
    writeback_retry_count: Mapped[int] = mapped_column(
        TINYINT(unsigned=True), server_default=text("0")
    )
    parse_started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    review_finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    close_reason: Mapped[str | None] = mapped_column(String(255))
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))

    # 允许通过 task.applicant 读取申请人；暂不建立大量反向任务集合。
    applicant: Mapped[AppUser] = relationship("AppUser")
