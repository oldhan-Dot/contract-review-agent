"""AI 审查与建议生成节点。"""

from app.graph.state import ReviewState
from app.review.context import RuleReviewInput
from app.review.engine import evaluate_model


def ai_review(state: ReviewState) -> dict:
    """调用已配置模型；失败受阻，绝不将模板冒充模型生成结果。"""
    risks, suggestions = evaluate_model(RuleReviewInput.from_state(state))
    return {"ai_risks": risks, "suggestions": {**state.get("suggestions", {}), **suggestions}}
