# v0.14：独立体验审计与首批工作台改进

本轮先让两个不继承历史结论的 agent 分别审阅交易员/研究员路径和视觉布局，再用隔离 Chromium 验收校验判断。两份基线报告保留 2b66f9c 的原始判断及固定源码链接：[交易员审计](fresh-trader-experience-v0.13-baseline.zh-CN.md)、[视觉审计](fresh-visual-review-v0.13-baseline.zh-CN.md)。这是内部独立视角评估，不代表任何交易机构，也不是行业认证。

结论：v0.13 的功能覆盖较广，但操作结果、对象上下文和决策信息的呈现仍有实质差距。本轮完成第一批执行与账簿改进，并修正复核发现的跨会话手动部署、策略停止提示和不确定撤单回执。研究工作台、策略项目、数据选择和事件中心仍需要下一批具体实现；不能把本轮通过的测试称为整个专业平台验收完成。

## 从任务出发的改动

**查看市场不打断录单。** 全局搜索 Inspect 和持仓市场名称打开只读查看层，保持当前 URL、订单市场、限价、数量及账簿筛选。关闭/Escape 回到原入口；Trade 仍是显式进入该市场票据的动作。只读层校验 source、市场和品种身份，区分 ticker、mark、funding 的独立时间；当前 ticker 不代表资金费或标记价同样新鲜。重试替换已聚焦控制后，Tab/Shift+Tab 仍在窗口内。来源切换或页面切换关闭旧查看层。

**提交后可以找到那一笔订单。** 票据内回执显示服务器状态、原生请求/成交/取消数量、实际已成交价格和费用、source 与 ID；挂单估计不被包装为成交。HTTP 拒绝没有虚构订单 ID，网络结果未确认不会显示成功。显式 Inspect this order 定位 source/ID，工作订单过滤不再遮蔽已成交结果。订单事件、账本引用和重载均按同一 ID 匹配。已有接口历史窗口外的记录显示不可用，不匹配另一个订单。

**后到的响应不能改写下一笔工作。** 提交回执按挂载生命周期、authenticated session、source、请求序号归属；草稿另按 payload 与 revision 检查。编辑后再改回相同值的草稿仍属于新的编辑。查看该订单不自动清空下一笔草稿，也不会未经点击切换账簿。总览和执行的撤单保留服务器结果，即使对应记录离开 Working。响应丢失时保留请求目标 ID/source，显示“撤单结果未确认”，提供精确查看入口。手动部署和策略停止的迟到回执也受当前会话/来源约束。

**金融信息优先占空间。** 总览账簿满宽，研究继续任务移到其后。持仓桌面摘要常显市场、数量、mark/market value、P&L、Reduce；永续保留 liquidation。其余既有字段在真实行详情中，固定操作列与详情分别留宽。1200px 票据放在账簿之后。主体表格 13px、辅助 12px、闭合行约47px；同一金融行底色统一。移动常显账户权益与 P&L，展开控制并入摘要。市场名/品种/方向组成左对齐的真实按钮，主要触摸入口保持44px。

## 基线问题逐项状态

| 审计项 | 本轮状态 | 下一步或验证边界 |
|---|---|---|
| 1 提交回执与精确结果 | 已实现并验收 | 精确 ID、事件/账本、重载、拒绝和迟到响应；历史检索仍受接口窗口限制：终态订单 200、审计事件 100、账本 500 |
| 2 撤单结果闭环 | 已实现并验收 | Working 行消失后保持原 ID/服务器数量；来源/会话切换拒绝旧回执；503/丢失响应展示明确的目标身份与未确认状态 |
| 3 Inspect 丢失订单草稿 | 已实现并验收 | 独立只读层，普通关闭/Escape/焦点恢复；显式离开页面仍遵循原草稿职责 |
| 4 下单栏挤出 P&L/强平 | 本轮桌面摘要已验收 | 1440/1200 的现货 P&L 与永续 P&L/liquidation；详情保留原字段 |
| 5 减仓准备只呈现影响计数 | 待实施 | 审查中列出具体控制器、组合及待取消指令，保留服务器二次准入 |
| 6 移动首屏冗长 | 已改善并限定验收 | 390×664、12 持仓/300 订单、无活动异常 fixture 的完整首条记录；真实异常仍优先展示 |
| 7 命名/导航层次不一致 | 待实施 | 统一任务名称及明确返回路径 |
| 8 假设与版本身份未贯穿 | 待实施 | 项目/版本/假设摘要贯穿 trial → result → release |
| 9 多试验目录难检索 | 待实施 | 固定 trial ledger，按版本、市场、窗口、成本及状态检索比较 |
| 10 数据兼容性提示较晚 | 待实施 | 选择前显示覆盖、就绪与产品/周期匹配原因 |
| 11 Run 前试验契约不完整 | 待实施 | 当前固定版本、数据/UTC、成本、scope、readiness 的紧凑核对摘要 |
| 12 结果决策与 release 阻碍分散 | 待实施 | 决策摘要、具体 blocker 跳转与返回同一 review |
| 13 Next actions 不易区分对象 | 部分改善 | 移出账簿侧栏；具体项目/版本身份与风险语义仍待实现 |
| 14 金融扫描/整体视觉层级 | 部分改善 | 账簿排版与移动核心路径完成；Research/Strategies/Data/Operations 布局待重构 |

## 后续完整设计方向

下一批先处理具经济后果的对象：减仓准备显示真实影响对象；研究固定 trial ledger、策略身份与数据兼容信息，运行前核对完整试验契约。接着把结果组织成评估对象、证据范围、成本与风险、未解决问题及下一步选择；release 阻碍可跳到具体对象再返回同一选择。最后调整 Strategies 默认项目视图、Data 先浏览已有资料、Operations 先显示事件与处置，再逐步把追加 CSS 整理成共用组件规则。视觉升级服务这些工作路径，保留金融数值与服务器事实。

设计交叉参考：[IBKR Monitor](https://www.ibkrguides.com/traderworkstation/monitor-panel.htm)、[Mosaic layout](https://www.ibkrguides.com/traderworkstation/mosaic-layout.htm)、[QuantConnect live results](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/results)、[TradingView Account Manager](https://www.tradingview.com/support/solutions/43000786138-what-is-the-account-manager/)。从账户监控、订单结果和可配置工作区推导上述职责，是我们的设计判断；不意味着达到这些产品的执行深度。

## 实际界面与验证

[桌面总览](../assets/desk-overview-v0.14-desktop.png) · [手机首屏](../assets/desk-overview-v0.14-mobile.png) · [1440px 执行](../assets/desk-execution-v0.14-desktop.png) · [1200px 执行](../assets/desk-execution-v0.14-1200.png) · [只读市场查看](../assets/desk-inspector-v0.14-desktop.png)。均为明确标注的 synthetic acceptance fixture；不是实际账户、策略收益或真实盘口执行证据。

具体失败与最终检查保留在 [acceptance history](acceptance-history-0.14.0.md) 与 [verification](../verification.md)。真实私有账户/用户/会话在升级时单独核对，测试使用隔离数据库。金融引擎与策略历史证据未因界面变更而重写；local paper execution 范围保持。
