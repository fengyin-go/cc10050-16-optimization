import { request } from './client'

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

async function postJson<T>(path: string, payload: Record<string, unknown> = {}): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return (await response.json()) as T
}

export type PriceEvent = {
  id: number
  event_no: string
  status: string
  pending: boolean
  商品标识: string
  基线价格: number
  采集价格: number
  变化幅度: string
  报价来源: string
  发现时间: string
  关联流水: number
  更正后价格: number | null
  更正原因: string
  更正时间: string
  复核结论: string
  默认收件方: string[]
}

export type Judgement = {
  id: number
  判定编号: string
  价格事件: number
  事件编号: string
  商品标识: string
  规则ID: number
  规则名称: string
  规则版本: string
  规则阈值: string
  基线快照: number
  采集快照: number
  变化幅度: string
  判定口径: string
  触发: boolean
  判定结论: string
  更正状态: string
  判定时间: string
  status: string
}

export type Delivery = Record<string, string | number | boolean | null> & {
  交付编号: string
  判定编号: string
  事件编号: string
  收件方: string
  交付类型: string
  交付状态: string
  证据可回查: boolean
  故障模式: string
  receipt?: Receipt
}

export type Receipt = {
  回执编号: string
  交付编号: string
  收件方: string
  回执类型: string
  回执状态: string
  证据可回查: boolean
  备注: string
  登记时间: string
}

export type Correction = {
  id: number
  更正编号: string
  价格事件: number
  事件编号: string
  基线快照: number
  原采集快照: number
  更正后快照: number
  原变化幅度: string
  更正后幅度: string
  规则版本: string
  更正后是否仍触发: boolean
  处置类型: string
  误报标记: boolean
  处置状态: string
  原因: string
  关联判定编号: string
  更正时间: string
}

export type Checklist = {
  ok: boolean
  message: string
  event: PriceEvent
  judgements: Judgement[]
  deliveries: Delivery[]
  corrections: Correction[]
  history: Record<string, string | number | boolean> | null
  review_eligibility: string
  blocked_reasons: string[]
}

export type ActionItem = {
  类型: string
  编号: string
  事件编号: string
  收件方: string
  说明: string
}

export const alertOpsApi = {
  listEvents: () => getJson<{ items: PriceEvent[] }>('/api/alert-ops/events'),
  createEvent: (payload: {
    goods_id: string
    baseline: number
    collected: number
    quote_source: string
    recipients: string[]
  }) => postJson<{ ok: boolean; message: string; event?: PriceEvent }>('/api/alert-ops/events', payload),
  evaluate: (eventId: number, ruleId: number) =>
    postJson<{ ok: boolean; message: string; judgement?: Judgement; idempotent?: boolean }>(
      `/api/alert-ops/events/${eventId}/evaluate`,
      { rule_id: ruleId },
    ),
  correct: (
    eventId: number,
    payload: { corrected_price: number; reason: string; false_positive: boolean },
  ) => postJson<{ ok: boolean; message: string; correction?: Correction }>(
    `/api/alert-ops/events/${eventId}/correct`,
    payload,
  ),
  checklist: (eventId: number) =>
    getJson<Checklist>(`/api/alert-ops/events/${eventId}/checklist`),
  falsePositiveCheck: (eventId: number) =>
    getJson<Record<string, unknown>>(`/api/alert-ops/events/${eventId}/false-positive-check`),
  dispatch: (payload: { judgement_id: number; recipients: string[]; simulate: string }) =>
    postJson<{ ok: boolean; message: string; deliveries?: Delivery[] }>(
      '/api/alert-ops/deliveries',
      payload,
    ),
  queryDelivery: (ref: string) =>
    getJson<{ ok: boolean; message: string; delivery: Delivery; receipt: Receipt | null }>(
      `/api/alert-ops/deliveries/${encodeURIComponent(ref)}`,
    ),
  registerReceipt: (ref: string, payload: { receipt_type: string; remark: string }) =>
    postJson<{ ok: boolean; message: string }>(
      `/api/alert-ops/deliveries/${encodeURIComponent(ref)}/receipt`,
      payload,
    ),
  reconcile: (judgementId: number) =>
    postJson<{ ok: boolean; message: string; breakdown: Record<string, number>; unknown_deliveries: string[] }>(
      `/api/alert-ops/judgements/${judgementId}/reconcile`,
    ),
  confirmCorrection: (correctionId: number) =>
    postJson<{ ok: boolean; message: string; remaining_actions?: ActionItem[] }>(
      `/api/alert-ops/corrections/${correctionId}/confirm`,
    ),
  dispatchCorrection: (correctionId: number, simulate: string) =>
    postJson<{ ok: boolean; message: string; sent?: Delivery[]; skipped?: unknown[] }>(
      `/api/alert-ops/corrections/${correctionId}/dispatch`,
      { simulate },
    ),
  actionItems: (eventId?: number) =>
    getJson<{ ok: boolean; count: number; items: ActionItem[] }>(
      `/api/alert-ops/action-items${eventId ? `?event_id=${eventId}` : ''}`,
    ),
}
