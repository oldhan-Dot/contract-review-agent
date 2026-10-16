import { ref } from 'vue'

let saved = null
try { saved = JSON.parse(localStorage.getItem('review-identity')) } catch { /* 首次访问 */ }
export const identity = ref(saved)
export const demoUsers = ref([])

export function setIdentity(user) {
  identity.value = user
  localStorage.setItem('review-identity', JSON.stringify(user))
}

export async function request(path, options = {}) {
  const headers = { ...options.headers }
  if (identity.value) {
    headers['X-User-Id'] = String(identity.value.id)
    headers['X-User-Role'] = identity.value.role
  }
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const response = await fetch(`${import.meta.env.VITE_API_BASE || '/api'}${path}`, { ...options, headers })
  if (options.blob && response.ok) return response.blob()
  let body
  try { body = await response.json() } catch { throw new Error('后端未响应，请确认服务已启动。') }
  if (!response.ok) throw new Error(body.message || `请求失败（${response.status}）`)
  return body.data
}

export async function loadIdentities() {
  demoUsers.value = await request('/demo/users')
  const found = demoUsers.value.find(user => user.id === identity.value?.id)
  setIdentity(found || demoUsers.value.find(user => user.role === 'BIZ') || demoUsers.value[0])
}

export const statusNames = { pending: '等待处理', parsing: '解析中', reviewing: '审查中', blocked: '处理受阻', completed: '审查完成' }
export const stageNames = { parse_document: '文档解析', structure_document: '条款和字段整理', rule_review: '规则检查', ai_review: 'AI 审查', save_results: '保存结果' }
export const levelNames = { HIGH: '高风险', MEDIUM: '中风险', LOW: '低风险' }
export const typeNames = { PURCHASE: '采购合同', SALES: '销售合同', SERVICE: '服务合同', LABOR: '劳动合同', UNKNOWN: '未分类' }

export async function downloadReport(task, format) {
  const blob = await request(`/tasks/${task.id}/report?format=${format}&download=true`, { blob: true })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `审查报告-${task.task_no}-v${task.review_version}.${format}`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
