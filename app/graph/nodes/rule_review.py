"""确定性规则审查节点。"""

from app.graph.state import ReviewState
from app.review.context import RuleReviewInput
from app.review.engine import evaluate_rules, load_rules


def rule_review(state: ReviewState) -> dict:
    """首批规则返回真实风险和模板建议；快照由执行器在节点边界保存。"""
    context = RuleReviewInput.from_state(state)
    rules = load_rules()
    risks, suggestions = evaluate_rules(context, rules)
    return {
        "rule_risks": risks,
        "suggestions": suggestions,
        "rule_versions": {rule["code"]: rule["version"] for rule in rules},
    }
