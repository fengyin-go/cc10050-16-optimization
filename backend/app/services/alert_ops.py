"""变价事件处置待送告警领域服务。

把原先“只做判定和写流水”的告警链路升级为可核对的处置闭环：

1. 事件判定引用具体报价快照与规则版本，同一价格事件 × 规则版本只确认一次，
   且始终用同一基线口径计算，禁止拿修订前后的不同金额拼比；
2. 交付能力可查询收件凭据（回执），明确区分确实投递、回撤、未送达与仍待复核，
   恢复期间未知状态一律不算成功；
3. 更正/误报处置与原价格事件、原告警判定关联，只对真正收到过旧告警的收件人
   发出更正或撤回，旧版超阈值告警在更正后不得再发送；
4. 变价历史流水承载显式交付状态与复核资格，不再靠“当前是否还有告警”推断旧交付。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.store import store

# 交付状态：未知绝不并入成功
DLV_PENDING = "已发起待确认"
DLV_DELIVERED = "已确认送达"
DLV_MISSED = "未送达"
DLV_WITHDRAWN = "已回撤"

# 流水侧交付归档口径
HIST_DELIVERED = "确实投递"
HIST_WITHDRAWN = "已回撤"
HIST_REVIEW = "待复核"
HIST_PARTIAL = "部分投递"
HIST_FAILED = "未送达（交付失败）"
HIST_NA = "未涉及交付"

# 复核资格
ELIG_PENDING_JUDGE = "待判定"
ELIG_BLOCKED_UNKNOWN = "暂缓复核（交付未知）"
ELIG_READY = "可复核"
ELIG_NA = "无需复核"

RECEIPT_QUERYABLE = "可查询"
RECEIPT_MISSING = "不可查询"

CORRECTION_OPEN = "处置待复核"
CORRECTION_CONFIRMED = "处置已确认"
CORRECTION_SENT = "更正已送达"
WITHDRAW_SENT = "撤回已送达"

FAULT_MODES = {"normal", "receipt_lost", "not_received"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _ratio(base: float, current: float) -> float:
    return (current - base) / base


class AlertOpsService:
    """变价告警处置闭环服务，状态流转全部集中在本服务内完成。"""

    # ---------------- 价格事件 ----------------

    def create_event(
        self,
        goods_id: str,
        baseline: float,
        collected: float,
        quote_source: str,
        recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        if baseline <= 0:
            return {"ok": False, "message": "基线价格必须为正数，无法建立同口径判定"}
        event_id = _next_id("price_event")
        history_id = _next_id("change_history")
        ratio = _ratio(baseline, collected)
        event_no = f"EVT-{goods_id}-{event_id:02d}"
        event = {
            "id": event_id,
            "event_no": event_no,
            "status": "待复核",
            "pending": True,
            "abnormal": False,
            "商品标识": goods_id,
            "基线价格": baseline,
            "采集价格": collected,
            "变化幅度": f"{ratio:.2%}",
            "报价来源": quote_source,
            "发现时间": _now(),
            "关联流水": history_id,
            "更正后价格": None,
            "更正原因": "",
            "更正时间": "",
            "复核结论": "",
            "默认收件方": recipients or [],
        }
        store.rows("price_event").append(event)
        history = {
            "id": history_id,
            "status": "待核实",
            "pending": True,
            "abnormal": False,
            "商品标识": goods_id,
            "旧价格": baseline,
            "新价格": collected,
            "变化幅度": f"{ratio:.2%}",
            "发现时间": event["发现时间"],
            "价格事件": event_no,
            "告警判定": "",
            "交付状态": HIST_REVIEW,
            "复核资格": ELIG_PENDING_JUDGE,
        }
        store.rows("change_history").append(history)
        return {"ok": True, "message": "价格事件已登记，等待同口径判定", "event": event}

    def list_events(self) -> list[dict[str, Any]]:
        return store.rows("price_event")

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        return store.find("price_event", event_id)

    def _find_event_by_no(self, event_no: str) -> dict[str, Any] | None:
        for event in store.rows("price_event"):
            if event.get("event_no") == event_no:
                return event
        return None

    # ---------------- 同口径事件判定（幂等） ----------------

    def evaluate_event(self, event_id: int, rule_id: int) -> dict[str, Any]:
        event = store.find("price_event", event_id)
        if event is None:
            return {"ok": False, "message": f"价格事件 {event_id} 不存在"}
        rule = store.find("price_alert", rule_id)
        if rule is None or rule.get("status") != "已生效":
            return {"ok": False, "message": "告警规则不存在或未生效，不能引用判定"}
        rule_version = str(rule.get("规则版本") or "v1")

        # 幂等：同一价格事件与告警规则版本的消费只确认一次
        for judgement in store.rows("alert_judgement"):
            if int(judgement.get("价格事件", 0)) == event_id and \
                    str(judgement.get("规则版本")) == rule_version and \
                    int(judgement.get("规则ID", 0)) == rule_id:
                return {
                    "ok": True,
                    "message": "同一价格事件与规则版本已判定，沿用既有判定不重复确认",
                    "judgement": judgement,
                    "idempotent": True,
                }

        # 同口径：始终引用事件登记时的基线/采集报价快照，更正价不参与本次拼比
        baseline = float(event["基线价格"])
        collected = float(event["采集价格"])
        threshold_text = str(rule.get("价差阈值", "0%"))
        try:
            threshold = float(threshold_text.rstrip("%"))
        except ValueError:
            return {"ok": False, "message": f"规则阈值 {threshold_text} 数值无效"}
        ratio = _ratio(baseline, collected)
        triggered = abs(ratio) * 100 >= threshold

        judgement_id = _next_id("alert_judgement")
        judgement_no = f"JUDG-{judgement_id:04d}"
        judgement = {
            "id": judgement_id,
            "判定编号": judgement_no,
            "价格事件": event_id,
            "事件编号": event["event_no"],
            "商品标识": event["商品标识"],
            "规则ID": rule_id,
            "规则名称": rule.get("规则名称", ""),
            "规则版本": rule_version,
            "规则阈值": threshold_text,
            "基线快照": baseline,
            "采集快照": collected,
            "变化幅度": f"{ratio:.2%}",
            "判定口径": f"{baseline:g}→{collected:g}（{event['event_no']} / {rule.get('规则名称', '')} {rule_version}）",
            "触发": triggered,
            "判定结论": "触发告警" if triggered else "未触发",
            "更正状态": "",
            "判定时间": _now(),
            "status": "已判定",
            "pending": not triggered,
            "abnormal": triggered,
        }
        store.rows("alert_judgement").append(judgement)

        history = self._linked_history(event)
        if history is not None:
            history["告警判定"] = judgement_no
            history["复核资格"] = HIST_REVIEW if triggered else ELIG_READY
        return {
            "ok": True,
            "message": "同口径判定完成" + ("，触发待送告警" if triggered else "，未达阈值"),
            "judgement": judgement,
            "idempotent": False,
        }

    # ---------------- 告警交付与收件凭据 ----------------

    def dispatch(
        self,
        judgement_id: int,
        recipients: list[str] | None = None,
        simulate: str = "normal",
    ) -> dict[str, Any]:
        if simulate not in FAULT_MODES:
            return {"ok": False, "message": f"未知故障模拟类型：{simulate}"}
        judgement = store.find("alert_judgement", judgement_id)
        if judgement is None:
            return {"ok": False, "message": "告警判定不存在"}
        if not judgement.get("触发"):
            return {"ok": False, "message": "该判定未触发告警，没有待送通知"}
        event = store.find("price_event", int(judgement["价格事件"]))
        if event is None:
            return {"ok": False, "message": "判定关联的价格事件不存在"}
        # 更正后旧版告警不得再发送
        if event.get("status") == "已更正":
            return {
                "ok": False,
                "message": (
                    f"事件 {event['event_no']} 已更正，旧版 {judgement['变化幅度']} 告警禁止再发送；"
                    "如需对外说明请走更正/撤回通知"
                ),
            }

        target_recipients = recipients or list(event.get("默认收件方") or [])
        if not target_recipients:
            return {"ok": False, "message": "没有可投递的收件方"}

        deliveries: list[dict[str, Any]] = []
        for recipient in target_recipients:
            delivery_id = _next_id("alert_delivery")
            delivery_no = f"DLV-{delivery_id:04d}"
            receipt_id = _next_id("alert_receipt")
            receipt_no = f"RCP-{receipt_id:04d}"
            if simulate == "normal":
                delivery_state = DLV_DELIVERED
                receipt_state = RECEIPT_QUERYABLE
                remark = "收件方已返回送达回执"
            elif simulate == "receipt_lost":
                delivery_state = DLV_PENDING
                receipt_state = RECEIPT_MISSING
                remark = "收件方实际已收到，回执丢失，暂不可回查"
            else:
                delivery_state = DLV_PENDING
                receipt_state = RECEIPT_MISSING
                remark = "收件方未收到，无送达证据，暂不可回查"
            receipt = {
                "id": receipt_id,
                "回执编号": receipt_no,
                "交付编号": delivery_no,
                "判定编号": judgement["判定编号"],
                "收件方": recipient,
                "回执类型": "送达回执",
                "回执状态": receipt_state,
                "证据可回查": receipt_state == RECEIPT_QUERYABLE,
                "备注": remark,
                "登记时间": _now(),
            }
            delivery = {
                "id": delivery_id,
                "交付编号": delivery_no,
                "判定编号": judgement["判定编号"],
                "价格事件": event["id"],
                "事件编号": event["event_no"],
                "商品标识": event["商品标识"],
                "收件方": recipient,
                "通知版本": judgement["规则版本"],
                "交付类型": "变价告警",
                "旧幅度": judgement["变化幅度"],
                "发起时间": _now(),
                "交付状态": delivery_state,
                "回执编号": receipt_no,
                "证据可回查": receipt_state == RECEIPT_QUERYABLE,
                "故障模式": simulate,
                "关联更正编号": "",
            }
            store.rows("alert_receipt").append(receipt)
            store.rows("alert_delivery").append(delivery)
            deliveries.append(delivery)

        self._recompute_delivery(event["id"])
        return {
            "ok": True,
            "message": f"已按 {simulate} 模式发起 {len(deliveries)} 条交付",
            "deliveries": deliveries,
        }

    def query_delivery(self, delivery_ref: str) -> dict[str, Any]:
        """交付能力：按交付编号或 id 查询收件凭据。"""
        delivery = None
        for row in store.rows("alert_delivery"):
            if row.get("交付编号") == delivery_ref or str(row.get("id")) == str(delivery_ref):
                delivery = row
                break
        if delivery is None:
            return {"ok": False, "message": f"交付记录 {delivery_ref} 不存在"}
        receipt = next(
            (r for r in store.rows("alert_receipt") if r.get("交付编号") == delivery["交付编号"]),
            None,
        )
        return {
            "ok": True,
            "message": "收件凭据查询完成",
            "delivery": delivery,
            "receipt": receipt,
        }

    def register_receipt(
        self,
        delivery_ref: str,
        receipt_type: str = "送达回执",
        remark: str = "",
    ) -> dict[str, Any]:
        found = self.query_delivery(delivery_ref)
        if not found["ok"]:
            return found
        delivery: dict[str, Any] = found["delivery"]
        receipt: dict[str, Any] | None = found["receipt"]
        if receipt is None:
            receipt_id = _next_id("alert_receipt")
            receipt = {
                "id": receipt_id,
                "回执编号": f"RCP-{receipt_id:04d}",
                "交付编号": delivery["交付编号"],
                "判定编号": delivery.get("判定编号", ""),
                "收件方": delivery["收件方"],
                "回执类型": receipt_type,
                "回执状态": RECEIPT_QUERYABLE,
                "证据可回查": True,
                "备注": remark,
                "登记时间": _now(),
            }
            store.rows("alert_receipt").append(receipt)
            delivery["回执编号"] = receipt["回执编号"]
        else:
            receipt["回执类型"] = receipt_type
            receipt["回执状态"] = RECEIPT_QUERYABLE
            receipt["证据可回查"] = True
            receipt["备注"] = (str(receipt.get("备注") or "") + " / " + remark).strip(" /")
            receipt["登记时间"] = _now()

        if receipt_type == "送达回执" and delivery["交付状态"] == DLV_PENDING:
            delivery["交付状态"] = DLV_DELIVERED
        elif receipt_type == "撤回回执":
            delivery["交付状态"] = DLV_WITHDRAWN
        delivery["证据可回查"] = True
        self._recompute_delivery(int(delivery["价格事件"]))
        # 回执补齐后恢复复核资格，已确认的更正处置同步收敛流水基础状态
        event = store.find("price_event", int(delivery["价格事件"]))
        if event is not None and self._review_eligibility(event["id"]) == ELIG_READY:
            history = self._linked_history(event)
            correction = next(
                (c for c in store.rows("alert_correction")
                 if int(c.get("价格事件", 0)) == event["id"]
                 and c.get("处置状态") != CORRECTION_OPEN),
                None,
            )
            if history is not None and correction is not None:
                history["status"] = "误报" if correction.get("误报标记") else "已确认"
                history["pending"] = False
                history["复核资格"] = ELIG_READY
        return {"ok": True, "message": "回执已补登记，交付状态已刷新", "delivery": delivery, "receipt": receipt}

    def reconcile(self, judgement_id: int) -> dict[str, Any]:
        """核对单：按收件凭据重算交付状态，未知状态单列，绝不计入成功。"""
        judgement = store.find("alert_judgement", judgement_id)
        if judgement is None:
            return {"ok": False, "message": "告警判定不存在"}
        deliveries = [
            d for d in store.rows("alert_delivery")
            if d.get("判定编号") == judgement["判定编号"] and d.get("交付类型") == "变价告警"
        ]
        breakdown = {
            DLV_DELIVERED: 0,
            DLV_PENDING: 0,
            DLV_MISSED: 0,
            DLV_WITHDRAWN: 0,
        }
        for delivery in deliveries:
            breakdown[delivery["交付状态"]] = breakdown.get(delivery["交付状态"], 0) + 1
        unknown = [d["交付编号"] for d in deliveries if d["交付状态"] == DLV_PENDING]
        self._recompute_delivery(int(judgement["价格事件"]))
        return {
            "ok": True,
            "message": "交付核对完成：未知状态保持待复核，未计入成功",
            "判定编号": judgement["判定编号"],
            "total": len(deliveries),
            "breakdown": breakdown,
            "unknown_deliveries": unknown,
            "evidence_missing": [
                d["交付编号"] for d in deliveries if not d.get("证据可回查")
            ],
        }

    # ---------------- 价格更正 / 误报处置 ----------------

    def correct_event(
        self,
        event_id: int,
        corrected_price: float,
        reason: str,
        false_positive: bool = False,
    ) -> dict[str, Any]:
        event = store.find("price_event", event_id)
        if event is None:
            return {"ok": False, "message": f"价格事件 {event_id} 不存在"}
        if corrected_price <= 0:
            return {"ok": False, "message": "更正价格必须为正数"}

        baseline = float(event["基线价格"])
        collected = float(event["采集价格"])
        # 同口径：更正后仍以原基线核算，禁止用修订前后金额互相拼比
        new_ratio = _ratio(baseline, corrected_price)
        original_judgement = next(
            (j for j in store.rows("alert_judgement") if int(j.get("价格事件", 0)) == event_id),
            None,
        )
        rule_id = int(original_judgement["规则ID"]) if original_judgement else 0
        rule_version = str(original_judgement["规则版本"]) if original_judgement else "v1"
        threshold_text = str(original_judgement["规则阈值"]) if original_judgement else "0%"
        try:
            threshold = float(threshold_text.rstrip("%"))
        except ValueError:
            threshold = 0.0
        still_triggered = abs(new_ratio) * 100 >= threshold

        correction_id = _next_id("alert_correction")
        correction_no = f"CRT-{correction_id:04d}"
        correction = {
            "id": correction_id,
            "更正编号": correction_no,
            "价格事件": event_id,
            "事件编号": event["event_no"],
            "商品标识": event["商品标识"],
            "基线快照": baseline,
            "原采集快照": collected,
            "更正后快照": corrected_price,
            "原变化幅度": event["变化幅度"],
            "更正后幅度": f"{new_ratio:.2%}",
            "规则ID": rule_id,
            "规则版本": rule_version,
            "规则阈值": threshold_text,
            "更正后是否仍触发": still_triggered,
            "处置类型": "误报更正" if false_positive else "价格更正",
            "误报标记": false_positive,
            "处置状态": CORRECTION_OPEN,
            "原因": reason,
            "关联判定编号": original_judgement["判定编号"] if original_judgement else "",
            "更正时间": _now(),
            "status": CORRECTION_OPEN,
            "pending": True,
            "abnormal": False,
        }
        store.rows("alert_correction").append(correction)

        event["更正后价格"] = corrected_price
        event["更正原因"] = reason
        event["更正时间"] = correction["更正时间"]
        event["status"] = "已更正"
        event["复核结论"] = "误报" if false_positive else "已更正待复核"
        event["pending"] = True
        if original_judgement is not None:
            original_judgement["更正状态"] = "已挂更正，等待对外处置"

        history = self._linked_history(event)
        if history is not None:
            history["复核资格"] = self._review_eligibility(event_id)
        return {
            "ok": True,
            "message": (
                "更正已登记并按同口径复核，原超阈值告警停止再发送"
                if not still_triggered
                else "更正已登记，更正后幅度仍达阈值，需对已送达收件人发更正通知"
            ),
            "correction": correction,
        }

    def confirm_correction(self, correction_id: int) -> dict[str, Any]:
        """流水先确认更正：回执不可查也能确认判定，但交付跟进项保留。"""
        correction = store.find("alert_correction", correction_id)
        if correction is None:
            return {"ok": False, "message": "更正处置不存在"}
        if correction["处置状态"] != CORRECTION_OPEN:
            return {"ok": False, "message": f"更正已处于 {correction['处置状态']}，不能重复确认"}
        correction["处置状态"] = CORRECTION_CONFIRMED
        correction["status"] = CORRECTION_CONFIRMED
        correction["pending"] = False

        event = store.find("price_event", int(correction["价格事件"]))
        blocked_by_delivery = False
        if event is not None:
            event["pending"] = False
            event["复核结论"] = "误报" if correction.get("误报标记") else "更正已确认"
            history = self._linked_history(event)
            # 先按当前回执重算，再决定流水是否可以结单
            self._recompute_delivery(event["id"])
            if history is not None:
                # 交付未知时更正判定可先确认，但流水保持待核实/待复核，不能被错误结单
                blocked_by_delivery = self._review_eligibility(event["id"]) == ELIG_BLOCKED_UNKNOWN
                if blocked_by_delivery:
                    history["status"] = "待核实"
                    history["pending"] = True
                    history["复核资格"] = ELIG_BLOCKED_UNKNOWN
                else:
                    history["status"] = "误报" if correction.get("误报标记") else "已确认"
                    history["pending"] = False
                    history["复核资格"] = ELIG_READY

        remaining = self.actionable_items(event["id"] if event else None)
        return {
            "ok": True,
            "message": "更正处置已在流水确认，交付侧未决项继续保留",
            "correction": correction,
            "remaining_actions": remaining["items"],
        }

    def dispatch_correction(self, correction_id: int, simulate: str = "normal") -> dict[str, Any]:
        """只对真正收到过旧告警（已确认送达）的收件人发更正/撤回。"""
        if simulate not in FAULT_MODES:
            return {"ok": False, "message": f"未知故障模拟类型：{simulate}"}
        correction = store.find("alert_correction", correction_id)
        if correction is None:
            return {"ok": False, "message": "更正处置不存在"}
        if correction["处置状态"] not in {CORRECTION_CONFIRMED, CORRECTION_SENT, WITHDRAW_SENT}:
            return {"ok": False, "message": "更正处置尚未确认，不能对外发送"}
        judgement_no = correction.get("关联判定编号")
        old_deliveries = [
            d for d in store.rows("alert_delivery")
            if d.get("判定编号") == judgement_no and d.get("交付类型") == "变价告警"
        ]
        received = [d for d in old_deliveries if d["交付状态"] == DLV_DELIVERED]
        skipped = [
            {
                "交付编号": d["交付编号"],
                "收件方": d["收件方"],
                "原因": f"旧通知状态为{d['交付状态']}，未证实收到，不发更正/撤回",
            }
            for d in old_deliveries
            if d["交付状态"] != DLV_DELIVERED
        ]
        if not received:
            return {
                "ok": False,
                "message": "没有任何收件人被证实收到过旧告警，不发送更正/撤回",
                "skipped": skipped,
            }

        send_type = "更正通知" if correction["更正后是否仍触发"] else "撤回通知"
        receipt_type = "更正回执" if correction["更正后是否仍触发"] else "撤回回执"
        sent: list[dict[str, Any]] = []
        for old in received:
            delivery_id = _next_id("alert_delivery")
            delivery_no = f"DLV-{delivery_id:04d}"
            receipt_id = _next_id("alert_receipt")
            receipt_no = f"RCP-{receipt_id:04d}"
            if simulate == "normal":
                delivery_state = DLV_DELIVERED
                receipt_state = RECEIPT_QUERYABLE
            else:
                delivery_state = DLV_PENDING
                receipt_state = RECEIPT_MISSING
            receipt = {
                "id": receipt_id,
                "回执编号": receipt_no,
                "交付编号": delivery_no,
                "判定编号": judgement_no,
                "收件方": old["收件方"],
                "回执类型": receipt_type,
                "回执状态": receipt_state,
                "证据可回查": receipt_state == RECEIPT_QUERYABLE,
                "备注": f"{send_type}回执",
                "登记时间": _now(),
            }
            delivery = {
                "id": delivery_id,
                "交付编号": delivery_no,
                "判定编号": judgement_no,
                "价格事件": correction["价格事件"],
                "事件编号": correction["事件编号"],
                "商品标识": correction["商品标识"],
                "收件方": old["收件方"],
                "通知版本": correction["规则版本"],
                "交付类型": send_type,
                "旧幅度": correction["原变化幅度"],
                "新幅度": correction["更正后幅度"],
                "发起时间": _now(),
                "交付状态": delivery_state,
                "回执编号": receipt_no,
                "证据可回查": receipt_state == RECEIPT_QUERYABLE,
                "故障模式": simulate,
                "关联更正编号": correction["更正编号"],
            }
            store.rows("alert_receipt").append(receipt)
            store.rows("alert_delivery").append(delivery)
            sent.append(delivery)

        correction["处置状态"] = CORRECTION_SENT if correction["更正后是否仍触发"] else WITHDRAW_SENT
        correction["status"] = correction["处置状态"]
        self._recompute_delivery(int(correction["价格事件"]))
        return {
            "ok": True,
            "message": f"{send_type}仅发送给 {len(sent)} 个证实收到旧告警的收件人",
            "sent": sent,
            "skipped": skipped,
        }

    # ---------------- 核对单 / 误报入口 / 待办 ----------------

    def false_positive_check(self, event_id: int) -> dict[str, Any]:
        """误报入口：按事件核对价格数据与告警是否已对外确认。"""
        event = store.find("price_event", event_id)
        if event is None:
            return {"ok": False, "message": f"价格事件 {event_id} 不存在"}
        judgements = [
            j for j in store.rows("alert_judgement") if int(j.get("价格事件", 0)) == event_id
        ]
        delivery_view: list[dict[str, Any]] = []
        for judgement in judgements:
            for delivery in self._deliveries_of(judgement["判定编号"]):
                receipt = next(
                    (r for r in store.rows("alert_receipt")
                     if r.get("交付编号") == delivery["交付编号"]),
                    None,
                )
                state = delivery["交付状态"]
                if state == DLV_DELIVERED:
                    external = "已对外确认"
                elif state == DLV_PENDING:
                    external = "对外确认未知（不能视作已确认）"
                elif state == DLV_MISSED:
                    external = "未对外送达"
                else:
                    external = "已撤回"
                delivery_view.append({
                    "交付编号": delivery["交付编号"],
                    "收件方": delivery["收件方"],
                    "交付类型": delivery["交付类型"],
                    "交付状态": state,
                    "对外确认": external,
                    "证据可回查": bool(delivery.get("证据可回查")),
                    "回执状态": receipt["回执状态"] if receipt else "无回执",
                    "回执类型": receipt["回执类型"] if receipt else "",
                })
        return {
            "ok": True,
            "message": "误报核对数据已汇总",
            "event": {
                "事件编号": event["event_no"],
                "商品标识": event["商品标识"],
                "基线价格": event["基线价格"],
                "采集价格": event["采集价格"],
                "变化幅度": event["变化幅度"],
                "更正后价格": event.get("更正后价格"),
                "报价来源": event.get("报价来源"),
            },
            "judgements": [
                {
                    "判定编号": j["判定编号"],
                    "规则名称": j["规则名称"],
                    "规则版本": j["规则版本"],
                    "规则阈值": j["规则阈值"],
                    "判定口径": j["判定口径"],
                    "判定结论": j["判定结论"],
                    "更正状态": j.get("更正状态", ""),
                }
                for j in judgements
            ],
            "deliveries": delivery_view,
            "review_eligibility": self._review_eligibility(event_id),
        }

    def checklist(self, event_id: int) -> dict[str, Any]:
        """告警核对单：区分事件判定、交付状态和需要跟进的更正。"""
        event = store.find("price_event", event_id)
        if event is None:
            return {"ok": False, "message": f"价格事件 {event_id} 不存在"}
        judgements = [
            j for j in store.rows("alert_judgement") if int(j.get("价格事件", 0)) == event_id
        ]
        corrections = [
            c for c in store.rows("alert_correction") if int(c.get("价格事件", 0)) == event_id
        ]
        deliveries: list[dict[str, Any]] = []
        for judgement in judgements:
            for delivery in self._deliveries_of(judgement["判定编号"]):
                receipt = next(
                    (r for r in store.rows("alert_receipt")
                     if r.get("交付编号") == delivery["交付编号"]),
                    None,
                )
                deliveries.append({**delivery, "receipt": receipt})
        history = self._linked_history(event)
        blocked: list[str] = []
        if any(d["交付状态"] == DLV_PENDING for d in deliveries):
            blocked.append("存在收件方响应未知的交付，复核前不得把未知算作成功")
        if event.get("status") != "已更正" and any(j.get("触发") for j in judgements):
            blocked.append("告警已触发且尚未登记更正/误报处置")
        return {
            "ok": True,
            "message": "告警核对单已生成",
            "event": event,
            "judgements": judgements,
            "deliveries": deliveries,
            "corrections": corrections,
            "history": history,
            "review_eligibility": self._review_eligibility(event_id),
            "blocked_reasons": blocked,
        }

    def actionable_items(self, event_id: int | None = None) -> dict[str, Any]:
        """通知侧仍可操作项：未知交付、待确认更正、待发更正/撤回。"""
        items: list[dict[str, Any]] = []
        for delivery in store.rows("alert_delivery"):
            if event_id is not None and int(delivery.get("价格事件", 0)) != event_id:
                continue
            if delivery["交付状态"] == DLV_PENDING:
                items.append({
                    "类型": "交付待确认",
                    "编号": delivery["交付编号"],
                    "事件编号": delivery.get("事件编号"),
                    "收件方": delivery["收件方"],
                    "说明": "回执不可回查，需等待补回执或核对收件方响应",
                })
        for correction in store.rows("alert_correction"):
            if event_id is not None and int(correction.get("价格事件", 0)) != event_id:
                continue
            if correction["处置状态"] == CORRECTION_OPEN:
                items.append({
                    "类型": "更正待确认",
                    "编号": correction["更正编号"],
                    "事件编号": correction["事件编号"],
                    "收件方": "",
                    "说明": "更正处置尚未在流水确认",
                })
            elif correction["处置状态"] == CORRECTION_CONFIRMED:
                received = [
                    d for d in store.rows("alert_delivery")
                    if d.get("判定编号") == correction.get("关联判定编号")
                    and d.get("交付类型") == "变价告警"
                    and d["交付状态"] == DLV_DELIVERED
                ]
                if received:
                    items.append({
                        "类型": "更正/撤回待发送",
                        "编号": correction["更正编号"],
                        "事件编号": correction["事件编号"],
                        "收件方": "、".join(d["收件方"] for d in received),
                        "说明": "存在证实收到旧告警的收件人，需发送"
                                + ("更正通知" if correction["更正后是否仍触发"] else "撤回通知"),
                    })
        for event in store.rows("price_event"):
            if event_id is not None and event["id"] != event_id:
                continue
            if event.get("status") == "待复核" and not any(
                int(j.get("价格事件", 0)) == event["id"] for j in store.rows("alert_judgement")
            ):
                items.append({
                    "类型": "事件待判定",
                    "编号": event["event_no"],
                    "事件编号": event["event_no"],
                    "收件方": "",
                    "说明": "价格事件尚未引用规则版本完成同口径判定",
                })
        return {"ok": True, "message": "可操作项查询完成", "count": len(items), "items": items}

    # ---------------- 内部辅助 ----------------

    def _deliveries_of(self, judgement_no: str) -> list[dict[str, Any]]:
        return [
            d for d in store.rows("alert_delivery")
            if d.get("判定编号") == judgement_no
        ]

    def _linked_history(self, event: dict[str, Any]) -> dict[str, Any] | None:
        history_id = event.get("关联流水")
        if not history_id:
            match = next(
                (r for r in store.rows("change_history") if r.get("价格事件") == event.get("event_no")),
                None,
            )
            return match
        return store.find("change_history", int(history_id))

    def _deliveries_for_event(self, event_id: int) -> list[dict[str, Any]]:
        return [
            d for d in store.rows("alert_delivery") if int(d.get("价格事件", 0)) == event_id
        ]

    def _review_eligibility(self, event_id: int) -> str:
        # 复核资格只看原始变价告警的对外确认情况；更正/撤回通知不改变旧告警的未知判定
        deliveries = [
            d for d in self._deliveries_for_event(event_id)
            if d.get("交付类型") == "变价告警"
        ]
        if not deliveries:
            judgement_exists = any(
                int(j.get("价格事件", 0)) == event_id for j in store.rows("alert_judgement")
            )
            return ELIG_READY if judgement_exists else ELIG_PENDING_JUDGE
        if any(d["交付状态"] == DLV_PENDING for d in deliveries):
            return ELIG_BLOCKED_UNKNOWN
        return ELIG_READY

    def _recompute_delivery(self, event_id: int) -> None:
        """把逐收件方交付汇总进变价历史流水：显式三态，不靠当前告警推断。

        流水「交付状态」只描述原始变价告警的对外确认结果；更正/撤回是后续跟进动作，
        不会抹掉旧告警确实投递或待复核的事实。
        """
        event = store.find("price_event", event_id)
        if event is None:
            return
        history = self._linked_history(event)
        if history is None:
            return
        deliveries = [
            d for d in self._deliveries_for_event(event_id)
            if d.get("交付类型") == "变价告警"
        ]
        if not deliveries:
            history["交付状态"] = HIST_REVIEW
            history["复核资格"] = self._review_eligibility(event_id)
            return
        states = {d["交付状态"] for d in deliveries}
        if DLV_PENDING in states:
            history["交付状态"] = HIST_REVIEW
        elif DLV_DELIVERED in states and DLV_MISSED not in states:
            history["交付状态"] = HIST_DELIVERED
        elif DLV_DELIVERED in states:
            history["交付状态"] = HIST_PARTIAL
        elif states <= {DLV_MISSED}:
            history["交付状态"] = HIST_FAILED
        elif states <= {DLV_WITHDRAWN}:
            history["交付状态"] = HIST_WITHDRAWN
        else:
            history["交付状态"] = HIST_PARTIAL
        history["复核资格"] = self._review_eligibility(event_id)


alert_ops = AlertOpsService()
