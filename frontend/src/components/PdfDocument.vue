<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { request } from '../lib/api'
import { anchorRectangles, rectangleStyle } from '../lib/pdf'

const props = defineProps({
  taskId: { type: [String, Number], required: true },
  reviewVersion: { type: Number, required: true },
  document: { type: Object, required: true },
  risks: { type: Array, default: () => [] },
  selectedRiskId: { type: String, default: null },
  field: { type: Object, default: null },
})
const emit = defineEmits(['select-risk'])
const pageNo = ref(1), zoom = ref(0.8), imageUrl = ref(''), loading = ref(false), error = ref('')
const scrollArea = ref(null)
const page = computed(() => props.document.pages.find(item => item.page_no === pageNo.value))
const overlays = computed(() => props.risks.flatMap(risk =>
  anchorRectangles(props.document, risk.anchor, pageNo.value).map((box, index) => ({
    key: `${risk.id}-${index}`, box, risk, active: props.selectedRiskId === risk.id && !props.field,
  }))).sort((a, b) => Number(a.active) - Number(b.active)))
const fieldBoxes = computed(() => anchorRectangles(props.document, props.field?.anchor, pageNo.value))
let sequence = 0, controller, pendingAnchor = null

// 图片请求也携带当前身份，避免 img 直连遗漏鉴权头；换页/卸载及时释放 blob。
async function loadPage() {
  const current = ++sequence
  controller?.abort()
  controller = new AbortController()
  loading.value = true; error.value = ''
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
  try {
    const query = new URLSearchParams({ review_version: props.reviewVersion, file_id: props.document.file_id })
    const blob = await request(`/tasks/${props.taskId}/pages/${pageNo.value}?${query}`, { blob: true, signal: controller.signal })
    if (current !== sequence) return
    imageUrl.value = URL.createObjectURL(blob)
  } catch (e) {
    if (current === sequence && e.name !== 'AbortError') error.value = e.message
  } finally { if (current === sequence) loading.value = false }
}
function fit() {
  if (page.value && scrollArea.value) zoom.value = Math.min(2, Math.max(0.25, (scrollArea.value.clientWidth - 40) / page.value.width))
}
function changeZoom(amount) { zoom.value = Math.min(2, Math.max(0.25, zoom.value + amount)) }
async function focusAnchor(anchor) {
  if (!anchor?.page_no || !props.document.pages.some(item => item.page_no === anchor.page_no)) return
  // 只有用户主动定位时才滚动；跨页定位等图片加载完成后执行一次。
  pendingAnchor = anchor
  pageNo.value = anchor.page_no
  await nextTick()
  if (imageUrl.value && !loading.value) {
    scrollToAnchor(anchor)
    pendingAnchor = null
  }
}
function scrollToAnchor(anchor) {
  const box = anchorRectangles(props.document, anchor, pageNo.value)[0]
  if (!box || !scrollArea.value) return
  scrollArea.value.scrollTo({
    top: Math.max(0, box.y0 * zoom.value - scrollArea.value.clientHeight / 3),
    left: Math.max(0, box.x0 * zoom.value - scrollArea.value.clientWidth / 3), behavior: 'smooth',
  })
}
function imageLoaded() {
  if (pendingAnchor?.page_no === pageNo.value) scrollToAnchor(pendingAnchor)
  pendingAnchor = null
}
// 多个独立监听源比较实际值，避免轮询替换 document 对象时因新数组引用重复加载图片。
watch([() => pageNo.value, () => props.reviewVersion, () => props.document.file_id], () => {
  if (!page.value) { pageNo.value = 1; return }
  if (pendingAnchor?.page_no !== pageNo.value) pendingAnchor = null
  loadPage()
})
onMounted(() => { fit(); loadPage() })
onBeforeUnmount(() => { sequence++; controller?.abort(); if (imageUrl.value) URL.revokeObjectURL(imageUrl.value) })
defineExpose({ focusAnchor })
</script>

<template>
  <div class="pdf-reader">
    <div class="pdf-toolbar">
      <button class="button secondary small-button" :disabled="pageNo <= 1" @click="pageNo--">上一页</button>
      <label>第 <select v-model.number="pageNo" aria-label="原件页码"><option v-for="item in document.pages" :key="item.page_no" :value="item.page_no">{{ item.page_no }}</option></select> / {{ document.pages.length }} 页</label>
      <button class="button secondary small-button" :disabled="pageNo >= document.pages.length" @click="pageNo++">下一页</button>
      <div class="pdf-zoom"><button class="text-button" aria-label="缩小" @click="changeZoom(-0.1)">−</button><span>{{ Math.round(zoom * 100) }}%</span><button class="text-button" aria-label="放大" @click="changeZoom(0.1)">＋</button><button class="text-button" @click="fit">适宽</button></div>
    </div>
    <div class="document-note">原件页面 · 黄色为风险证据，蓝色为选中字段 · 精度：{{ ['PDF_OCR', 'IMAGE_OCR'].includes(document.source_format) ? 'OCR 内容块' : '行/段落' }}</div>
    <div v-if="error" class="notice error" role="alert">{{ error }}<button class="text-button" @click="loadPage">重试</button></div>
    <div ref="scrollArea" class="pdf-scroll" :aria-busy="loading">
      <div v-if="loading" class="empty-state"><span class="spinner"></span> 正在读取第 {{ pageNo }} 页…</div>
      <div v-if="imageUrl && page" class="pdf-page" :style="{ width: `${page.width * zoom}px`, height: `${page.height * zoom}px` }">
        <img :src="imageUrl" :alt="`合同原件第 ${pageNo} 页`" draggable="false" @load="imageLoaded" />
        <button v-for="item in overlays" :key="item.key" class="pdf-evidence" :class="{ active: item.active }" :style="rectangleStyle(item.box)" :title="item.risk.risk_name" :aria-label="`定位风险：${item.risk.risk_name}`" @click="emit('select-risk', item.risk)"></button>
        <div v-for="(box, index) in fieldBoxes" :key="`field-${index}`" class="pdf-field-evidence" :style="rectangleStyle(box)"></div>
      </div>
    </div>
  </div>
</template>
