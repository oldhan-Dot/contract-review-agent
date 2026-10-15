<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { identity, request, statusNames, levelNames, typeNames } from '../lib/api'

const router = useRouter()
const rows = ref([]), stats = ref({}), total = ref(0), page = ref(1)
const status = ref(''), level = ref(''), error = ref(''), loading = ref(false), capabilities = ref(null)
const showUpload = ref(false), sending = ref(false), file = ref(null)
const form = ref({ contract_type: 'PURCHASE', our_party_role: '', contract_name: '', applicant_dept: '' })
const metrics = computed(() => [
  { label: '全部合同', value: Object.values(stats.value).reduce((sum, n) => sum + n, 0), icon: '▤', tone: 'blue' },
  { label: '正在处理', value: ['pending', 'parsing', 'reviewing'].reduce((sum, k) => sum + (stats.value[k] || 0), 0), icon: '◷', tone: 'amber' },
  { label: '审查完成', value: stats.value.completed || 0, icon: '✓', tone: 'green' },
  { label: '需要处理', value: stats.value.blocked || 0, icon: '!', tone: 'red' },
])
let interval
async function load() {
  if (loading.value) return
  loading.value = true
  try {
    const query = new URLSearchParams({ page: String(page.value), page_size: '10' })
    if (status.value) query.set('status', status.value)
    if (level.value) query.set('risk_level', level.value)
    const data = await request(`/tasks?${query}`)
    rows.value = data.items; total.value = data.total; stats.value = data.stats; error.value = ''
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
function filter() { page.value = 1; load() }
async function upload() {
  error.value = ''
  if (!file.value || !form.value.our_party_role) { error.value = '请选择 DOCX 文件，并明确我方角色。'; return }
  sending.value = true
  try {
    const data = new FormData()
    data.append('file', file.value)
    for (const [key, value] of Object.entries(form.value)) if (value.trim()) data.append(key, value.trim())
    const result = await request('/tasks/upload', { method: 'POST', body: data })
    showUpload.value = false
    router.push(`/tasks/${result.task_id}`)
  } catch (e) { error.value = e.message } finally { sending.value = false }
}
async function demo() {
  sending.value = true
  try {
    const result = await request('/demo/task', { method: 'POST' })
    router.push(`/tasks/${result.task_id}`)
  } catch (e) { error.value = e.message } finally { sending.value = false }
}
function date(value) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
onMounted(async () => {
  load(); interval = setInterval(load, 4000)
  try { capabilities.value = await request('/workspace') } catch { /* 列表会显示连接错误 */ }
})
onUnmounted(() => clearInterval(interval))
</script>

<template>
  <main class="page dashboard">
    <div class="page-heading"><div><div class="eyebrow">合同全流程管理</div><h1>合同审查大盘</h1><p>提交合同、跟踪审查进度，在一个工作空间完成风险复核。</p></div>
      <div class="actions" v-if="identity.role === 'BIZ'"><button class="button secondary" :disabled="sending" @click="demo">体验合成样例</button><button class="button primary" @click="showUpload = true">＋ 上传合同</button></div>
    </div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <div class="metrics"><article v-for="metric in metrics" :key="metric.label" class="metric"><span class="metric-icon" :class="metric.tone">{{ metric.icon }}</span><div><span>{{ metric.label }}</span><strong>{{ metric.value }}</strong></div></article></div>
    <div v-if="capabilities && !capabilities.llm_configured" class="notice amber"><b>模型服务待配置</b> · 可以上传并查看真实规则初筛结果，完整模型审查暂不可用，不会自动确认为审查完成。</div>
    <div v-if="capabilities && !capabilities.runner" class="notice error">后台执行器未就绪，任务会保留等待状态。请检查环境状态。</div>
    <section class="panel task-panel">
      <div class="panel-heading"><h2>审查任务 <span class="count">{{ total }}</span></h2><button class="text-button" @click="load">↻ 刷新</button></div>
      <div class="filter-bar"><select v-model="status" @change="filter"><option value="">全部审查状态</option><option v-for="(label, key) in statusNames" :key="key" :value="key">{{ label }}</option></select><select v-model="level" @change="filter"><option value="">全部风险等级</option><option v-for="(label, key) in levelNames" :key="key" :value="key">{{ label }}</option></select><span class="muted filter-note">每 4 秒自动更新处理状态</span></div>
      <div class="table-scroll"><table class="task-table"><thead><tr><th>合同名称 / 编号</th><th>业务类型</th><th>申请人</th><th>合同金额</th><th>审查状态</th><th>已识别风险</th><th>创建时间</th><th></th></tr></thead>
        <tbody><tr v-for="task in rows" :key="task.id" @click="router.push(`/tasks/${task.id}`)"><td class="contract-cell"><span class="file-symbol">W</span><div><strong>{{ task.contract_name }}</strong><small>{{ task.task_no }}</small></div></td><td>{{ typeNames[task.contract_type] || task.contract_type }}</td><td>{{ task.applicant }}</td><td class="amount">{{ task.amount ? `${task.currency || ''} ${Number(task.amount).toLocaleString('zh-CN')}` : '待提取' }}</td><td><span class="status-pill" :class="task.status"><i></i>{{ statusNames[task.status] || task.status }}</span></td><td><span v-if="task.overall_risk_level" class="risk-pill" :class="task.overall_risk_level">{{ levelNames[task.overall_risk_level] }}</span><span v-else class="muted">—</span></td><td class="small muted">{{ date(task.created_at) }}</td><td><button class="text-button">查看 →</button></td></tr></tbody></table></div>
      <div v-if="!rows.length" class="empty-state"><div class="empty-symbol">▤</div><h3>还没有符合条件的合同</h3><p>{{ identity.role === 'BIZ' ? '上传一份 DOCX，或使用合成样例体验审查流程。' : '切换为业务演示身份上传合同，任务将在这里显示。' }}</p></div>
      <div class="pagination"><span>共 {{ total }} 条任务</span><div><button class="button secondary small-button" :disabled="page <= 1" @click="page--; load()">上一页</button><span>{{ page }}</span><button class="button secondary small-button" :disabled="page * 10 >= total" @click="page++; load()">下一页</button></div></div>
    </section>
    <div class="dashboard-foot"><span>支持 DOCX · 原件与证据保留 · 审查意见需法务确认</span><span>PDF / OCR 与分页预览将逐步补齐</span></div>
    <div v-if="showUpload" class="modal-backdrop" @click.self="showUpload = false"><form class="modal upload-modal" @submit.prevent="upload"><div class="modal-heading"><h2>提交合同审查</h2><button type="button" class="icon-button" @click="showUpload = false">×</button></div><p class="muted">明确合同类型和我方角色，名称及金额由系统尝试提取。</p>
      <label class="upload-drop"><span class="upload-symbol">↑</span><strong>{{ file?.name || '选择 DOCX 合同文件' }}</strong><small>当前支持 DOCX，最大 50 MB</small><input type="file" accept=".docx" @change="file = $event.target.files[0]" required /></label>
      <div class="form-grid"><label>合同类型<select v-model="form.contract_type"><option v-for="(label, key) in typeNames" v-show="key !== 'UNKNOWN'" :key="key" :value="key">{{ label }}</option></select></label><label>我方角色<select v-model="form.our_party_role" required><option value="" disabled>请明确选择</option><option value="A">我方是甲方</option><option value="B">我方是乙方</option></select></label></div><label>合同名称 <span class="muted">可选</span><input v-model="form.contract_name" maxlength="255" placeholder="留空后从正文提取" /></label><label>送审部门 <span class="muted">可选</span><input v-model="form.applicant_dept" maxlength="64" placeholder="例如：采购部" /></label><div v-if="error" class="notice error">{{ error }}</div><div class="modal-actions"><button type="button" class="button secondary" @click="showUpload = false">取消</button><button class="button primary" :disabled="sending">{{ sending ? '上传中…' : '提交并开始审查' }}</button></div>
    </form></div>
  </main>
</template>
