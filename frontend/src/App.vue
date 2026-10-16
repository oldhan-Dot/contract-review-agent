<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { demoUsers, identity, loadIdentities, setIdentity } from './lib/api'
const startupError = ref('')
const router = useRouter()
async function switchIdentity(event) {
  const user = demoUsers.value.find(user => user.id === Number(event.target.value))
  if (!user || user.id === identity.value?.id) return
  // 身份改变后回到大盘；RouterView 按用户 ID 重建，重新加载该身份有权查看的数据。
  setIdentity(user)
  await router.replace('/')
}
onMounted(async () => {
  try { await loadIdentities() } catch (error) { startupError.value = error.message }
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-symbol">契</span><div>契约审查<small>CONTRACT REVIEW</small></div></div>
      <div class="nav-caption">工作空间</div>
      <nav>
        <RouterLink to="/" :class="{ active: $route.path === '/' || $route.path.startsWith('/tasks') }"><span>▤</span> 合同审查大盘</RouterLink>
        <RouterLink v-if="identity?.role !== 'BIZ'" to="/rules" active-class="active"><span>⚖</span> 规则与示范条款</RouterLink>
      </nav>
      <div class="sidebar-note"><span class="live-dot"></span> DOCX / PDF 审查主流程<small>规则初筛 · 证据核对 · 人工复核</small></div>
      <RouterLink class="environment-link" to="/environment">◉ 环境状态</RouterLink>
    </aside>
    <div class="app-content">
      <header class="topbar">
        <span class="breadcrumb">工作空间 <span>/</span> {{ $route.meta.title || '合同审查' }}</span>
        <div class="identity-switch"><span class="demo-label">演示身份</span>
          <select :value="identity?.id" @change="switchIdentity">
            <option v-for="user in demoUsers" :key="user.id" :value="user.id">{{ user.name }}</option>
          </select><span class="avatar">{{ identity?.name?.slice(0, 1) || '用' }}</span>
        </div>
      </header>
      <div v-if="startupError" class="notice error startup-error">{{ startupError }} 请启动后端后刷新。</div>
      <RouterView v-else-if="identity" :key="`${identity.id}-${$route.path}`" />
      <div v-else class="empty-state">正在连接工作台…</div>
    </div>
  </div>
</template>
