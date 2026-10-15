"""单执行槽后台处理；数据库 pending 为持久待办来源，避免仅存内存。"""

import threading
from pathlib import Path

from loguru import logger
from sqlalchemy import func, select, text, update

from app.core.config import ROOT
from app.core.db import get_engine, get_session_factory
from app.models import ContractFile, ContractTask
from app.services.result_store import persist_state


def prepare_workspace_schema():
    # 仅执行新增表脚本，绝不重新初始化或清空已有业务表。
    script = (ROOT / "sql/ddl/002_workspace.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
    with get_engine().begin() as db:
        for statement in sql.split(";"):
            if statement.strip():
                db.exec_driver_sql(statement)


class ReviewRunner:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.running = False

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._serve, name="review-runner", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _serve(self):
        # MySQL 命名锁保证同一数据库只运行一个领取器；重载/双进程不会互相恢复任务。
        try:
            with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as leader:
                name = "cra_workspace_" + str(get_engine().url.database)
                if not leader.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": name[:64]}):
                    return
                try:
                    self.running = True
                    with get_session_factory()() as db:
                        db.execute(
                            update(ContractTask)
                            .where(ContractTask.status.in_(["parsing", "reviewing"]))
                            .values(
                                status="blocked",
                                block_reason="INTERRUPTED",
                                block_detail="上次处理已中断，可查看部分结果并由管理员重试。",
                            )
                        )
                        db.commit()
                    while not self.stop_event.is_set():
                        leader.execute(text("SELECT 1"))
                        state = self._claim()
                        if state:
                            self._run(state)
                        else:
                            self.stop_event.wait(1)
                finally:
                    self.running = False
                    leader.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name[:64]})
        except Exception as exc:
            self.running = False
            logger.error("后台执行器停止：{}，请检查数据库并重启后端", type(exc).__name__)

    def _claim(self):
        with get_session_factory()() as db:
            task = db.scalar(
                select(ContractTask)
                .where(ContractTask.status == "pending", ContractTask.is_deleted.is_(False))
                .order_by(ContractTask.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return None
            file = db.scalar(
                select(ContractFile)
                .where(ContractFile.task_id == task.id)
                .order_by(ContractFile.file_version.desc(), ContractFile.id.desc())
                .limit(1)
            )
            if file is None or not Path(file.storage_path).is_file():
                task.status, task.block_reason, task.block_detail = (
                    "blocked",
                    "ATTACHMENT_MISSING",
                    "未找到合同原件。",
                )
                db.commit()
                return None
            task.status, task.parse_started_at = "parsing", func.now()
            task.block_reason = task.block_detail = None
            state = dict(task_id=task.id, file_id=file.id, review_version=task.review_version)
            if task.contract_name != "待识别合同":
                state["task_name"] = task.contract_name
            db.commit()
            return state

    def _run(self, state):
        from app.graph.review_graph import build_review_graph

        try:
            for update in build_review_graph().stream(state, stream_mode="updates"):
                for name, fields in update.items():
                    state.update(fields)
                    if name != "save_results":
                        persist_state(state, name)
        except Exception as exc:
            reason = getattr(exc, "reason", "PROCESSING_FAILED")
            detail = getattr(exc, "detail", "处理失败，请检查送审信息与文件后重试。")
            with get_session_factory()() as db:
                task = db.scalar(
                    select(ContractTask)
                    .where(ContractTask.id == state["task_id"])
                    .with_for_update()
                )
                if task and task.review_version == state["review_version"]:
                    task.status, task.block_reason, task.block_detail = (
                        "blocked",
                        reason[:32],
                        detail[:512],
                    )
                    task.review_degraded, task.review_degraded_reason = True, detail[:255]
                    db.commit()
            logger.warning("任务 {} 受阻：{}", state["task_id"], reason)


runner = ReviewRunner()
