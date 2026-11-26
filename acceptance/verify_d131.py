#!/usr/bin/env python3 -I
"""D131 变价告警处置闭环验收脚本。

覆盖三类场景：
1. 回执丢失：收件方实际已收到旧告警，但回执暂不可查 → 补回执后必须发可追查的更正/撤回；
2. 未收到：收件方没有收到旧告警 → 明确未送达，任何时候不得对其发更正/撤回；
3. 暂缓回执：处置更正时回执仍不可查 → 流水可先确认更正、通知侧保留可操作项，
   回执补到后只对真正收到过旧通知的收件人继续处理。

仅依赖标准库，对运行中的后端（默认 http://127.0.0.1:8020）发请求并逐项断言。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8020"
PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, bool, str]] = []


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    url = BASE + urllib.parse.quote(path, safe="/?=&%{}")
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, condition, detail))
    print(f"[{PASS if condition else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def scenario_receipt_lost() -> None:
    print("\n=== 场景A：回执丢失（已收到，回执暂不可查） ===")
    event = call("POST", "/api/alert-ops/events", {
        "goods_id": "D131-A", "baseline": 100, "collected": 86,
        "quote_source": "比价批次CMP-A", "recipients": ["运营值班组", "站点对接人"],
    })
    event_id = event["event"]["id"]

    # 同口径判定：100→86 = -14%，阈值 10%，触发
    eva = call("POST", f"/api/alert-ops/events/{event_id}/evaluate", {"rule_id": 4})
    check("A1 同口径判定触发 14% 告警",
          eva["ok"] and eva["judgement"]["触发"] and eva["judgement"]["变化幅度"] == "-14.00%",
          eva.get("judgement", {}).get("判定口径", eva.get("message")))

    # 幂等：同一事件 × 规则版本只确认一次
    eva2 = call("POST", f"/api/alert-ops/events/{event_id}/evaluate", {"rule_id": 4})
    judgements = call("GET", "/api/alert-ops/judgements")["items"]
    same = [j for j in judgements if j["价格事件"] == event_id and j["规则版本"] == "v1"]
    check("A2 同一价格事件×规则版本只确认一次",
          eva2.get("idempotent") is True and len(same) == 1,
          f"判定条数={len(same)}")

    judgement_id = eva["judgement"]["id"]
    dispatch = call("POST", "/api/alert-ops/deliveries", {
        "judgement_id": judgement_id, "simulate": "receipt_lost",
    })
    check("A3 按回执丢失故障发起交付，状态为待确认",
          dispatch["ok"] and all(d["交付状态"] == "已发起待确认" for d in dispatch["deliveries"]),
          f"{len(dispatch.get('deliveries', []))} 条交付")
    check("A4 未知状态下证据不可回查",
          all(not d["证据可回查"] for d in dispatch["deliveries"]))

    # 恢复期间核对：未知不能算成功
    reconcile = call("POST", f"/api/alert-ops/judgements/{judgement_id}/reconcile")
    check("A5 核对单把未知单列且不计成功",
          reconcile["breakdown"].get("已确认送达", 0) == 0
          and len(reconcile["unknown_deliveries"]) == 2,
          str(reconcile["breakdown"]))

    # 运营更正为 98 并标记误报
    correction = call("POST", f"/api/alert-ops/events/{event_id}/correct", {
        "corrected_price": 98, "reason": "采集价格修正", "false_positive": True,
    })
    check("A6 更正后同口径幅度 -2.00% 不再触发旧 14% 告警",
          correction["ok"] and correction["correction"]["更正后幅度"] == "-2.00%"
          and correction["correction"]["更正后是否仍触发"] is False)

    # 旧版告警禁止再发送
    redispatch = call("POST", "/api/alert-ops/deliveries", {
        "judgement_id": judgement_id, "simulate": "receipt_lost",
    })
    check("A7 更正后旧 14% 告警禁止再发送", not redispatch["ok"], redispatch.get("message", ""))

    # 回执不可查时提交更正处置确认：流水可先确认，通知侧保留待办
    confirm = call("POST", f"/api/alert-ops/corrections/{correction['correction']['id']}/confirm")
    check("A8 回执不可查时流水先确认更正",
          confirm["ok"] and confirm["correction"]["处置状态"] == "处置已确认")
    check("A9 确认后通知侧仍有可操作项（交付待确认）",
          any(i["类型"] == "交付待确认" for i in confirm["remaining_actions"]),
          str([i["类型"] for i in confirm["remaining_actions"]]))

    # 此时不能发撤回：没有任何收件人被证实收到
    block = call("POST", f"/api/alert-ops/corrections/{correction['correction']['id']}/dispatch",
                 {"simulate": "normal"})
    check("A10 未证实收到前不发送撤回通知", not block["ok"], block.get("message", ""))

    # 回执补到：两个收件方都实际收到，补送达回执
    for delivery in dispatch["deliveries"]:
        ref = delivery["交付编号"]
        got = call("POST", f"/api/alert-ops/deliveries/{ref}/receipt",
                   {"receipt_type": "送达回执", "remark": "补登记：实际已收到"})
        check(f"A11 补回执 {ref} 后状态转为已确认送达",
              got["ok"] and got["delivery"]["交付状态"] == "已确认送达"
              and got["receipt"]["证据可回查"] is True)

    # 再核对：全部确认成功
    reconcile2 = call("POST", f"/api/alert-ops/judgements/{judgement_id}/reconcile")
    check("A12 回执补齐后核对为全部送达",
          reconcile2["breakdown"].get("已确认送达") == 2
          and reconcile2["breakdown"].get("已发起待确认", 0) == 0)

    # 发送撤回（更正后不再触发 → 撤回通知），应发给两个证实收到的收件人
    withdraw = call("POST", f"/api/alert-ops/corrections/{correction['correction']['id']}/dispatch",
                    {"simulate": "normal"})
    check("A13 仅向证实收到旧告警的收件人发可追查撤回回执",
          withdraw["ok"] and len(withdraw["sent"]) == 2
          and all(d["交付类型"] == "撤回通知" and d["证据可回查"] for d in withdraw["sent"])
          and withdraw["skipped"] == [],
          str([d["收件方"] for d in withdraw.get("sent", [])]))

    # 流水显式三态
    history = call("GET", f"/api/change_history?商品标识=D131-A")
    row = history["items"][0]
    check("A14 历史流水显式记录确实投递并关联事件/判定",
          row["交付状态"] in {"确实投递"} and row["价格事件"] and row["告警判定"],
          f"交付状态={row['交付状态']} 复核资格={row['复核资格']}")

    # 误报入口核对
    fpc = call("GET", f"/api/alert-ops/events/{event_id}/false-positive-check")
    check("A15 误报入口可按事件核对价格数据与对外确认",
          fpc["ok"] and {d["对外确认"] for d in fpc["deliveries"] if d["交付类型"] == "变价告警"}
          == {"已对外确认"},
          str([d["对外确认"] for d in fpc["deliveries"] if d["交付类型"] == "变价告警"]))


def scenario_not_received() -> None:
    print("\n=== 场景B：未收到（无送达证据，明确未送达） ===")
    event = call("POST", "/api/alert-ops/events", {
        "goods_id": "D131-B", "baseline": 100, "collected": 86,
        "quote_source": "比价批次CMP-B", "recipients": ["运营值班组", "站点对接人"],
    })
    event_id = event["event"]["id"]
    eva = call("POST", f"/api/alert-ops/events/{event_id}/evaluate", {"rule_id": 4})
    judgement_id = eva["judgement"]["id"]
    dispatch = call("POST", "/api/alert-ops/deliveries", {
        "judgement_id": judgement_id, "simulate": "not_received",
    })
    check("B1 未收到故障：交付保持待确认且证据不可回查",
          all(d["交付状态"] == "已发起待确认" and not d["证据可回查"]
              for d in dispatch["deliveries"]))

    correction = call("POST", f"/api/alert-ops/events/{event_id}/correct", {
        "corrected_price": 98, "reason": "采集价格修正", "false_positive": True,
    })
    call("POST", f"/api/alert-ops/corrections/{correction['correction']['id']}/confirm")

    # 运营核对收件方响应：确认未收到 → 不补送达回执，维持未证实
    # 直接尝试发撤回：应被拒绝
    block = call("POST", f"/api/alert-ops/corrections/{correction['correction']['id']}/dispatch",
                 {"simulate": "normal"})
    check("B2 未收到时明确不发送更正/撤回（未知不算成功）",
          not block["ok"] and "没有任何收件人被证实收到" in block.get("message", ""),
          block.get("message", ""))

    fpc = call("GET", f"/api/alert-ops/events/{event_id}/false-positive-check")
    unknown = [d for d in fpc["deliveries"] if d["交付状态"] == "已发起待确认"]
    check("B3 误报核对显示对外确认未知而非已确认",
          all("未知" in d["对外确认"] for d in unknown) and len(unknown) == 2,
          str([d["对外确认"] for d in unknown]))

    checklist = call("GET", f"/api/alert-ops/events/{event_id}/checklist")
    check("B4 核对单复核资格为暂缓（交付未知）",
          checklist["review_eligibility"] == "暂缓复核（交付未知）"
          and len(checklist["blocked_reasons"]) >= 1,
          checklist["review_eligibility"])


def scenario_delayed_receipt_mixed() -> None:
    print("\n=== 场景C：暂缓回执 + 混合收件人（一个收到、一个未收到） ===")
    event = call("POST", "/api/alert-ops/events", {
        "goods_id": "D131-C", "baseline": 100, "collected": 86,
        "quote_source": "比价批次CMP-C", "recipients": ["运营值班组", "站点对接人"],
    })
    event_id = event["event"]["id"]
    eva = call("POST", f"/api/alert-ops/events/{event_id}/evaluate", {"rule_id": 4})
    judgement_id = eva["judgement"]["id"]
    dispatch = call("POST", "/api/alert-ops/deliveries", {
        "judgement_id": judgement_id, "simulate": "receipt_lost",
    })
    deliveries = dispatch["deliveries"]

    # 收件方暂缓回执期间，运营在流水更正同一事件
    correction = call("POST", f"/api/alert-ops/events/{event_id}/correct", {
        "corrected_price": 98, "reason": "D131 价格更正", "false_positive": True,
    })
    # 回执仍不可查时提交更正处置
    confirm = call("POST", f"/api/alert-ops/corrections/{correction['correction']['id']}/confirm")
    check("C1 回执不可查时流水可先确认更正",
          confirm["ok"] and confirm["correction"]["处置状态"] == "处置已确认")
    items = call("GET", f"/api/alert-ops/action-items?event_id={event_id}")
    check("C2 通知侧仍保留可操作项（交付待确认×2）",
          items["count"] >= 2
          and all(i["类型"] == "交付待确认" for i in items["items"]),
          str([i["类型"] for i in items["items"]]))

    # 刷新两页：核对单与流水均保持待复核
    checklist = call("GET", f"/api/alert-ops/events/{event_id}/checklist")
    history = call("GET", f"/api/change_history?商品标识=D131-C")
    check("C3 刷新后核对单与流水都仍是待复核，未被错误结单",
          checklist["review_eligibility"] == "暂缓复核（交付未知）"
          and history["items"][0]["交付状态"] == "待复核"
          and history["items"][0]["status"] == "待核实")

    # 回执补到：仅第一个收件人确认收到
    first_ref = deliveries[0]["交付编号"]
    call("POST", f"/api/alert-ops/deliveries/{first_ref}/receipt",
         {"receipt_type": "送达回执", "remark": "延迟回执补登记"})

    # 从原单接着处理：发撤回
    withdraw = call("POST", f"/api/alert-ops/corrections/{correction['correction']['id']}/dispatch",
                    {"simulate": "normal"})
    check("C4 撤回只发给真正收到旧通知的 1 个收件人",
          withdraw["ok"] and len(withdraw["sent"]) == 1
          and withdraw["sent"][0]["收件方"] == deliveries[0]["收件方"]
          and len(withdraw["skipped"]) == 1
          and "未证实收到" in withdraw["skipped"][0]["原因"],
          f"sent={[d['收件方'] for d in withdraw.get('sent', [])]} skip={withdraw.get('skipped')}")

    # 旧 14% 告警全程未被重发：统计变价告警类型交付只有最初两条
    all_dlv = [d for d in call("GET", "/api/alert-ops/events")["items"]]  # noqa: F841
    # 通过交付列表接口间接统计：用 checklist
    final_list = call("GET", f"/api/alert-ops/events/{event_id}/checklist")
    old_alerts = [d for d in final_list["deliveries"] if d["交付类型"] == "变价告警"]
    check("C5 旧 14% 告警未被重发（变价告警交付仍为最初 2 条）",
          len(old_alerts) == 2, str([(d["交付编号"], d["交付状态"]) for d in old_alerts]))


def scenario_version_guard() -> None:
    print("\n=== 场景D：同口径拼比与规则版本守卫 ===")
    # 专用临时规则，避免污染种子规则 4（D131 规则始终保持 v1/10%）
    created = call("POST", "/api/price_alert", {
        "values": {
            "规则名称": "版本守卫临时规则",
            "监控商品": "D131-D",
            "价差阈值": "10%",
            "连续次数": "1",
            "通知渠道": "站内通知",
        }
    })
    temp_rule_id = created["entry"]["id"]
    call("POST", f"/api/price_alert/{temp_rule_id}/actions", {"action": "启用规则"})

    # 规则阈值修订产生新版本
    revise = call("POST", f"/api/alert-ops/rules/{temp_rule_id}/revise",
                  {"new_threshold": "20%", "reason": "阈值收紧验证"})
    check("D1 规则修订升级版本且旧判定保留 v1",
          revise["ok"] and revise["old_version"] == "v1" and revise["new_version"] == "v2",
          revise.get("message", ""))

    event = call("POST", "/api/alert-ops/events", {
        "goods_id": "D131-D", "baseline": 100, "collected": 86,
        "quote_source": "比价批次CMP-D", "recipients": ["运营值班组"],
    })
    event_id = event["event"]["id"]
    eva = call("POST", f"/api/alert-ops/events/{event_id}/evaluate", {"rule_id": temp_rule_id})
    check("D2 新版本 v2 下 -14% 不触发 20% 阈值",
          eva["ok"] and eva["judgement"]["规则版本"] == "v2" and eva["judgement"]["触发"] is False,
          eva.get("judgement", {}).get("判定口径", ""))

    # 再判一次仍是幂等
    eva2 = call("POST", f"/api/alert-ops/events/{event_id}/evaluate", {"rule_id": temp_rule_id})
    check("D3 新版本下重复判定同样幂等", eva2.get("idempotent") is True)

    # 同口径守卫：更正价不参与旧判定拼比（查判定快照）
    correction = call("POST", f"/api/alert-ops/events/{event_id}/correct", {
        "corrected_price": 98, "reason": "口径守卫验证", "false_positive": False,
    })
    j = eva["judgement"]
    check("D4 判定快照固定为 100→86，未被 98 污染",
          j["基线快照"] == 100 and j["采集快照"] == 86
          and correction["correction"]["原采集快照"] == 86
          and correction["correction"]["更正后快照"] == 98)

    # 种子规则 4 不受场景 D 影响
    rule4 = call("GET", "/api/alert-ops/rules/4")["rule"]
    check("D5 种子规则 4 保持 v1 / 10% 未被污染",
          rule4["规则版本"] == "v1" and rule4["价差阈值"] == "10%",
          f"{rule4['规则版本']} / {rule4['价差阈值']}")


def main() -> int:
    health = call("GET", "/api/health")
    if not health.get("ok"):
        print("后端未运行，请先执行 backend/run.sh")
        return 2
    scenario_receipt_lost()
    scenario_not_received()
    scenario_delayed_receipt_mixed()
    scenario_version_guard()
    failed = [name for name, ok, _ in results if not ok]
    print("\n================ 验收汇总 ================")
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：")
        for name in failed:
            print(" -", name)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
