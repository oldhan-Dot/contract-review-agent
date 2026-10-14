"""按编号或标题切分条款；保持原文区间，超长内容再按句子和长度细分。"""

import re
from bisect import bisect_right

from app.parsing.exceptions import ParseBlocked
from app.parsing.ir import ParagraphNode, ParsedDocument
from app.structuring.models import Clause, SplitStrategy

# 更具体的编号优先，否则「第三条之一」会先被识别成「第三条」。
_CN = "一二三四五六七八九十百零〇两"
CLAUSE_NO_PATTERNS = [
    (re.compile(rf"^第\s*[{_CN}]{{1,6}}\s*条之\s*[{_CN}]+"), 1, "cn_article_sub"),
    (re.compile(rf"^第\s*[{_CN}]{{1,6}}\s*条"), 1, "cn_article"),
    (re.compile(r"^第\s*\d{1,3}\s*条"), 1, "ar_article"),
    (re.compile(rf"^[{_CN}]{{1,3}}\s*[、．.]"), 1, "cn_dun"),
    (re.compile(r"^\d{1,2}(?:\.\d{1,2}){2}(?=\s|[、．.])"), 2, "dot3"),
    # PRD FR-204 将 1.1 / 1.2 定为一级，采用该验收口径。
    (re.compile(r"^\d{1,2}\.\d{1,2}(?=\s|[、．.])"), 1, "dot2"),
    (re.compile(r"^\d{1,2}\s*[、．.)）](?!\d)"), 1, "dot1"),
    (re.compile(rf"^[（(]\s*[{_CN}]{{1,3}}\s*[）)]"), 2, "cn_paren"),
    (re.compile(r"^[（(]\s*\d{1,2}\s*[）)]"), 3, "ar_paren"),
    (re.compile(rf"^【\s*第?\s*[{_CN}\d]{{1,6}}\s*条?\s*】"), 1, "bracket"),
]
MAX_NO_PREFIX_LEN = 12


def match_clause_no(text: str) -> tuple[int, str, str] | None:
    """返回层级、原文编号和识别方式；允许编号单独一段，正文在下一段。"""
    head = text.lstrip()
    for pattern, level, tag in CLAUSE_NO_PATTERNS:
        match = pattern.match(head)
        if match and match.end() <= MAX_NO_PREFIX_LEN:
            # 小数后直接跟货币/计量单位通常是金额或数量，不作为条款边界。
            remainder = head[match.end() :].lstrip()
            if tag.startswith("dot") and re.match(
                r"(?:万元|亿元|元|%|％|公斤|千克)(?:\s|$)", remainder
            ):
                continue
            return level, match.group(), tag
    return None


def _is_pseudo_heading(paragraph: ParagraphNode) -> bool:
    text = paragraph.text.strip()
    if not 2 <= len(text) <= 30 or text.endswith(("。", "；", "，", ".", ";", ",", "！", "？")):
        return False
    visible_runs = [run for run in paragraph.runs if run.text.strip()]
    return (
        text.endswith(("：", ":")) or bool(visible_runs) and all(run.bold for run in visible_runs)
    )


def _choose_strategy(paragraphs: list[ParagraphNode]) -> SplitStrategy:
    # 同一文档按优先级选一个主策略，避免在不同位置任意改变切分粒度。
    if any(match_clause_no(p.text) for p in paragraphs):
        return "NUMBER"
    if any(p.heading_level is not None for p in paragraphs):
        return "STYLE"
    if any(_is_pseudo_heading(p) for p in paragraphs):
        return "PSEUDO_HEADING"
    return "BLANK_LINE"


def _is_boundary(paragraph: ParagraphNode, strategy: SplitStrategy) -> bool:
    if strategy == "NUMBER":
        return match_clause_no(paragraph.text) is not None
    if strategy == "STYLE":
        return paragraph.heading_level is not None
    if strategy == "PSEUDO_HEADING":
        return _is_pseudo_heading(paragraph)
    return True  # 没有结构线索时每个自然段独立成条款。


def _build_clause(
    document: ParsedDocument, group: list[ParagraphNode], index: int, strategy: SplitStrategy
) -> Clause:
    first, last = group[0], group[-1]
    number = match_clause_no(first.text) if strategy == "NUMBER" else None
    raw = document.slice_text(first.char_start, last.char_end).strip()
    prefix = number[1] if number else None
    text = raw[len(prefix) :].strip() if prefix else raw
    # 纯编号且没有正文时保留原文标签，不能静默丢掉合同里的文字。
    if not text:
        prefix, text = None, raw
    level = number[0] if number else first.heading_level if strategy == "STYLE" else 0
    heading = first.text.strip()
    if prefix:
        heading = heading[len(prefix) :].strip()
    title = (
        heading if len(heading) <= 80 and not heading.endswith(("。", "；", "！", "？")) else None
    )
    return Clause(
        index=index,
        clause_no=prefix,
        level=level or 0,
        title=title,
        text=text,
        paragraph_index_start=first.index,
        paragraph_index_end=last.index,
        char_start=first.char_start,
        char_end=last.char_end,
        page_start=first.page_no,
        page_end=last.page_no,
        split_strategy=strategy,
    )


