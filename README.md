# 商品价格监控采集平台

面向多区域站点的商品价格抓取、规格归一、比价计算与变价告警的采集治理平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/AlertOpsView.vue       变价事件处置工作台（判定/交付/更正闭环）
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/alert_ops_router.py  变价告警处置闭环接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/alert_ops.py  事件判定/交付凭据/更正处置服务
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── acceptance/verify_d131.py D131 变价告警处置闭环验收脚本（仅标准库）
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8020/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5200/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8020`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 采集站点目录 | `shop_site` | 站点配置 | 站点名称、站点域名、采集区域 |
| 价格爬取任务 | `price_crawler` | 爬取任务 | 任务名称、目标站点、商品关键词 |
| 页面解析模板 | `page_parser` | 解析模板 | 模板名称、适用站点、选择器路径 |
| 规格归一器 | `spec_normalizer` | 规格归一规则 | 规则名称、源规格文本、归一规格 |
| 货币换算器 | `currency_conv` | 汇率快照 | 快照日期、源货币、目标货币 |
| 价格基线库 | `price_baseline` | 价格基线 | 商品标识、归一规格、基线价格 |
| 比价计算器 | `price_compare` | 比价结果 | 比价批次、商品标识、最低价站点 |
| 变价告警规则 | `price_alert` | 告警规则 | 规则名称、监控商品、价差阈值 |
| 商品登记中心 | `goods_registry` | 商品档案 | 商品标识、商品名称、品牌 |
| 规格匹配器 | `sku_match` | 匹配记录 | 匹配批次、商品档案、候选规格 |
| 页面样本库 | `sample_store` | 页面样本 | 样本批次、站点名称、原始HTML摘要 |
| 解析质量审计 | `parse_audit` | 审计明细 | 审计时间、解析模板、样本总数 |
| 采集调度计划 | `crawl_schedule` | 调度计划 | 计划名称、关联任务、调度周期 |
| 抓取频控 | `rate_limit` | 频控规则 | 规则名称、目标站点、每分钟上限 |
| 代理线路池 | `proxy_route` | 代理线路 | 线路标识、线路区域、可用性评分 |
| 失败隔离区 | `failure_quarantine` | 隔离记录 | 样本指纹、失败阶段、失败原因 |
| 比价导出通道 | `price_export` | 导出任务 | 任务名称、商品范围、导出格式 |
| 变价历史流水 | `change_history` | 变价记录 | 商品标识、旧价格、新价格 |
| 人工复核队列 | `match_review` | 复核记录 | 记录编号、匹配记录、复核结论 |
| 采集量指标 | `collect_metrics` | 指标记录 | 统计时间、站点名称、抓取量 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 变价告警处置闭环

原先告警规则只做阈值判定和写流水，更正/重投会与已发起交付产生状态冲突。
`/alert_ops` 工作台与 `/api/alert-ops/*` 接口把链路升级为可核对的处置闭环：

- **同口径事件判定**：判定引用具体报价快照（基线→采集）与规则版本，始终用同一基线核算，
  禁止拿修订前后金额拼比；同一价格事件 × 规则版本只确认一次（幂等）。规则修订阈值会升级版本，
  旧版本上的判定与交付凭据保持可追溯。
- **交付凭据可查询**：每条交付带交付编号与收件回执，状态显式区分为
  「已确认送达 / 已发起待确认（未知）/ 未送达 / 已回撤」。恢复期间未知状态一律不算成功，
  支持 `normal`、`receipt_lost`（已收到但回执丢失）、`not_received`（未收到）三种故障模拟。
- **更正与误报处置**：更正后旧版超阈值告警禁止再发送；流水可在回执不可查时先确认更正判定，
  但通知侧保留「交付待确认」待办。回执补齐后，更正/撤回通知只发给真正收到过旧告警的收件人，
  未证实收到的一律跳过。
- **历史流水三态**：变价历史流水显式记录「价格事件 / 告警判定 / 交付状态 / 复核资格」，
  确实投递、已回撤、待复核可直接区分，不靠当前是否还有告警去推断旧交付。
- **误报入口核对**：可按事件回查价格数据、判定口径与每条交付是否已对外确认，未知不会显示成已确认。

### D131 验收

种子数据内置 D131 事件（旧价 100、阈值 10%、采集价 86 → -14% 待送告警）。
后端启动后运行（脚本只用标准库，通过 HTTP 对运行中的服务断言）：

```bash
python3 -I acceptance/verify_d131.py
```

覆盖：回执丢失后补回执的可追查撤回、未收到时明确未送达且不发更正、
暂缓回执期间先确认更正再从原单续办、同口径拼比与规则版本守卫，共 30 项断言。

领域：数据工程
子方向：数据采集、爬虫与RPA
