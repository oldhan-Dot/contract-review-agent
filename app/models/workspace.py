"""首版工作台的持久快照；保留原有细分表，后续再拆分结果存储。"""

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ReviewSnapshot(TimestampMixin, Base):
    __tablename__ = "review_snapshot"
    __table_args__ = (UniqueConstraint("task_id", "review_version", name="uk_snapshot_version"),)
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("contract_task.id"))
    review_version: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    human_review: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(INTEGER(unsigned=True))


class ReviewRuleSetting(TimestampMixin, Base):
    __tablename__ = "review_rule_setting"
    rule_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=1)
