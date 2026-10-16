<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import PdfDocument from '../components/PdfDocument.vue'
import { downloadReport, identity, request, statusNames, stageNames, typeNames, levelNames } from '../lib/api'

const route = useRoute(), id = route.params.id
const data = ref(null), selected = ref(null), tab = ref('document'), filter = ref('ALL')
const error = ref(''), message = ref(''), busy = ref(false), loading = ref(false)
const drafts = ref({}), comment = ref(''), dirty = ref(false), editingRevision = ref(null), editingVersion = ref(null)
const report = ref(null), comments = ref(null)
const pdfReader = ref(null), viewMode = ref('pdf'), selectedField = ref(null)
const canReview = computed(() => identity.value?.role === 'LEGAL')
// 重审可能从相同修订号重新开始，因此同时核对审查版本和意见修订号。
const stale = computed(() => dirty.value && (editingRevision.value !== data.value?.revision || editingVersion.value !== data.value?.task.review_version))
const risks = computed(() => (data.value?.risks || []).filter(risk => filter.value === 'ALL' || risk.risk_level === filter.value))
const activeRisk = computed(() => data.value?.risks.find(risk => risk.id === selected.value))
const isPdf = computed(() => ['PDF_TEXT', 'PDF_OCR', 'IMAGE_OCR'].includes(data.value?.document?.source_format))
const activeAnchor = computed(() => selectedField.value?.anchor || activeRisk.value?.anchor)
// 根据已有审查状态提示下一步，不另建一套流程状态。
const nextStep = computed(() => {
  if (!data.value) return ''
  if (stale.value) return '审查结果已更新，请先重新载入，再根据新结果修改意见。'
  if (data.value.task.status === 'blocked') return '审查暂未完成，可查看已有结果；请管理员处理受阻原因后重新审查。'
  if (!data.value.complete) return '正在自动审查，完成后可核对原文、风险和修改建议。'
  if (!canReview.value) return identity.value?.role === 'ADMIN'
    ? '可查看结果和管理规则；修改建议与确认审查请切换法务身份。'
    : '可查看风险和下载报告；修改建议与确认审查由法务完成。'
  if (dirty.value) return '意见有未保存的修改，请保存草稿，或确认本次审查后再演示回写。'
  return data.value.confirmed
    ? '法务已确认，可导出报告，或演示将意见写入模拟审批评论区。'
    : '请核对风险对应原文，按需修改建议，再确认本次审查。'
})
let interval
async function load() {
  if (loading.value) return
  loading.value = true
  try {
    const result = await request(`/tasks/${id}`)
    if (data.value?.task.review_version !== result.task.review_version) { selectedField.value = null; selected.value = null }
    data.value = result
    if (!dirty.value) {
      drafts.value = Object.fromEntries(result.risks.map(risk => [risk.id, result.human_review.suggestions?.[risk.id] ?? risk.suggestion?.text ?? '']))
      comment.value = result.human_review.comment || ''
      editingRevision.value = result.revision
      editingVersion.value = result.task.review_version
    }
    if (!selected.value && result.risks.length) selected.value = result.risks[0].id
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
async function focus(risk) {
  selectedField.value = null; selected.value = risk.id; tab.value = 'document'
  if (risk.anchor?.paragraph_index == null) { message.value = '这是缺失项，没有可高亮的原文位置。'; return }
  message.value = ''
  await nextTick()
  if (pdfReader.value && viewMode.value === 'pdf') await pdfReader.value.focusAnchor(risk.anchor)
  else document.getElementById(`paragraph-${risk.anchor.paragraph_index}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
function parts(paragraph) {
  const anchor = activeAnchor.value
  const start = Math.max(0, (anchor?.global_char_start ?? -1) - paragraph.char_start)
  const end = Math.min(paragraph.text.length, (anchor?.global_char_end ?? -1) - paragraph.char_start)
  if (!anchor || end <= start) return [paragraph.text, '', '']
  return [paragraph.text.slice(0, start), paragraph.text.slice(start, end), paragraph.text.slice(end)]
}
function selectParagraph(paragraph) {
  const risk = data.value.risks.find(item => item.anchor && item.anchor.global_char_start < paragraph.char_end && item.anchor.global_char_end > paragraph.char_start)
  if (risk) selectRisk(risk)
}
function selectRisk(risk) {
  selectedField.value = null; selected.value = risk.id; filter.value = 'ALL'
  nextTick(() => document.getElementById(`risk-${risk.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
}
async function focusField(field) {
  if (!field.anchor) { message.value = '该字段没有可定位的原文证据。'; return }
  selectedField.value = field; tab.value = 'document'; message.value = ''
  await nextTick()
  if (pdfReader.value && viewMode.value === 'pdf') await pdfReader.value.focusAnchor(field.anchor)
  else document.getElementById(`paragraph-${field.anchor.paragraph_index}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
async function action(callback) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await callback() } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function save(confirm) {
  if (busy.value || stale.value || (!confirm && !dirty.value)) return
  await action(async () => {
    await request(`/tasks/${id}/review`, { method: 'PATCH', body: JSON.stringify({ review_version: editingVersion.value, expected_revision: editingRevision.value, suggestions: drafts.value, comment: comment.value, confirm }) })
    dirty.value = false; await load(); message.value = confirm ? '法务确认已保存。' : '意见草稿已保存。'
  })
}
async function retry() {
  await action(async () => { await request(`/tasks/${id}/retry`, { method: 'POST', body: JSON.stringify({ review_version: data.value.task.review_version }) }); dirty.value = false; selected.value = null; await load() })
}
async function writeback() {
  await action(async () => {
    // 仅合并演示操作入口；关联和回写仍分别由后端检查权限与确认版本。
    const version = data.value.task.review_version, revision = data.value.revision
    if (!data.value.task.approval_no) await request(`/tasks/${id}/mock-approval`, { method: 'POST' })
    await request(`/tasks/${id}/writeback`, { method: 'POST', body: JSON.stringify({ review_version: version, expected_revision: revision }) })
    await load(); message.value = '意见已写入模拟审批评论区，可点击“查看模拟评论”核对。'
  })
}
onMounted(() => { load(); interval = setInterval(load, 3000) })
onUnmounted(() => clearInterval(interval))
</script>

<template>
  <main class="page review-page">
    <RouterLink to="/" class="back-link">← 返回合同大盘</RouterLink>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div><div v-if="message" class="notice success">{{ message }}</div>
    <template v-if="data">
      <div class="page-heading compact"><div><h1>{{ data.task.contract_name }} <span class="status-pill" :class="data.task.status"><i></i>{{ statusNames[data.task.status] }}</span></h1><p>{{ typeNames[data.task.contract_type] }} <span class="separator">/</span> 我方{{ data.task.our_party_role === 'A' ? '甲方' : '乙方' }} <span class="separator">/</span> {{ data.task.task_no }} <span class="separator">/</span> 第 {{ data.task.review_version }} 次审查</p></div><div class="actions"><button class="button secondary" :disabled="busy || !data.document" @click="action(async () => { report = await request(`/tasks/${id}/report`) })">报告预览</button><button class="button secondary" :disabled="busy || !data.document" @click="action(() => downloadReport(data.task, 'pdf'))">导出 PDF</button><button class="button secondary" :disabled="busy || !data.document" @click="action(() => downloadReport(data.task, 'md'))">Markdown</button></div></div>
      <div v-if="data.task.status === 'blocked'" class="notice amber"><div><b>当前审查尚未完成</b><br>{{ data.task.block_detail }}</div><button v-if="identity.role === 'ADMIN'" class="button secondary small-button" :disabled="busy" @click="retry">重新审查</button><small v-else>修正配置后，可切换管理员身份重新审查。</small></div>
      <div v-else-if="['pending', 'parsing', 'reviewing'].includes(data.task.status)" class="notice blue"><span class="spinner"></span>正在处理合同，结果会自动更新。当前阶段：{{ stageNames[data.stage] || statusNames[data.task.status] }}</div>
      <div class="notice blue next-step">{{ nextStep }}</div>
      <div v-if="dirty" class="notice amber">当前修改尚未保存。报告预览、导出和模拟评论使用已保存内容；请先保存草稿或确认审查。</div>
      <div class="review-summary"><div><span class="eyebrow">已识别综合风险</span><strong :class="data.task.overall_risk_level">{{ levelNames[data.task.overall_risk_level] || '待评估' }}</strong></div><p>{{ data.task.review_summary || '等待解析和审查结果。' }}</p><div class="summary-count"><b>{{ data.risks.length }}</b><span>已识别风险项</span></div><span class="confirmation" :class="{ done: data.confirmed }">{{ data.confirmed ? '✓ 法务已确认' : '○ 尚未人工确认' }}</span></div>
      <div class="workspace-columns">
        <section class="panel document-panel"><div class="panel-heading"><div class="tabs"><button :class="{ active: tab === 'document' }" @click="tab = 'document'">合同原文</button><button :class="{ active: tab === 'metadata' }" @click="tab = 'metadata'">提取信息</button></div><div v-if="isPdf && tab === 'document'" class="pdf-view-switch"><button class="text-button" @click="viewMode = viewMode === 'pdf' ? 'text' : 'pdf'">{{ viewMode === 'pdf' ? '切换文字视图' : '查看原件' }}</button></div><span v-else class="muted small">原件保留 · 证据定位</span></div>
          <div class="document-note">{{ isPdf ? '原件可缩放、定位证据；PDF 可翻页，单张图片按一页展示。' : 'DOCX 段落阅读视图，原始分页与转换 PDF 预览后续补齐。' }}</div>
          <div v-if="data.document?.quality?.warnings?.length" class="document-note">{{ data.document.quality.warnings.join('；') }}</div>
          <div v-if="!data.document" class="empty-state"><div class="empty-symbol">▤</div><h3>等待文档解析</h3><p>原文会在解析完成后显示。</p></div>
          <PdfDocument v-else-if="tab === 'document' && viewMode === 'pdf' && isPdf" ref="pdfReader" :task-id="id" :review-version="data.task.review_version" :document="data.document" :risks="data.risks" :selected-risk-id="selected" :field="selectedField" @select-risk="selectRisk" />
          <div v-else-if="tab === 'document'" class="document-scroll"><div class="paper"><div class="paper-caption">{{ data.task.contract_name }}</div><div v-for="paragraph in data.document.paragraphs" :key="paragraph.index" :id="`paragraph-${paragraph.index}`" class="paragraph" :class="{ 'heading-paragraph': paragraph.heading_level, 'table-paragraph': paragraph.in_table, 'has-risk': data.risks.some(r => r.anchor && r.anchor.paragraph_index <= paragraph.index && r.anchor.paragraph_index_end >= paragraph.index) }" @click="selectParagraph(paragraph)"><span class="paragraph-number">{{ paragraph.page_no ? `P${paragraph.page_no} · ` : '' }}{{ paragraph.index + 1 }}</span><span>{{ parts(paragraph)[0] }}<mark v-if="parts(paragraph)[1]">{{ parts(paragraph)[1] }}</mark>{{ parts(paragraph)[2] }}</span></div></div></div>
          <div v-else class="metadata-list"><article v-for="field in data.metadata?.items || []" :key="field.field_key" class="metadata-item" :class="{ locatable: field.anchor }" @click="focusField(field)"><label>{{ field.field_label }}</label><strong :class="{ 'missing-value': field.is_missing }">{{ field.field_value || '未识别' }}</strong><button v-if="field.anchor" class="text-button small">定位原文 ↗</button><small v-if="field.warnings?.length">{{ field.warnings.join('；') }}</small></article></div>
        </section>
        <section class="panel risk-panel"><div class="panel-heading"><h2>风险与修改建议 <span class="count">{{ data.risks.length }}</span></h2><span class="muted small">点击卡片定位原文</span></div><div class="risk-tabs"><button v-for="key in ['ALL', 'HIGH', 'MEDIUM', 'LOW']" :key="key" :class="{ active: filter === key }" @click="filter = key">{{ key === 'ALL' ? '全部' : levelNames[key] }} <span>{{ data.risks.filter(risk => key === 'ALL' || risk.risk_level === key).length }}</span></button></div>
          <div class="risk-scroll"><div v-if="!risks.length" class="empty-state"><div class="empty-symbol">◇</div><h3>{{ data.complete ? '当前筛选下没有风险' : '等待风险结果' }}</h3><p>{{ data.complete ? '仍需结合完整合同进行人工复核。' : '尚无结果不代表合同已经安全。' }}</p></div>
            <article v-for="risk in risks" :key="risk.id" :id="`risk-${risk.id}`" class="risk-card" :class="{ selected: selected === risk.id }"><div class="risk-card-top" @click="focus(risk)"><span class="risk-pill" :class="risk.risk_level">{{ levelNames[risk.risk_level] }}</span><small class="muted">{{ risk.source === 'llm' ? '模型研判' : `规则 ${risk.rule_code}` }}</small><button class="text-button">{{ risk.anchor ? '定位 ↗' : '缺失项' }}</button></div><h3 @click="focus(risk)">{{ risk.risk_name }}</h3><blockquote v-if="risk.original_text" @click="focus(risk)">{{ risk.original_text }}</blockquote><p>{{ risk.risk_reason }}</p><div class="basis"><span>审查依据</span>{{ risk.legal_basis || '待法务确认' }}</div><label class="suggestion-label">推荐修改 <small>{{ risk.suggestion?.source === 'llm' ? '模型生成 · 待确认' : '示范条款参考' }}</small></label><textarea :value="drafts[risk.id] ?? risk.suggestion?.text ?? ''" :readonly="!canReview || busy" rows="3" @input="drafts[risk.id] = $event.target.value; dirty = true"></textarea><div class="risk-actions"><button class="text-button" @click="action(async () => { await navigator.clipboard.writeText(drafts[risk.id] || risk.suggestion?.text || ''); message = '建议已复制。' })">复制建议</button><span class="muted small">{{ canReview ? '可直接编辑，保存后生效' : '由法务编辑与确认' }}</span></div></article>
          </div></section>
      </div>
      <section class="panel human-panel"><div class="panel-heading"><h2>法务复核与审批协同</h2><span class="muted small">{{ data.task.approval_no || '演示回写时自动关联模拟审批单' }}</span></div><div v-if="stale" class="notice amber">审查结果已更新，请重新载入。重新载入会放弃当前未保存的修改。<button class="text-button" @click="dirty = false; load()">重新载入</button></div><label>补充法务意见<textarea v-model="comment" :readonly="!canReview || busy" maxlength="10000" rows="3" placeholder="填写需要业务方重点关注的事项、整改要求或补充说明…" @input="dirty = true"></textarea></label><div class="human-actions"><div class="actions"><button v-if="canReview" class="button secondary" :disabled="busy || !dirty || stale || !['blocked', 'completed'].includes(data.task.status)" @click="save(false)">保存意见草稿</button><button v-if="canReview" class="button primary" :disabled="busy || stale || !data.complete || (data.confirmed && !dirty)" @click="save(true)">{{ data.confirmed && !dirty ? '法务已确认' : '确认本次审查' }}</button></div><div class="actions"><button class="button secondary" :disabled="busy || !data.task.approval_no" @click="action(async () => { comments = await request(`/tasks/${id}/comments`) })">查看模拟评论</button><button v-if="canReview" class="button primary" :disabled="busy || dirty || stale || !data.complete || !data.confirmed || data.task.writeback_status === 'success'" @click="writeback">{{ data.task.writeback_status === 'success' ? '已写入模拟审批' : '演示回写' }}</button></div></div><p class="muted small">演示回写仅写入本系统的模拟审批评论区。{{ data.coverage_notes.join(' ') }} {{ !data.complete ? '部分结果可保存草稿和导出，完整审查确认暂不可用。' : '' }}</p></section>
    </template>
    <div v-else-if="!error" class="empty-state">正在加载合同…</div>
    <div v-if="report || comments" class="modal-backdrop" @click.self="report = null; comments = null"><div class="modal report-modal"><div class="modal-heading"><h2>{{ report ? '审查报告预览' : '模拟审批评论' }}</h2><button class="icon-button" @click="report = null; comments = null">×</button></div><div v-if="report?.partial" class="notice amber">此报告为部分结果草稿，不是完整审查结论。</div><pre v-if="report">{{ report.markdown }}</pre><template v-else><p v-if="!comments.length" class="muted">当前还没有回写评论。</p><article v-for="item in comments" :key="item.id"><h3>{{ item.author }} · 评论 #{{ item.id }}</h3><pre>{{ item.content }}</pre></article></template></div></div>
  </main>
</template>
