import { test } from 'node:test'
import assert from 'node:assert/strict'
import { anchorRectangles, rectangleStyle } from '../../frontend/src/lib/pdf.js'

const box = { x0: 10, y0: 20, x1: 30, y1: 40, page_width: 100, page_height: 200 }
const document = { file_id: 4, paragraphs: [
  { page_no: 1, char_start: 0, char_end: 10, line_boxes: [
    { char_start: 0, char_end: 4, bbox: box }, { char_start: 5, char_end: 10, bbox: { ...box, y0: 50, y1: 60 } },
  ] },
  { page_no: 2, char_start: 11, char_end: 20, line_boxes: [{ char_start: 0, char_end: 9, bbox: box }] },
] }

test('cross-page evidence is split into the correct page and intersecting lines', () => {
  const anchor = { file_id: 4, global_char_start: 6, global_char_end: 14 }
  assert.equal(anchorRectangles(document, anchor, 1).length, 1)
  assert.equal(anchorRectangles(document, anchor, 1)[0].y0, 50)
  assert.equal(anchorRectangles(document, anchor, 2).length, 1)
})
test('missing evidence and other-file anchors do not create misleading overlays', () => {
  assert.deepEqual(anchorRectangles(document, null, 1), [])
  assert.deepEqual(anchorRectangles(document, { file_id: 99, global_char_start: 0, global_char_end: 4 }, 1), [])
})
test('page-relative positions stay aligned at any display zoom', () => {
  assert.deepEqual(rectangleStyle(box), { left: '10%', top: '10%', width: '20%', height: '10%' })
})
