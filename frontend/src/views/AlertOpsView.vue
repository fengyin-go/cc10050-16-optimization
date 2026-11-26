<template>
  <section class="page alert-ops" data-module="alert_ops">
    <header class="page-head">
      <div>
        <h2>变价事件处置工作台</h2>
        <p class="page-desc">
          同口径判定引用具体报价与规则版本；交付凭据可回查；更正/撤回只发给证实收到旧告警的收件人，
          未知状态绝不计入成功。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="refresh-all" @click="reloadAll">刷新两页</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">价格事件</span>
        <strong class="stat-value">{{ events.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待判定</span>
        <strong class="stat-value">{{ pendingJudgeCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">交付待确认</span>
        <strong class="stat-value">{{ pendingDeliveryCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">更正待跟进</span>
        <strong class="stat-value">{{ pendingCorrectionCount }}</strong>
      </article>
    </div>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        :data-testid="`tab-${tab.key}`"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <p v-if="message" class="inline-message" :class="messageKind" data-testid="ops-message">{{ message }}</p>

    <!-- 事件与判定 -->
    <div v-if="activeTab === 'events'" class="tab-panel">
      <div class="panel-grid">
        <article class="panel">
          <h3>价格事件（同口径报价快照）</h3>
          <table class="data-table">
            <thead>
              <tr>
                <th>事件编号</th><th>商品</th><th>基线→采集</th><th>幅度</th><th>状态</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="event in events" :key="event.id" :data-testid="`event-${event.id}`"
                  :class="{ selected: selectedEventId === event.id }">
                <td>{{ event.event_no }}</td>
                <td>{{ event.商品标识 }}</td>
                <td>{{ event.基线价格 }} → {{ event.采集价格 }}</td>
                <td>{{ event.变化幅度 }}</td>
                <td>{{ event.status }}</td>
                <td class="row-actions">
                  <button class="link" type="button" @click="selectEvent(event.id)">核对单</button>
                </td>
              </tr>
            </tbody>
          </table>
        </article>

        <article class="panel" data-testid="event-actions">
          <h3>事件判定与故障模拟</h3>
          <template v-if="selectedEvent">
            <dl class="kv">
              <dt>事件</dt><dd>{{ selectedEvent.event_no }}（{{ selectedEvent.商品标识 }}）</dd>
              <dt>报价口径</dt><dd>{{ selectedEvent.基线价格 }} → {{ selectedEvent.采集价格 }}，{{ selectedEvent.变化幅度 }}</dd>
              <dt>报价来源</dt><dd>{{ selectedEvent.报价来源 }}</dd>
              <dt v-if="selectedEvent.更正后价格">更正后</dt>
              <dd v-if="selectedEvent.更正后价格">{{ selectedEvent.更正后价格 }}（{{ selectedEvent.复核结论 }}）</dd>
            </dl>

            <div class="action-block">
              <h4>1. 同口径判定（规则版本幂等）</h4>
              <label class="inline-field">
                规则ID
                <input v-model="ruleId" data-testid="rule-id-input" class="small-input" />
              </label>
              <button class="btn primary" type="button" data-testid="evaluate-btn" @click="evaluate">
                引用规则判定
              </button>
            </div>

            <div class="action-block">
              <h4>2. 发起交付（可回查告警接收记录模拟）</h4>
              <label class="inline-field">
                故障模式
                <select v-model="simulate" data-testid="simulate-select">
                  <option value="normal">正常（回执立即可查）</option>
                  <option value="receipt_lost">回执丢失（已收到，回执暂不可查）</option>
                  <option value="not_received">未收到（无送达证据）</option>
                </select>
              </label>
              <button
                v-for="judgement in selectedJudgements"
                :key="judgement.id"
                class="btn"
                type="button"
                :disabled="!judgement.触发"
                :data-testid="`dispatch-${judgement.id}`"
                @click="dispatch(judgement.id)"
              >
                按{{ judgement.判定编号 }}（{{ judgement.规则版本 }} {{ judgement.变化幅度 }}）发起
              </button>
            </div>

            <div class="action-block">
              <h4>3. 价格更正 / 误报处置</h4>
              <label class="inline-field">
                更正价格
                <input v-model="correctedPrice" data-testid="corrected-price" class="small-input" />
              </label>
              <label class="inline-field">
                原因
                <input v-model="correctionReason" data-testid="correction-reason" />
              </label>
              <label class="inline-field">
                <input v-model="falsePositive" type="checkbox" data-testid="false-positive" />
                同时标记误报
              </label>
              <button class="btn primary" type="button" data-testid="correct-btn" @click="correct">
                提交更正处置
              </button>
            </div>
          </template>
          <p v-else class="empty-state">请先在左侧选择一个价格事件</p>
        </article>
      </div>
    </div>

    <!-- 告警核对单 -->
    <div v-if="activeTab === 'checklist'" class="tab-panel">
      <template v-if="checklist">
        <article class="panel">
          <h3>事件判定（报价快照 × 规则版本，同口径不拼比）</h3>
          <table class="data-table">
            <thead>
              <tr><th>判定编号</th><th>规则/版本</th><th>阈值</th><th>判定口径</th><th>结论</th><th>更正状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="j in checklist.judgements" :key="j.id">
                <td>{{ j.判定编号 }}</td>
                <td>{{ j.规则名称 }} / {{ j.规则版本 }}</td>
                <td>{{ j.规则阈值 }}</td>
                <td>{{ j.判定口径 }}</td>
                <td :class="j.触发 ? 'tone-danger' : 'tone-ok'">{{ j.判定结论 }}</td>
                <td>{{ j.更正状态 || '—' }}</td>
              </tr>
              <tr v-if="!checklist.judgements.length"><td colspan="6" class="empty-state">尚未判定</td></tr>
            </tbody>
          </table>
        </article>

        <article class="panel">
          <h3>交付状态与收件凭据（未知不算成功）</h3>
          <table class="data-table">
            <thead>
              <tr>
                <th>交付编号</th><th>类型</th><th>收件方</th><th>交付状态</th><th>回执</th><th>证据</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in checklist.deliveries" :key="String(d.交付编号)"
                  :data-testid="`delivery-${String(d.交付编号)}`">
                <td>{{ d.交付编号 }}</td>
                <td>{{ d.交付类型 }}</td>
                <td>{{ d.收件方 }}</td>
                <td :class="deliveryTone(String(d.交付状态))">{{ d.交付状态 }}</td>
                <td>{{ d.receipt ? `${d.receipt.回执类型} / ${d.receipt.回执状态}` : '无回执' }}</td>
                <td>{{ d.证据可回查 ? '可回查' : '不可回查' }}</td>
                <td class="row-actions">
                  <button class="link" type="button" :data-testid="`query-${String(d.交付编号)}`"
                          @click="queryDelivery(String(d.交付编号))">查凭据</button>
                  <button class="link" type="button"
                          :data-testid="`receipt-${String(d.交付编号)}`"
                          @click="registerReceipt(String(d.交付编号))">补回执</button>
                </td>
              </tr>
              <tr v-if="!checklist.deliveries.length"><td colspan="7" class="empty-state">暂无交付</td></tr>
            </tbody>
          </table>
        </article>

        <article class="panel">
          <h3>需要跟进的更正</h3>
          <table class="data-table">
            <thead>
              <tr><th>更正编号</th><th>类型</th><th>原幅度→更正后</th><th>处置状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in checklist.corrections" :key="c.id" :data-testid="`correction-${c.id}`">
                <td>{{ c.更正编号 }}</td>
                <td>{{ c.处置类型 }}</td>
                <td>{{ c.原变化幅度 }} → {{ c.更正后幅度 }}</td>
                <td>{{ c.处置状态 }}</td>
                <td class="row-actions">
                  <button class="link" type="button" :disabled="c.处置状态 !== '处置待复核'"
                          :data-testid="`confirm-correction-${c.id}`"
                          @click="confirmCorrection(c.id)">确认处置</button>
                  <button class="link" type="button"
                          :disabled="c.处置状态 === '处置待复核'"
                          :data-testid="`send-correction-${c.id}`"
                          @click="dispatchCorrection(c.id)">
                    {{ c.更正后是否仍触发 ? '发更正通知' : '发撤回通知' }}
                  </button>
                </td>
              </tr>
              <tr v-if="!checklist.corrections.length"><td colspan="5" class="empty-state">暂无更正处置</td></tr>
            </tbody>
          </table>
        </article>

        <article class="panel review-panel" data-testid="review-panel">
          <h3>复核资格</h3>
          <p class="eligibility" :class="checklist.review_eligibility === '可复核' ? 'tone-ok' : 'tone-warn'">
            {{ checklist.review_eligibility }}
          </p>
          <ul v-if="checklist.blocked_reasons.length" class="block-list">
            <li v-for="reason in checklist.blocked_reasons" :key="reason">{{ reason }}</li>
          </ul>
          <dl class="kv" v-if="checklist.history">
            <dt>关联流水</dt><dd>{{ checklist.history.价格事件 }} / {{ checklist.history.告警判定 || '未关联判定' }}</dd>
            <dt>流水交付状态</dt><dd>{{ checklist.history.交付状态 }}</dd>
          </dl>
        </article>
      </template>
      <p v-else class="empty-state">请先在「事件与判定」页选择价格事件</p>
    </div>

    <!-- 可操作项 / 误报入口 -->
    <div v-if="activeTab === 'actions'" class="tab-panel">
      <article class="panel">
        <h3>通知侧可操作项（更正确认后仍保留的待办）</h3>
        <table class="data-table">
          <thead><tr><th>类型</th><th>编号</th><th>事件</th><th>收件方</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="(item, index) in actionItems" :key="`${item.编号}-${index}`"
                :data-testid="`action-item-${index}`">
              <td>{{ item.类型 }}</td><td>{{ item.编号 }}</td><td>{{ item.事件编号 }}</td>
              <td>{{ item.收件方 || '—' }}</td><td>{{ item.说明 }}</td>
            </tr>
            <tr v-if="!actionItems.length"><td colspan="5" class="empty-state">没有待处理项</td></tr>
          </tbody>
        </table>
      </article>

      <article class="panel" data-testid="false-positive-panel">
        <h3>误报入口：按事件核对价格数据与对外确认</h3>
        <label class="inline-field">
          事件ID
          <input v-model="fpEventId" class="small-input" data-testid="fp-event-id" />
        </label>
        <button class="btn primary" type="button" data-testid="fp-check-btn" @click="falsePositiveCheck">
          核对误报数据
        </button>
        <template v-if="fpResult">
          <dl class="kv">
            <template v-for="(value, key) in fpResult.event" :key="String(key)">
              <dt>{{ String(key) }}</dt><dd>{{ value ?? '—' }}</dd>
            </template>
          </dl>
          <table class="data-table">
            <thead><tr><th>交付编号</th><th>收件方</th><th>类型</th><th>交付状态</th><th>对外确认</th><th>证据</th></tr></thead>
            <tbody>
              <tr v-for="d in fpResult.deliveries" :key="String(d.交付编号)">
                <td>{{ d.交付编号 }}</td><td>{{ d.收件方 }}</td><td>{{ d.交付类型 }}</td>
                <td :class="deliveryTone(String(d.交付状态))">{{ d.交付状态 }}</td>
                <td :class="String(d.对外确认).includes('未知') ? 'tone-warn' : ''">{{ d.对外确认 }}</td>
                <td>{{ d.证据可回查 ? '可回查' : '不可回查' }}</td>
              </tr>
              <tr v-if="!fpResult.deliveries.length"><td colspan="6" class="empty-state">该事件暂无对外交付</td></tr>
            </tbody>
          </table>
          <p class="eligibility">复核资格：{{ fpResult.review_eligibility }}</p>
        </template>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { alertOpsApi } from '@/api/alert-ops'
import type {
  ActionItem,
  Checklist,
  Delivery,
  PriceEvent,
} from '@/api/alert-ops'

const tabs = [
  { key: 'events', label: '事件与判定' },
  { key: 'checklist', label: '告警核对单' },
  { key: 'actions', label: '可操作项 / 误报入口' },
] as const

const activeTab = ref<(typeof tabs)[number]['key']>('events')
const events = ref<PriceEvent[]>([])
const selectedEventId = ref<number | null>(null)
const checklist = ref<Checklist | null>(null)
const actionItems = ref<ActionItem[]>([])
const message = ref('')
const messageKind = ref('tone-ok')

const ruleId = ref('4')
const simulate = ref<'normal' | 'receipt_lost' | 'not_received'>('receipt_lost')
const correctedPrice = ref('98')
const correctionReason = ref('D131 采集价格更正')
const falsePositive = ref(true)
const fpEventId = ref('1')
const fpResult = ref<Record<string, any> | null>(null)

const selectedEvent = computed(() => events.value.find((e) => e.id === selectedEventId.value) ?? null)
const selectedJudgements = computed(() => checklist.value?.judgements ?? [])

const pendingJudgeCount = computed(
  () => actionItems.value.filter((i) => i.类型 === '事件待判定').length,
)
const pendingDeliveryCount = computed(
  () => actionItems.value.filter((i) => i.类型 === '交付待确认').length,
)
const pendingCorrectionCount = computed(
  () => actionItems.value.filter((i) => i.类型 !== '交付待确认' && i.类型 !== '事件待判定').length,
)

function notify(text: string, ok = true) {
  message.value = text
  messageKind.value = ok ? 'tone-ok' : 'tone-danger'
}

function deliveryTone(state: string): string {
  if (state === '已确认送达') return 'tone-ok'
  if (state === '已回撤') return 'tone-muted'
  if (state === '未送达') return 'tone-danger'
  return 'tone-warn'
}

async function loadEvents() {
  const payload = await alertOpsApi.listEvents()
  events.value = payload.items
  if (selectedEventId.value === null && events.value.length) {
    await selectEvent(events.value[0].id)
  }
}

async function loadActionItems() {
  const payload = await alertOpsApi.actionItems()
  actionItems.value = payload.items
}

async function reloadAll() {
  await loadEvents()
  await loadActionItems()
  if (selectedEventId.value !== null) {
    checklist.value = await alertOpsApi.checklist(selectedEventId.value)
  }
  notify('两页数据已刷新')
}

async function selectEvent(id: number) {
  selectedEventId.value = id
  fpEventId.value = String(id)
  checklist.value = await alertOpsApi.checklist(id)
  activeTab.value = 'checklist'
}

async function evaluate() {
  if (selectedEventId.value === null) return
  const result = await alertOpsApi.evaluate(selectedEventId.value, Number(ruleId.value))
  notify(result.message + (result.idempotent ? '（幂等未重复确认）' : ''), result.ok)
  await reloadAll()
}

async function dispatch(judgementId: number) {
  const result = await alertOpsApi.dispatch({
    judgement_id: judgementId,
    recipients: [],
    simulate: simulate.value,
  })
  notify(result.message, result.ok)
  await reloadAll()
}

async function correct() {
  if (selectedEventId.value === null) return
  const result = await alertOpsApi.correct(selectedEventId.value, {
    corrected_price: Number(correctedPrice.value),
    reason: correctionReason.value,
    false_positive: falsePositive.value,
  })
  notify(result.message, result.ok)
  await reloadAll()
}

async function queryDelivery(ref: string) {
  const result = await alertOpsApi.queryDelivery(ref)
  const receiptText = result.receipt
    ? `${result.receipt.回执类型} / ${result.receipt.回执状态} / ${result.receipt.备注}`
    : '无收件凭据'
  notify(`${ref}：${result.delivery.交付状态}；${receiptText}`, result.ok)
}

async function registerReceipt(ref: string) {
  const result = await alertOpsApi.registerReceipt(ref, {
    receipt_type: '送达回执',
    remark: '值班员补登记收件凭据',
  })
  notify(result.message, result.ok)
  await reloadAll()
}

async function confirmCorrection(correctionId: number) {
  const result = await alertOpsApi.confirmCorrection(correctionId)
  const remaining = (result.remaining_actions ?? []).map((i) => i.类型).join('、') || '无'
  notify(`${result.message}；通知侧剩余可操作项：${remaining}`, result.ok)
  await reloadAll()
}

async function dispatchCorrection(correctionId: number) {
  const result = await alertOpsApi.dispatchCorrection(correctionId, 'normal')
  const skipped = (result.skipped ?? []).length
  notify(
    result.ok
      ? `${result.message}；跳过未证实收到 ${skipped} 个收件人`
      : result.message,
    result.ok,
  )
  await reloadAll()
}

async function falsePositiveCheck() {
  fpResult.value = await alertOpsApi.falsePositiveCheck(Number(fpEventId.value)) as never
  notify('误报核对数据已加载')
}

onMounted(reloadAll)
</script>

<style scoped>
.alert-ops .tabs { display: flex; gap: 8px; margin: 8px 0 12px; }
.tab { border: 1px solid var(--border); background: #fff; border-radius: 6px 6px 0 0; padding: 8px 16px; cursor: pointer; font-size: 13px; }
.tab.active { background: var(--brand); color: #fff; border-color: var(--brand); }
.panel-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 12px; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.panel h3 { margin: 0 0 10px; font-size: 14px; }
.action-block { border-top: 1px dashed var(--border); padding-top: 10px; margin-top: 10px; }
.action-block h4 { margin: 0 0 8px; font-size: 13px; color: var(--muted); }
.inline-field { display: inline-flex; align-items: center; gap: 6px; margin-right: 10px; font-size: 13px; }
.small-input { width: 90px; padding: 6px; border: 1px solid var(--border); border-radius: 6px; }
.inline-field input:not(.small-input), .inline-field select { padding: 6px; border: 1px solid var(--border); border-radius: 6px; }
.kv { display: grid; grid-template-columns: 90px 1fr; gap: 6px 10px; margin: 0; font-size: 13px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
.inline-message { padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.tone-ok { color: #15803d; }
.tone-danger { color: #b42318; }
.tone-warn { color: #b45309; }
.tone-muted { color: var(--muted); }
.inline-message.tone-ok { background: #f0fdf4; }
.inline-message.tone-danger { background: #fef2f2; }
.eligibility { font-weight: 600; font-size: 14px; }
.block-list { margin: 8px 0; padding-left: 18px; color: #b45309; font-size: 13px; }
tr.selected { background: #eff6ff; }
button:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
