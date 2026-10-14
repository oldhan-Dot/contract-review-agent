"""条款类型归类：标题优先、关键词加权、父类型继承、可选模型兜底。"""

import json
import re
from collections.abc import Callable, Mapping

from app.structuring.clause_keywords import GENERAL_TITLES, KEYWORDS
from app.structuring.models import Clause, ClauseType

# 外部兜底只需返回 clause_type 和 confidence；本模块不创建模型客户端。
FallbackClassifier = Callable[[Clause], Mapping[str, object]]


def _score(text: str, *, title: bool = False) -> tuple[dict, dict]:
    scores, hits = {}, {}
    identity_declaration = text.strip() in {"甲方", "乙方"} or bool(
        re.search(r"(?:^|\n)\s*(?:甲方|乙方)\s*[:：]", text)
    )
    for clause_type, groups in KEYWORDS.items():
        matches = {}
        for weight, words in zip((3, 2, 1), groups):
            for word in words:
                # 日常条款反复提到甲乙方，不等于在定义主体；正文中需有主体声明线索。
                if word in ("甲方", "乙方") and not identity_declaration:
                    continue
                if word in text:
                    matches[word] = weight * (2 if title else 1)
        if matches:
            scores[clause_type.value] = sum(matches.values())
            hits[clause_type.value] = matches
    return scores, hits


def _rank(scores: dict) -> list[tuple[str, int]]:
    # 同分只稳定排序用于展示；判定时仍要求第一名得分严格高于第二名。
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _classified(
    clause: Clause,
    clause_type: ClauseType | str,
    confidence: float,
    *,
    method: str,
    scores: dict,
    hits: dict,
    secondary: str | None = None,
    needs_fallback: bool = False,
    **details,
) -> Clause:
    evidence = {
        "method": method,
        "scores": scores,
        "hits": hits,
        "needs_fallback": needs_fallback,
        **details,
    }
    # 重建并验证模型，不直接修改输入，也不让外部回调绕过类型/置信度校验。
    return Clause.model_validate(
        {
            **clause.model_dump(),
            "clause_type": clause_type,
            "clause_type_secondary": secondary,
            "type_confidence": confidence,
            "classify_rule_hit": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        }
    )


def _parent_score(parent: Clause) -> int:
    try:
        evidence = json.loads(parent.classify_rule_hit or "{}")
        score = evidence.get(
            "inherited_score", evidence.get("scores", {}).get(parent.clause_type.value, 3)
        )
        return score if isinstance(score, int) and not isinstance(score, bool) and score >= 0 else 3
    except (ValueError, AttributeError, TypeError):
        return 3


def _is_general_parent(parent: Clause) -> bool:
    try:
        return json.loads(parent.classify_rule_hit or "{}").get("method") in {
            "general_title",
            "general_parent",
        }
    except (ValueError, AttributeError, TypeError):
        return False


def classify_clause(
    clause: Clause, *, parent: Clause | None = None, fallback: FallbackClassifier | None = None
) -> Clause:
    """归类单条条款；返回带类型及判定依据的新 Clause。"""
    # 1. 明确的通用标题归 OTHER；标题关键词有唯一最高分时优先定案。
    title = re.sub(r"\s+", "", clause.title or "").rstrip("：:")
    # 定义章节的子条款即使出现付款等术语，也应保留通用章节属性。
    if parent is not None and parent.clause_type == ClauseType.OTHER and _is_general_parent(parent):
        return _classified(
            clause,
            ClauseType.OTHER,
            parent.type_confidence,
            method="general_parent",
            scores={},
            hits={},
            parent_index=parent.index,
        )
    if title in GENERAL_TITLES:
        return _classified(
            clause, ClauseType.OTHER, 0.95, method="general_title", scores={}, hits={}
        )
    if title in {"主体信息", "合同主体", "签约主体信息"}:
        return _classified(
            clause,
            ClauseType.PARTY_INFO,
            0.95,
            method="title",
            scores={"PARTY_INFO": 6},
            hits={"PARTY_INFO": {title: 6}},
        )
    title_scores, title_hits = _score(title, title=True)
    ranked = _rank(title_scores)
    if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return _classified(
            clause, ranked[0][0], 0.95, method="title", scores=title_scores, hits=title_hits
        )

    # 2. 正文按主/次/弱词 3/2/1 分计数；并列和低分不能靠排序强行决定。
    scores, hits = _score(clause.text)
    ranked = _rank(scores)
    decisive = ranked and ranked[0][1] >= 3 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1])

    # 3. 子条款默认继承可信父类型，自己的唯一最高分更高时才覆盖。
    if parent is not None and parent.clause_type != ClauseType.OTHER and parent.type_confidence > 0:
        inherited_score = _parent_score(parent)
        if not decisive or ranked[0][1] <= inherited_score:
            return _classified(
                clause,
                parent.clause_type,
                parent.type_confidence,
                method="parent",
                scores=scores,
                hits=hits,
                parent_index=parent.index,
                inherited_score=inherited_score,
            )
    if decisive:
        top_type, top_score = ranked[0]
        secondary = ranked[1][0] if len(ranked) > 1 and top_score - ranked[1][1] <= 1 else None
        return _classified(
            clause,
            top_type,
            min(0.5 + top_score * 0.1, 0.95),
            method="keywords",
            scores=scores,
            hits=hits,
            secondary=secondary,
        )

    # 4. 规则不足时才调用外部兜底；服务未接入/失败时 OTHER，并记录覆盖缺口。
    reason = "no_keywords" if not ranked else "low_score" if ranked[0][1] < 3 else "tied_scores"
    if fallback is not None:
        try:
            answer = fallback(clause.model_copy(deep=True))
            if set(answer) != {"clause_type", "confidence"}:
                raise ValueError("兜底输出字段不符合约定")
            if isinstance(answer["confidence"], bool) or not isinstance(
                answer["confidence"], (int, float)
            ):
                raise ValueError("兜底置信度必须为数值")
            return _classified(
                clause,
                answer["clause_type"],
                answer["confidence"],
                method="fallback",
                scores=scores,
                hits=hits,
                reason=reason,
            )
        except Exception as exc:
            # 不把服务异常原文放进证据，避免其中包含密钥或合同正文。
            return _classified(
                clause,
                ClauseType.OTHER,
                0,
                method="unresolved",
                scores=scores,
                hits=hits,
                needs_fallback=True,
                reason=reason,
                fallback_error=type(exc).__name__,
            )
    return _classified(
        clause,
        ClauseType.OTHER,
        0,
        method="unresolved",
        scores=scores,
        hits=hits,
        needs_fallback=True,
        reason=reason,
    )


def classify_clauses(
    clauses: list[Clause], *, fallback: FallbackClassifier | None = None
) -> list[Clause]:
    """按父记录在前的顺序批量归类，保留原文区间和全部其他字段。"""
    result, classified = [], {}
    for clause in clauses:
        if clause.index in classified:
            raise ValueError("条款顺序号不能重复")
        parent = classified.get(clause.parent_index)
        if clause.parent_index is not None and parent is None:
            raise ValueError("归类时父条款必须存在且先于子条款")
        current = classify_clause(clause, parent=parent, fallback=fallback)
        result.append(current)
        classified[current.index] = current
    return result
