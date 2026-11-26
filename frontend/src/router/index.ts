import { createRouter, createWebHistory } from 'vue-router'

import AlertOpsView from '@/views/AlertOpsView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径优先于动态模块路由，避免被 /:moduleKey 吞掉
    { path: '/alert_ops', name: 'alert-ops', component: AlertOpsView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
