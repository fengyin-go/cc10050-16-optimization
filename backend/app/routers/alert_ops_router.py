"""变价告警处置闭环路由：运营用同口径入口处理待送告警、交付凭据与更正。

与通用资源路由并列，所有业务判断都在 alert_ops 服务内完成，路由层只做参数解析。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.alert_ops import alert_ops
from app.store import store

router = APIRouter(prefix="/api/alert-ops", tags=["变价告警处置"])


class EventCreatePayload(BaseModel):
    goods_id: str
    baseline: float
    collected: float
    quote_source: str = ""
    recipients: list[str] = Field(default_factory=list)


class EvaluatePayload(BaseModel):
    rule_id: int


class DispatchPayload(BaseModel):
    judgement_id: int
    recipients: list[str] = Field(default_factory=list)
    simulate: str = "normal"


class ReceiptPayload(BaseModel):
    receipt_type: str = "送达回执"
    remark: str = ""


class CorrectPayload(BaseModel):
    corrected_price: float
    reason: str
    false_positive: bool = False


class CorrectionDispatchPayload(BaseModel):
    simulate: str = "normal"


class RuleRevisePayload(BaseModel):
    new_threshold: str
    reason: str = ""


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int) -> dict[str, Any]:
    rule = store.find("price_alert", rule_id)
    if rule is None:
        return {"ok": False, "message": f"告警规则 {rule_id} 不存在"}
    return {"ok": True, "message": "告警规则详情", "rule": rule}


@router.post("/rules/{rule_id}/revise")
def revise_rule(rule_id: int, payload: RuleRevisePayload) -> dict[str, Any]:
    from app.services.price_alert import PriceAlertService

    return PriceAlertService().revise_rule(rule_id, payload.new_threshold, payload.reason)


@router.get("/events")
def list_events() -> dict[str, Any]:
    events = alert_ops.list_events()
    return {"ok": True, "message": "价格事件列表", "items": events, "total": len(events)}


@router.post("/events")
def create_event(payload: EventCreatePayload) -> dict[str, Any]:
    return alert_ops.create_event(
        payload.goods_id, payload.baseline, payload.collected,
        payload.quote_source, payload.recipients,
    )


@router.get("/events/{event_id}")
def get_event(event_id: int) -> dict[str, Any]:
    event = alert_ops.get_event(event_id)
    if event is None:
        return {"ok": False, "message": f"价格事件 {event_id} 不存在"}
    return {"ok": True, "message": "价格事件详情", "event": event}


@router.post("/events/{event_id}/evaluate")
def evaluate_event(event_id: int, payload: EvaluatePayload) -> dict[str, Any]:
    return alert_ops.evaluate_event(event_id, payload.rule_id)


@router.post("/events/{event_id}/correct")
def correct_event(event_id: int, payload: CorrectPayload) -> dict[str, Any]:
    return alert_ops.correct_event(
        event_id, payload.corrected_price, payload.reason, payload.false_positive,
    )


@router.get("/events/{event_id}/checklist")
def checklist(event_id: int) -> dict[str, Any]:
    return alert_ops.checklist(event_id)


@router.get("/events/{event_id}/false-positive-check")
def false_positive_check(event_id: int) -> dict[str, Any]:
    return alert_ops.false_positive_check(event_id)


@router.get("/judgements")
def list_judgements() -> dict[str, Any]:
    rows = store.rows("alert_judgement")
    return {"ok": True, "message": "事件判定列表", "items": rows, "total": len(rows)}


@router.post("/judgements/{judgement_id}/reconcile")
def reconcile(judgement_id: int) -> dict[str, Any]:
    return alert_ops.reconcile(judgement_id)


@router.post("/deliveries")
def dispatch(payload: DispatchPayload) -> dict[str, Any]:
    return alert_ops.dispatch(
        payload.judgement_id, payload.recipients or None, payload.simulate,
    )


@router.get("/deliveries/{delivery_ref}")
def query_delivery(delivery_ref: str) -> dict[str, Any]:
    return alert_ops.query_delivery(delivery_ref)


@router.post("/deliveries/{delivery_ref}/receipt")
def register_receipt(delivery_ref: str, payload: ReceiptPayload) -> dict[str, Any]:
    return alert_ops.register_receipt(delivery_ref, payload.receipt_type, payload.remark)


@router.get("/receipts")
def list_receipts() -> dict[str, Any]:
    rows = store.rows("alert_receipt")
    return {"ok": True, "message": "收件回执列表", "items": rows, "total": len(rows)}


@router.get("/corrections")
def list_corrections() -> dict[str, Any]:
    rows = store.rows("alert_correction")
    return {"ok": True, "message": "更正处置列表", "items": rows, "total": len(rows)}


@router.post("/corrections/{correction_id}/confirm")
def confirm_correction(correction_id: int) -> dict[str, Any]:
    return alert_ops.confirm_correction(correction_id)


@router.post("/corrections/{correction_id}/dispatch")
def dispatch_correction(correction_id: int, payload: CorrectionDispatchPayload) -> dict[str, Any]:
    return alert_ops.dispatch_correction(correction_id, payload.simulate)


@router.get("/action-items")
def actionable_items(event_id: int | None = None) -> dict[str, Any]:
    return alert_ops.actionable_items(event_id)
