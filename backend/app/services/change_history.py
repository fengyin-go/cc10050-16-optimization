"""变价历史流水领域服务：记录商品价格的历次变化供追溯，负责变价确认、误报标记与口径切换沉淀。

数据链路位置：追溯沉淀层，消费比价计算器的价差结果与货币换算器的口径变更，
确认后的变价记录供告警规则与导出通道引用。

升级点：流水显式承载「价格事件 / 告警判定 / 交付状态 / 复核资格」，
确实投递、已回撤、待复核必须可区分，禁止靠当前是否还有告警去推断旧交付。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 流水交付三态：显式区分，禁止靠当前告警推断旧交付
HIST_REVIEW = "待复核"
HIST_DELIVERED = "确实投递"
HIST_WITHDRAWN = "已回撤"


class ChangeHistoryService(BaseModuleService):
    MODULE = "change_history"
    ENTITY = "变价记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = [
        "商品标识", "旧价格", "新价格", "变化幅度", "发现时间",
        "价格事件", "告警判定", "交付状态", "复核资格",
    ]
    REQUIRED_FIELDS = ["商品标识", "旧价格", "新价格"]
    STATUS_ORDER = ["已确认", "待核实", "误报"]
    ACTION_RULES = {"确认变价": "已确认", "标记误报": "误报"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：变价确认与追溯 ----

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """通用动作入口同样执行交付守卫：未知状态不能被确认成成功。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is not None and action == "确认变价" and entry.get("交付状态") == HIST_REVIEW:
            return None, "交付凭据仍待复核，未知状态不能确认为成功；请先在变价事件处置工作台核对收件方响应"
        return super().run_action(entry_id, action)

    def confirm_change(self, entry_id: int) -> dict[str, Any]:
        """确认一次变价并校验价格口径有效；若交付状态未知则保持暂缓复核。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        try:
            old_price = float(entry.get("旧价格", "0") or 0)
            new_price = float(entry.get("新价格", "0") or 0)
        except ValueError:
            return {"ok": False, "message": "旧价格/新价格数值无效"}
        if old_price <= 0:
            return {"ok": False, "message": "旧价格必须为正数"}
        ratio = (new_price - old_price) / old_price
        entry["变化幅度"] = f"{ratio:.2%}"
        # 交付未知时不允许把流水直接结掉：恢复期间未知状态不算成功
        if entry.get("交付状态") == "待复核":
            entry["status"] = "待核实"
            entry["pending"] = True
            entry.setdefault("复核资格", "暂缓复核（交付未知）")
            return {
                "ok": False,
                "message": "存在待复核的交付凭据，暂不能确认变价；请先在告警核对单核对收件方响应",
                "ratio": f"{ratio:.2%}",
                "goods": entry.get("商品标识", ""),
            }
        entry["status"] = "已确认"
        entry["pending"] = False
        return {
            "ok": True,
            "message": "变价已确认",
            "ratio": f"{ratio:.2%}",
            "goods": entry.get("商品标识", ""),
        }

    def mark_false_positive(self, entry_id: int, reason: str) -> dict[str, Any]:
        """标记误报：保留告警判定与交付跟进，不再用“有没有当前告警”推断旧交付。

        历史上这里会联动关闭所有生效规则，但规则关闭并不能撤回已发出的告警，
        反而让旧交付失去可回查入口。现在只沉淀误报结论，并显式返回交付侧待办。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "变价记录不存在"}
        # 延迟导入避免与 alert_ops 形成模块加载环
        from app.services.alert_ops import alert_ops

        entry["status"] = "误报"
        entry["pending"] = False
        entry.setdefault("复核资格", "可复核")
        linked_event = next(
            (e for e in store.rows("price_event") if e.get("event_no") == entry.get("价格事件")),
            None,
        )
        followups: list[dict[str, Any]] = []
        if linked_event is not None:
            linked_event["复核结论"] = "误报"
            followups = alert_ops.actionable_items(linked_event["id"]).get("items", [])
            entry["复核资格"] = alert_ops.false_positive_check(linked_event["id"]).get(
                "review_eligibility", entry["复核资格"])
        return {
            "ok": True,
            "message": f"误报已标记: {reason}；历史告警交付记录保留可回查",
            "followup_count": len(followups),
            "followups": followups,
        }

    def query_by_goods(self, goods_id: str, status: str | None = None) -> dict[str, Any]:
        """按商品查询变价历史并支持状态过滤。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("商品标识") == goods_id]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: str(r.get("发现时间", "")), reverse=True)
        return {
            "ok": True,
            "message": "变价历史查询完成",
            "goods": goods_id,
            "count": len(rows),
            "records": [
                {
                    "old": r.get("旧价格", ""),
                    "new": r.get("新价格", ""),
                    "ratio": r.get("变化幅度", ""),
                    "found_at": r.get("发现时间", ""),
                    "event": r.get("价格事件", ""),
                    "judgement": r.get("告警判定", ""),
                    "delivery": r.get("交付状态", "未涉及交付"),
                    "eligibility": r.get("复核资格", "无需复核"),
                }
                for r in rows[:10]
            ],
        }

    def sync_currency_switch(self, goods_id: str, old_price: float, new_price: float) -> dict[str, Any]:
        """货币口径切换时重新核算变价幅度。"""
        if old_price <= 0:
            return {"ok": False, "message": "旧价格无效"}
        ratio = (new_price - old_price) / old_price
        affected = 0
        for row in store.rows(self.MODULE):
            if row.get("商品标识") == goods_id and row.get("status") == "已确认":
                row["变化幅度"] = f"{ratio:.2%}"
                row["发现时间"] = datetime.now(timezone.utc).isoformat()
                affected += 1
        return {
            "ok": True,
            "message": "口径切换已同步变价幅度",
            "affected": affected,
            "ratio": f"{ratio:.2%}",
        }

    def pending_for_alert(self, goods_id: str) -> dict[str, Any]:
        """把待核实记录交给告警规则消费。"""
        rows = [
            r for r in store.rows(self.MODULE)
            if r.get("商品标识") == goods_id and r.get("status") == "待核实"
        ]
        if not rows:
            return {"ok": False, "message": "没有待核实的变价记录", "count": 0}
        return {
            "ok": True,
            "message": "待核实记录已移交告警规则",
            "count": len(rows),
            "goods": goods_id,
        }

    def history_stats(self, days: int = 7) -> dict[str, Any]:
        """输出变价历史统计快照，交付三态单独计数。"""
        rows = store.rows(self.MODULE)
        confirmed = sum(1 for r in rows if r.get("status") == "已确认")
        false_pos = sum(1 for r in rows if r.get("status") == "误报")
        delivered = sum(1 for r in rows if r.get("交付状态") == "确实投递")
        withdrawn = sum(1 for r in rows if r.get("交付状态") == "已回撤")
        under_review = sum(1 for r in rows if r.get("交付状态") == "待复核")
        return {
            "ok": True,
            "message": "变价统计完成",
            "total": len(rows),
            "confirmed": confirmed,
            "false_positive": false_pos,
            "pending": len(rows) - confirmed - false_pos,
            "delivered": delivered,
            "withdrawn": withdrawn,
            "delivery_under_review": under_review,
        }
