"""审查结果保存节点。"""

from app.graph.state import ReviewState
from app.services.result_store import persist_state


def save_results(state: ReviewState) -> dict:
    """仅在模型步骤通过后，提交本次结果并更新 completed。"""
    return {"review_summary": persist_state(state, "save_results", complete=True)}
