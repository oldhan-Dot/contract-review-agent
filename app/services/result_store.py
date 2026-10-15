"""按审查版本保存首版工作台快照，支持中途失败后查看已完成的步骤。"""

from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from app.core.db import get_session_factory
from app.models import ContractTask, ReviewSnapshot
from app.review.engine import risk_key


def snapshot_for(db, task, *, lock=False):
    query = select(ReviewSnapshot).where(
        ReviewSnapshot.task_id == task.id, ReviewSnapshot.review_version == task.review_version
    )
    return db.scalar(query.with_for_update() if lock else query)


def state_payload(state):
    payload = {}
    for key in ("document", "metadata", "rule_risks", "ai_risks"):
        if key in state:
            payload[key] = state[key].model_dump(mode="json")
    if "clauses" in state:
        payload["clauses"] = [item.model_dump(mode="json") for item in state["clauses"]]
    payload["suggestions"] = state.get("suggestions", {})
    payload["rule_versions"] = state.get("rule_versions", {})
    payload["coverage_notes"] = [
        "当前仅覆盖首批演示规则，企业信用未实际查询。",
        "风险与修改建议需法务复核，依据待核验项不代表已确认法条。",
    ]
    return payload


def result_risks(payload):
    found = {}
    for key in ("rule_risks", "ai_risks"):
        for item in payload.get(key, {}).get("items", []):
            identity = risk_key(item)
            found[identity] = {
                **item,
                "id": identity,
                "suggestion": payload.get("suggestions", {}).get(identity),
            }
    return list(found.values())


def persist_state(state, stage, *, complete=False):
    if complete and ("rule_risks" not in state or "ai_risks" not in state):
        raise ValueError("关键审查步骤缺失，不能保存为完整结果")
    with get_session_factory()() as db:
        task = db.scalar(
            select(ContractTask).where(ContractTask.id == state["task_id"]).with_for_update()
        )
        if task is None or task.review_version != state["review_version"]:
            raise ValueError("审查版本已改变，拒绝保存旧执行结果")
        if not complete and task.status == "completed":
            return task.review_summary  # 流事件迟到时，不能把最终结果覆盖为部分结果。
        payload = state_payload(state)
        payload.update(stage=stage, complete=complete)
        snapshot = snapshot_for(db, task, lock=True)
        if snapshot is None:
            snapshot = ReviewSnapshot(
                task_id=task.id,
                review_version=task.review_version,
                payload=payload,
                human_review={},
                revision=0,
            )
            db.add(snapshot)
        else:
            snapshot.payload = payload
        risks = result_risks(payload)
        levels = {risk["risk_level"] for risk in risks}
        task.overall_risk_level = next(
            (level for level in ("HIGH", "MEDIUM", "LOW") if level in levels),
            "LOW" if complete else None,
        )
        task.review_summary = f"已识别 {len(risks)} 项待复核风险。" + (
            "审查已完成，请法务复核。" if complete else "当前为部分结果，尚未完成模型审查。"
        )
        metadata = {
            item["field_key"]: item for item in payload.get("metadata", {}).get("items", [])
        }
        title = metadata.get("contract_title", {})
        if task.contract_name == "待识别合同" and title.get("anchor") and title.get("field_value"):
            task.contract_name = title["field_value"][:255]
        amount = metadata.get("amount", {})
        if task.amount is None and amount.get("field_value") and not amount.get("warnings"):
            try:
                task.amount = Decimal(amount["field_value"])
            except InvalidOperation:
                pass
        if not task.currency:
            task.currency = metadata.get("currency", {}).get("field_value")
        if complete:
            task.status, task.block_reason, task.block_detail = "completed", None, None
            task.review_degraded, task.review_degraded_reason = False, None
            task.review_conclusion = "RECTIFY" if risks else "PASS"
            task.review_finished_at = func.now()
        elif stage != "parse_document":
            task.status = "reviewing"
        db.commit()
        return task.review_summary
