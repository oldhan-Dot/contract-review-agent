<script setup>
import { onMounted, ref } from 'vue'

const report = ref(null)
const error = ref('')
const loading = ref(false)
const names = { db: 'MySQL 数据库', llm: '模型服务', mineru: 'MinerU 文档解析', approval: '审批 Mock' }
const labels = {
  ok: '可用', error: '连接异常', disabled: '待配置',
  reachable: '连接可用，功能待验证', unverified: '待验证', pending: '待实现',
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch(`${import.meta.env.VITE_API_BASE || '/api'}/health`)
    const body = await response.json()
    if (!body.db) throw new Error('invalid response')
    report.value = body
  } catch {
    report.value = null
    error.value = '后端尚未连接，请确认后端已在 8000 端口启动。'
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <section>
    <h1>环境检查</h1>
    <p>查看实际服务连接状态。模型未配置时保留规则初筛结果，并明确显示审查未完成。</p>
    <button :disabled="loading" @click="refresh">{{ loading ? '检查中…' : '刷新状态' }}</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="report" class="grid">
      <article v-for="(name, key) in names" :key="key">
        <h2>{{ name }}</h2>
        <strong :class="report[key].status">{{ labels[report[key].status] }}</strong>
        <p v-if="key === 'db' && report.db.status === 'ok'">
          {{ report.db.tables }} 张表 · {{ report.db.users }} 个预置用户
        </p>
        <p v-else>{{ report[key].message }}</p>
      </article>
    </div>
    <a href="http://127.0.0.1:8000/docs" target="_blank" rel="noopener">查看后端 API 文档</a>
  </section>
</template>
