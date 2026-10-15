<script setup>
import { onMounted, ref } from 'vue'
import { identity, request, levelNames } from '../lib/api'
const rules = ref([]), selected = ref(null), error = ref(''), message = ref(''), busy = ref(false)
async function load() {
  try { rules.value = await request('/rules'); selected.value = rules.value.find(r => r.code === selected.value?.code) || rules.value[0] } catch (e) { error.value = e.message }
}
async function save() {
  busy.value = true; error.value = ''; message.value = ''
  try {
    const rule = selected.value
    await request(`/rules/${rule.code}`, { method: 'PATCH', body: JSON.stringify({ expected_version: rule.version, enabled: rule.enabled, level: rule.level, basis: rule.basis, suggestion: rule.suggestion }) })
    await load(); message.value = '规则与示范文本已保存，后续新任务或重新审查时使用。'
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <main class="page"><div class="page-heading"><div><div class="eyebrow">可解释的审查标准</div><h1>规则与示范条款</h1><p>管理首批审查规则、风险等级和推荐修改文本。</p></div><span class="version-tag">{{ rules.length }} 条初筛规则</span></div><div v-if="error" class="notice error">{{ error }}</div><div v-if="message" class="notice success">{{ message }}</div><div class="notice blue">当前支持启停、等级、依据和示范文本维护。复杂条件编辑与更多专项规则后续补齐，配置变更不会改写历史审查结果。</div>
    <div class="rules-layout"><section class="panel rule-list"><div class="panel-heading"><h2>审查清单</h2></div><button v-for="rule in rules" :key="rule.code" class="rule-list-item" :class="{ selected: selected?.code === rule.code }" @click="selected = rule"><span class="rule-code">{{ rule.code }}</span><strong>{{ rule.name }}</strong><span class="rule-list-bottom"><span class="risk-pill" :class="rule.level">{{ levelNames[rule.level] }}</span><small :class="rule.enabled ? 'green-text' : 'muted'">{{ rule.enabled ? '● 已启用' : '○ 已停用' }}</small></span></button></section>
      <section v-if="selected" class="panel rule-editor"><div class="panel-heading"><div><h2>{{ selected.name }}</h2><span class="muted small">{{ selected.code }} · 配置版本 {{ selected.version }}</span></div><button v-if="identity.role === 'ADMIN'" class="button primary" :disabled="busy" @click="save">保存配置</button><span v-else class="muted">法务只读</span></div><div class="rule-editor-body"><label>规则适用说明<div class="condition-description">{{ selected.description }}</div></label><div class="form-grid"><label>风险等级<select v-model="selected.level" :disabled="identity.role !== 'ADMIN'"><option v-for="(name, key) in levelNames" :key="key" :value="key">{{ name }}</option></select></label><label>是否启用<div class="checkbox-row"><input type="checkbox" v-model="selected.enabled" :disabled="identity.role !== 'ADMIN'" />在后续任务中使用</div></label></div><label>审查依据<textarea v-model="selected.basis" :readonly="identity.role !== 'ADMIN'" maxlength="2000" rows="3"></textarea><small class="muted">请注明依据来源，不将演示阈值或谈判建议表述为通用法律规定。</small></label><label>关联示范条款<textarea v-model="selected.suggestion" :readonly="identity.role !== 'ADMIN'" maxlength="8000" rows="8"></textarea><small class="muted">作为风险命中后的参考文本，法务仍可按具体合同修改。</small></label></div></section>
    </div>
  </main>
</template>
