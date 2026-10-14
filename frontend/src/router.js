import { createRouter, createWebHistory } from 'vue-router'
import EnvironmentView from './views/EnvironmentView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', component: EnvironmentView }],
})
