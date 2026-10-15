"""报告与回写共用同一份已保存数据，不重新调用模型生成另一套结论。"""

import pymupdf as fitz

from app.services.result_store import result_risks


def markdown_report(task, snapshot):
    payload = snapshot.payload if snapshot else {}
    human = snapshot.human_review if snapshot else {}
    confirmed = bool(snapshot and snapshot.confirmed_revision == snapshot.revision)
    lines = [
        f"# {task.contract_name} 审查报告",
        "",
        f"任务：{task.task_no} / 审查版本：{task.review_version}",
        f"状态：{task.status} / 已识别综合风险：{task.overall_risk_level or '尚未确定'}",
        f"法务确认：{'已确认' if confirmed else '未确认（草稿）'}",
        "",
        task.review_summary or "尚未产生审查结果。",
        "",
    ]
    if not payload.get("complete"):
        lines += ["**部分结果：模型审查或其他关键步骤尚未完成，不能作为完整审查结论。**", ""]
    lines += ["## 合同信息", ""]
    for field in payload.get("metadata", {}).get("items", []):
        lines.append(f"- {field['field_label']}：{field.get('field_value') or '未识别'}")
    lines += ["", "## 风险与建议", ""]
    for number, risk in enumerate(result_risks(payload), 1):
        suggestion = human.get("suggestions", {}).get(
            risk["id"], (risk.get("suggestion") or {}).get("text", "待补充")
        )
        anchor = risk.get("anchor") or {}
        location = (
            f"段落 {(anchor.get('paragraph_index') or 0) + 1}" if anchor else "缺失项，无原文位置"
        )
        lines += [
            f"### {number}. [{risk['risk_level']}] {risk['risk_name']}",
            f"来源：{risk['source']} / {risk.get('rule_code') or '模型研判'} / {location}",
            f"原文：{risk.get('original_text') or '无可定位原文'}",
            f"成因：{risk['risk_reason']}",
            f"依据：{risk.get('legal_basis') or '待法务确认'}",
            f"修改建议：{suggestion}",
            "",
        ]
    lines += ["## 法务意见", "", human.get("comment") or "尚未填写。", "", "## 覆盖与限制", ""]
    lines.extend("- " + note for note in payload.get("coverage_notes", []))
    return "\n".join(lines)


def pdf_report(markdown):
    # 首版使用内置中文字体逐行排版，避免依赖本机字体/Word；后续优化模板。
    document = fitz.open()
    page, y = document.new_page(), 48
    for paragraph in markdown.splitlines():
        line = ""
        wrapped = []
        for char in paragraph.replace("**", "").lstrip("# "):
            if fitz.get_text_length(line + char, fontname="china-s", fontsize=10) > 490:
                wrapped.append(line)
                line = ""
            line += char
        wrapped.append(line)
        for line in wrapped:
            if y > 790:
                page, y = document.new_page(), 48
            page.insert_text((48, y), line, fontsize=10, fontname="china-s")
            y += 16
    content = document.tobytes(garbage=4, deflate=True)
    document.close()
    return content