def _child_ranges(document: ParsedDocument, parent: Clause, target_len: int):
    """尽量在句末或自然段末切分；无标点的长句按长度切，始终使用原文偏移。"""
    raw = document.full_text
    breaks = [
        parent.char_start + m.end()
        for m in re.finditer(r"[。；！？!?;\n]", parent.source_text(document))
    ]
    cursor = parent.char_start
    while cursor < parent.char_end:
        limit = min(cursor + target_len, parent.char_end)
        position = bisect_right(breaks, limit) - 1
        end = (
            breaks[position]
            if limit < parent.char_end and position >= 0 and breaks[position] > cursor
            else limit
        )
        start, trimmed_end = cursor, end
        # 分段边界的空白可排除；它们仍完整保留在父条款和 full_text 中。
        while start < trimmed_end and raw[start].isspace():
            start += 1
        while trimmed_end > start and raw[trimmed_end - 1].isspace():
            trimmed_end -= 1
        if start < trimmed_end:
            yield start, trimmed_end
        cursor = end


def _append_children(
    document: ParsedDocument, clauses: list[Clause], parent: Clause, target_len: int
):
    starts = [p.char_start for p in document.paragraphs]
    for part_no, (start, end) in enumerate(_child_ranges(document, parent, target_len), start=1):
        first = document.paragraphs[bisect_right(starts, start) - 1]
        last = document.paragraphs[bisect_right(starts, end - 1) - 1]
        raw = document.slice_text(start, end).strip()
        prefix = (
            parent.clause_no
            if part_no == 1 and parent.clause_no and raw.startswith(parent.clause_no)
            else None
        )
        text = raw[len(prefix) :].strip() if prefix else raw
        if not text:
            prefix, text = None, raw
        clauses.append(
            Clause(
                index=len(clauses) + 1,
                clause_no=prefix,
                level=0,
                title=parent.title,
                text=text,
                paragraph_index_start=first.index,
                paragraph_index_end=last.index,
                char_start=start,
                char_end=end,
                page_start=first.page_no,
                page_end=last.page_no,
                split_strategy="LENGTH_FALLBACK",
                parent_index=parent.index,
                part_no=part_no,
            )
        )


def _validate_result(document: ParsedDocument, clauses: list[Clause]) -> None:
    """逐条核对原文，再验证编号、父记录和整份文档的正文覆盖。"""
    cursor = 0
    by_index = {}
    child_progress = {}
    for expected_index, clause in enumerate(clauses, start=1):
        if clause.index != expected_index:
            raise ValueError("条款顺序号必须从 1 开始且连续")
        clause.validate_against_document(document)
        if clause.parent_index is not None and clause.parent_index not in by_index:
            raise ValueError("条款父记录不存在或未出现在子条款之前")
        if clause.part_no is not None:
            parent = by_index[clause.parent_index]
            if not parent.char_start <= clause.char_start < clause.char_end <= parent.char_end:
                raise ValueError("拆分子条款越出父条款原文范围")
            child_cursor, next_part = child_progress.get(parent.index, (parent.char_start, 1))
            if clause.part_no != next_part or clause.char_start < child_cursor:
                raise ValueError("子条款序号不连续或原文区间重叠")
            if document.slice_text(child_cursor, clause.char_start).strip():
                raise ValueError("子条款切分遗漏父条款正文")
            child_progress[parent.index] = (clause.char_end, next_part + 1)
        else:
            if clause.char_start < cursor or document.slice_text(cursor, clause.char_start).strip():
                raise ValueError("主条款区间重叠或遗漏正文")
            cursor = clause.char_end
        by_index[clause.index] = clause
    if document.slice_text(cursor, len(document.full_text)).strip():
        raise ValueError("条款切分遗漏正文末尾")
    for parent_index, (child_cursor, _) in child_progress.items():
        if document.slice_text(child_cursor, by_index[parent_index].char_end).strip():
            raise ValueError("子条款切分遗漏父条款末尾")


def split_clauses(
    document: ParsedDocument, *, max_len: int = 3000, target_len: int = 800
) -> list[Clause]:
    """切分入口；返回条款列表，分类保持 OTHER，不调用数据库或模型服务。"""
    if (
        any(isinstance(n, bool) or not isinstance(n, int) for n in (max_len, target_len))
        or not 0 < target_len <= max_len
    ):
        raise ValueError("切分长度必须是正整数，target_len 不能大于 max_len")
    if not document.paragraphs or not document.full_text.strip():
        raise ParseBlocked("EMPTY_BODY", "没有可切分的合同正文")

    # 1. 选择编号、标题样式、伪标题或自然段策略，并确定边界。
    strategy = _choose_strategy(document.paragraphs)
    boundaries = [0] + [p.index for p in document.paragraphs[1:] if _is_boundary(p, strategy)]
    boundaries.append(len(document.paragraphs))
    clauses = []
    hierarchy = []
    # 2. 按段落组生成条款，编号仍覆盖完整原文，建立层级父记录。
    for start, end in zip(boundaries, boundaries[1:]):
        clause = _build_clause(document, document.paragraphs[start:end], len(clauses) + 1, strategy)
        if clause.level > 0:
            while hierarchy and hierarchy[-1].level >= clause.level:
                hierarchy.pop()
            if hierarchy:
                clause.parent_index = hierarchy[-1].index
            hierarchy.append(clause)
        clauses.append(clause)

        # 3. 超长条款保留父记录用于追溯，审查时使用有 part_no 的子条款。
        if len(clause.text) > max_len:
            _append_children(document, clauses, clause, target_len)

    # 4. 返回前确保条款区间与原文一致，父记录存在，正文未被静默丢弃。
    _validate_result(document, clauses)
    return clauses
