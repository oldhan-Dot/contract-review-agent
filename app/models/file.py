"""合同原件记录，对应现有 contract_file 表。"""

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.mysql import BIGINT, CHAR, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ContractFile(TimestampMixin, Base):
    __tablename__ = "contract_file"
    __table_args__ = (Index("idx_task", "task_id"), Index("idx_md5", "file_md5"))

    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("contract_task.id", name="fk_file_task")
    )
    original_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(512))
    file_size: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    file_md5: Mapped[str] = mapped_column(CHAR(32))
    mime_type: Mapped[str] = mapped_column(String(128))
    file_ext: Mapped[str] = mapped_column(String(16))
    parse_channel: Mapped[str | None] = mapped_column(String(16))
    is_encrypted: Mapped[bool] = mapped_column(server_default=text("0"))
    page_count: Mapped[int | None] = mapped_column(INTEGER)
    file_version: Mapped[int] = mapped_column(INTEGER(unsigned=True), server_default=text("1"))
