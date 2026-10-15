import { createRouter, createWebHistory } from 'vue-router'
import EnvironmentView from './views/EnvironmentView.vue'
import DashboardView from './views/DashboardView.vue'
import ReviewView from './views/ReviewView.vue'
import RulesView from './views/RulesView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardView, meta: { title: '合同审查大盘' } },
    { path: '/tasks/:id', component: ReviewView, meta: { title: '智能审查工作台' } },
    { path: '/rules', component: RulesView, meta: { title: '规则与示范条款' } },
    { path: '/environment', component: EnvironmentView, meta: { title: '环境状态' } },
  ],
})
