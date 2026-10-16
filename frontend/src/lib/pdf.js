// 证据文字区间与解析器保存的行框相交；跨页时逐页计算，不拼接多页矩形。
export function anchorRectangles(document, anchor, pageNo) {
  if (!anchor || anchor.file_id !== document.file_id) return []
  return document.paragraphs.flatMap(paragraph => {
    if (paragraph.page_no !== pageNo) return []
    const start = Math.max(anchor.global_char_start, paragraph.char_start)
    const end = Math.min(anchor.global_char_end, paragraph.char_end)
    if (end <= start) return []
    const lines = (paragraph.line_boxes || []).filter(line =>
      paragraph.char_start + line.char_start < end && paragraph.char_start + line.char_end > start)
    return lines.map(line => line.bbox)
  })
}

export function rectangleStyle(box) {
  return {
    left: `${box.x0 / box.page_width * 100}%`, top: `${box.y0 / box.page_height * 100}%`,
    width: `${(box.x1 - box.x0) / box.page_width * 100}%`,
    height: `${(box.y1 - box.y0) / box.page_height * 100}%`,
  }
}
