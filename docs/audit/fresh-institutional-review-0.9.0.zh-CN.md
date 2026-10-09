# v0.9.0 独立机构交易员视角审计

审查日期：2026-10-09（Asia/Singapore）  
审查提交：`6436a279e0b6fc9e7fe0500ab6544ee14bf369c7`  
范围：指定现货与线性 USDT 永续、公开 OKX 行情、历史研究、本地模拟。没有提交交易所订单。

## 判断

**当前平台有实际研究价值，但尚不能作为顶级机构交易员可以依赖的研究、部署和日常值班闭环。即使只按已经约定的研究与本地模拟范围验收，仍有阻断性差距。**

可以用于明确指定市场的中低频假设研究、成本敏感性分析、冻结回放，以及有人监督的 paper 实验。不能把某个版本的历史收益直接视为部署后行为的可靠代表，也不能把当前模拟账户视为具有完整资本预算、保护执行和事故诊断的机构账户。

这个结论不以缺少交易所实盘或微秒级系统为依据。一个好的研究平台也不需要保证每条策略盈利；它必须让交易员可信地证伪假设、比较收益来源，并清楚知道部署后遇到失败会发生什么。当前最紧要的问题正是这些经济与操作契约未闭合。

本轮由不继承此前设计上下文的独立 agent 从源码与证据入手。主审随后交叉挑战其结论，并在禁止外部请求的临时 SQLite 库中复核三个反例。所谓 Jane Street 视角是根据公开材料推导的需求框架，不代表其员工或内部验收标准。其公开介绍强调研究、模型、交易与实时可见性的结合；生产工程分享强调订单、持仓、异常与及时响应的重要性。[公开介绍](https://www.janestreet.com/what-we-do/overview/)、[生产工程分享](https://www.janestreet.com/tech-talks/production-engineering-when-trading-billions-of-dollars-a-day/)

## 三个真实工作场景

| 角色 | 必须完成的工作 | 当前判断 |
|---|---|---|
| 研究型交易员 | 解释收益机制，冻结数据和版本，设置可反驳实验，比较成本与基准，回放，再审阅模拟部署 | 冻结输入、因果计算和回放已实现；历史与前向失败处理不一致，部分历史结果不能代表部署策略 |
| 组合风险经理 | 分配共同资本，合并现货与永续风险，检查集中度和压力损失，限制新增风险，核对净损益归属 | 共同现金、事务风险和贡献账本已有实质基础；多个 sleeve 的资金承诺和相关风险没有完整账户预算层 |
| 执行／值班交易员 | 检查决策与成交，发现多腿失败，处理补偿和残余库存，重启恢复，核对未结资金费率 | 持久命令、幂等和补偿已有实现；未结费率可阻塞保护动作，受阻补偿可能漏出主桌告警 |

优先级含义：P1 阻止将当前版本当成可靠的专业研究与 paper 闭环；P2 是限制适用范围的结构性能力缺口。它们不是实盘投资建议或安全漏洞评分。

## P1-1：资金费率待发布时，减仓与模拟清算均受阻

**状态：独立 agent 与主审均已复现。**

[pro_execution.py:536](/Users/billpwchan/Documents/okx_algo/backend/tidebench/pro_execution.py:536) 在判断 `reduce_only` 和清算豁免之前，要求已到期资金费率已经入账；否则返回 `funding_pending`。[pro_service.py:1062](/Users/billpwchan/Documents/okx_algo/backend/tidebench/pro_service.py:1062) 也在模拟清算与止损处理前调用资金费率同步。

反例使用临时合成市场：100 张合约，每张 0.01 BTC，价格 100 开多，10 倍杠杆，保证金 10；资金费率到期但尚无历史事件，最新 mark、bid、ask 为 80。未实现损益为 −20，维护保证金为 0.32。普通减仓与 `liquidation=True` 均返回 `funding_pending`，仓位仍为 100，债务仍为 0。

问题不是应该忽略费率，也不是缺少命令幂等。问题是系统把未结经济义务与所有仓位变化绑定，缺少保护动作可用、未结义务仍准确保留的模型。OKX 的公开清算说明将未结资金费用纳入清算成本，并区分 mark 驱动的清算与止损；这里仅用作经济机制的校准，不声称本地模型等同完整交易所规则。[OKX 清算说明](https://www.okx.com/en-sg/help/frequently-issues-of-contracts-for-compulsory-liquidation)

最小验收必须满足以下契约：

- 到期时保留当时的数量、市场、归属和未结义务，不能用零费率冒充已经完成结算。
- 在报价及合约单位有效时，保护性减仓／模拟清算能够处理；若模型选择停止，必须明确将模拟与经济账本标记为未完成，不能继续表现为可靠风险状态。
- 即使平仓、重开、重启，后来公布的费率也只按原到期数量及归属入账一次，不漏计、不重复、不归给新持仓。
- 账户、费用、保险债务和贡献账本仍可对账；未知费用不能被认证为完整权益。

[反例证据](fresh-review-0.9.0/funding-gap.json)

## P1-2：历史研究与 managed 组合执行不同的失败政策

**状态：历史与实际 managed 路径均已复现，主审再次复核。**

[portfolio_research.py:797](/Users/billpwchan/Documents/okx_algo/backend/tidebench/portfolio_research.py:797) 在减仓后逐腿执行有效 additions；[813 行](/Users/billpwchan/Documents/okx_algo/backend/tidebench/portfolio_research.py:813) 记录被跳过腿后继续。它没有 managed controller 的最小腿拒绝、残差上限和整组补偿契约。[managed_portfolios.py:779](/Users/billpwchan/Documents/okx_algo/backend/tidebench/managed_portfolios.py:779)、[808 行](/Users/billpwchan/Documents/okx_algo/backend/tidebench/managed_portfolios.py:808)

共同 sizing 输入：初始现金 10,000，BTC／ETH 各 50%，费用和滑点均零。BTC 价格 100、最小量 0.01；ETH 价格 100,000、最小量 1。目标数量分别为 50 和 0.05。

四根历史 bar 的研究买入并保留 BTC 50，同时记录三次 ETH `minimum_size`。实际 managed 路径在任何 add 之前失败，组为 `failed`，批次为 `compensated`，零订单、零持仓。两边 forward 信号时间线并非每项相同；反例证明的是相同经济 sizing 障碍下的失败政策差异，不冒充完整时钟等价实验。

共享权重函数、规划函数和不可变版本都值得保留，但它们不能替代共享执行语义。当前公开 28 项结果并没有因此被判为伪造；问题是绑定版本仍可能研究一种失败行为、部署另一种失败行为。

最小验收：等价的决策、报价、规则事件与失败序列输入两条路径，覆盖最小腿失败、先成交后拒绝、超残差、补偿受阻及重启。最终数量、现金、费用与组状态必须一致，或具有显式、受审阅的差异契约。失败政策与残差限额要进入研究配置、冻结输入及回放身份。

[反例证据](fresh-review-0.9.0/history-forward-gap.json)

## P1-3：补偿受阻且仍有库存，主交易桌可能未显示事故

**状态：实际 runtime 反例已复现，主审再次复核。**

[Overview.tsx:124](/Users/billpwchan/Documents/okx_algo/frontend/src/pages/Overview.tsx:124) 根据 deployment 成员的状态及错误构建策略告警，没有消费 managed group 或持久事故。组进入补偿与补偿受阻阶段时，仅组状态／错误被更新：[managed_portfolios.py:841](/Users/billpwchan/Documents/okx_algo/backend/tidebench/managed_portfolios.py:841)、[899 行](/Users/billpwchan/Documents/okx_algo/backend/tidebench/managed_portfolios.py:899)。准备阶段失败也只写组错误：[pro_service.py:1210](/Users/billpwchan/Documents/okx_algo/backend/tidebench/pro_service.py:1210)。

隔离复现让 ETH 加仓被拒，BTC 的补偿减仓也被拒。组进入 `compensating`，保留 BTC 0.04103290；两个成员仍为 `running`，`last_error=null`。后台 `managed_portfolio` incident 已经 open，但 operations health 的检查均为 `ok`，主桌策略错误数组为空。

准确范围：**后端事故记录已经存在**。[pro_service.py:1344](/Users/billpwchan/Documents/okx_algo/backend/tidebench/pro_service.py:1344) 会记录 managed 事故；成功补偿后，成员错误也能进入主桌。因此不能泛称所有组故障均无告警。缺口是准备失败或仍处于受阻补偿时，交易员最需要看见的信息没有完整进入主桌，而基础设施健康仍可能正常。[operations health:1406](/Users/billpwchan/Documents/okx_algo/backend/tidebench/pro_service.py:1406)

这个问题首先是决策信息层级，而不是边框、配色或卡片样式。美观界面必须让“现在什么需要我处理”先于一般状态和指标。

最小验收：反例在下一次主桌刷新内显示组名、失败阶段、残余资产与金额、持续时间及直接诊断入口；准备阶段失败也必须可见。组数和腿数分开呈现。事故确认不隐藏未解决风险，后台正常不冒充经济状态正常。

[反例证据](fresh-review-0.9.0/alert-gap.json)

## P2-1：历史成员与持仓生命周期尚不完整

**状态：能力未完整实现，而非只欠更多运行观察。**

当前真实 forward instrument observations 有价值，但不能倒推多年历史。研究的 point-in-time 支持规则事件，不过会拒绝非 live 状态及合约单位转换：[portfolio_research.py:215](/Users/billpwchan/Documents/okx_algo/backend/tidebench/portfolio_research.py:215)、[225 行](/Users/billpwchan/Documents/okx_algo/backend/tidebench/portfolio_research.py:225)。动态成员、退市持仓结算和数量转换仍在设计范围。[历史宇宙设计](../historical-universe-design.md)

这允许指定当前 BTC／ETH／SOL 的篮子情景研究，不能支持无幸存者偏差的历史横截面选币结论。官方 API 变更记录中确有合约重命名及 `expired/rebase/post_only/live` 状态迁移，说明生命周期不是纯理论边界。[OKX 官方变更记录](https://www.okx.com/docs-v5/log_en/)

最小验收：有来源的三市场历史样本，覆盖上市、暂停／恢复及持仓期间退市；每根决策只用当时可知成员，未知覆盖不能默认可交易。缺失退市结算证据时保留库存并阻断可信结果。更新原始证据不改变冻结研究。

## P2-2：sleeve 目标没有完整共享账户预算层

**状态：能力未实现；不能误称已有 sleeve governor 失效。**

每组按完整账户 equity × `capital_pct` 计算资本：[managed_portfolios.py:503](/Users/billpwchan/Documents/okx_algo/backend/tidebench/managed_portfolios.py:503)。账户事务有订单名义金额、gross、杠杆和每日亏损限制：[pro_execution.py:20](/Users/billpwchan/Documents/okx_algo/backend/tidebench/pro_execution.py:20)、[623 行](/Users/billpwchan/Documents/okx_algo/backend/tidebench/pro_execution.py:623)。资产集中度和多组相关风险没有完整账户层准入，资本承诺也未形成确定预算政策。

共同现金缩放防止透支，但不能回答多个组如何竞争有限预算；执行顺序可能影响得到的资金和是否触发补偿。一个组的 covariance governor 不能自动成为账户 governor。代码正确将其标为 allocated sleeve、成交前风险，并没有保证账户实际波动：[portfolio_risk.py:69](/Users/billpwchan/Documents/okx_algo/backend/tidebench/portfolio_risk.py:69)。

最小验收：两个不同市场、各声明 70% 资本的组同时激活，账户按明确政策接受、缩放或拒绝；交换调度顺序不能无解释改变预算。相关 sleeve、同向现货／永续、已有持仓和待执行承诺共同进入事务准入。无需要求所有模型实现风险相等，但必须准确陈述控制对象。

## 策略与成交真实性：已有机制，尚无可批准资本的证据

当前趋势、回归、突破、轮动和 funding carry 等机制可以运行，不能简单称为空架子。完整冻结输入、成本情景和负面结果披露也有实际研究价值。

但是，增加 family、指标或样本冠军不会补上可靠收益来源。交易员仍需要知道收益来自市场 beta、方向选择、仓位缩放、funding／basis、换手还是执行假设，并与相应基准比较。尚未建立稳健 alpha，不等于平台必须删除这些策略；正确产品状态应是可证伪假设与适用条件，不能表达为推荐部署赢家。

v0.9 的 28 项使用两个相邻半年窗口、当前选定的三个市场以及相关参数／成本情景，是探索性证据。公开全部结果和 trial／holdout 治理有价值，但不自动消除选择偏差；原始研究展示过参数搜索可以对无预测能力的序列产生漂亮样本内结果。[Bailey 等原始研究](https://www.davidhbailey.com/dhbpapers/overfitting.pdf)

成交仍是下一 open 或 quote 的本地模拟，全额填单没有建立排队、部分成交、冲击和资金容量。对日频轮动也需要按目标规模校准成本、成交可达性与结果敏感性，不需要先追求微秒级工程。

最小验收应声明目标资金规模和交易时钟，记录可用流动性证据、决策到成交时间、真实 spread 与观察到的模拟差异；拒绝超出已校准容量的结论。研究基准与敞口差异应明确，不用全样本风险缩放冒充可执行对照。没有成立的 edge 时保留失败结果。

## 本轮对公开结果的独立复核

原始捕获库位于已有 managed worktree 的忽略数据目录，未作为公开资产发布。本轮从捕获读取数据，核对产物，并只重构一个案例：

- BTC、ETH、SOL 各 4,380 根 bar，确认状态、连续 4H 时钟及内容哈希与公开报告一致。
- 当前 implementation、plan、runner 以及公开 ordered-results hash 均匹配。
- **独立重构 1／28 项**：`risk_momentum_84 / check_B / fee20 / slip10`，输入与完整结果哈希精确匹配。

| 指标 | 独立重算值 |
|---|---:|
| 净收益 | +0.169187974% |
| 最大回撤 | 5.391659958% |
| 实际账户年化波动 | 8.973649479% |
| 平均 gross 敞口 | 13.318520390% |
| 换手／初始权益 | 16.754890120× |
| 费用 | 335.097802395 USDT |
| 订单 | 292 |
| 执行拒绝 | 0 |
| 最终库存 | 空 |

这复核的是捕获及同一实现的计算可重复性，不是另写独立交易模拟器证明全模型正确。它也不证明历史成员完整、真实成交可达、统计显著性或盈利优势。没有把此前全 28 项回放声明当成本轮全部重跑。

A 的主规格双倍成本收益约 −8.453%，B 的邻近 42-return 规格亏损，降低回撤同时降低敞口；反证在原报告中真实保留。B 实际波动 8.97% 高于约 8% 的计划账户参考，也说明模型目标不是实际风险上限。[方法与反证](../portfolio-risk-research.md)

公开真实行情 smoke 仅 5.896 秒；策略当时选现金，零策略订单，另外两笔是单独的手动 paper 成交探针。这证明集成与现金路径，不证明策略经历实际进入、重平衡、退出、补偿或长期运行。[原始 smoke](v0.9.0-public-feed-paper-smoke.json)

[独立回放证据](fresh-review-0.9.0/risk-replay.json)

## 下一轮的优先顺序与完成定义

1. **先闭合三项 P1。** 以本轮反例作为验收：未结费用与保护动作分离且经济守恒；历史与 managed 共享失败政策；主桌直接呈现持久事故及残余风险。此之前不以新策略数量或发布测试总数认定专业闭环完成。
2. **完成研究与共同账户的经济契约。** 历史成员／生命周期、明确资本承诺、账户集中风险准入、收益来源与对应基准、可观察成本校准。组合 allocator 的具体形式可以选，但不应隐式取决于谁先执行。
3. **建立持续 forward 对照。** 记录研究预期与实际模拟决策／成交／风险的差异，覆盖进入、重平衡、退出、funding、重启及补偿。积累全时间窗统计，而非只展示当前最多 500 条观察的 summary。[forward_performance.py:64](/Users/billpwchan/Documents/okx_algo/backend/tidebench/forward_performance.py:64)
4. **再按明确目标用户与规模完成运维验收。** 定义最大组数／市场数／保留历史／资金规模，验证长期数据年龄、未结义务、恢复、错误和尾部耗时。SQLite 单写者对当前工作区不是天然错误；需要测量目标负载，再决定是否拆分或迁移。多周可靠性与 alpha 是不同验收，不能彼此替代。

## 本轮边界与可复现证据

主应用源码、交易配置、主库用户和财务记录没有修改；原预览仍为 v0.9.0 并保持就绪。当前实际预览停在 Initial setup，没有创建管理员，因此交互判断主要来自实际路由和源码；此前发布浏览器测试是既有证据，不冒充本轮完整交互验收。

本轮没有重跑 730 项后端与 24 项浏览器发布测试。独立 agent 提供的汇总脚本随后由主审实际执行，四个 case 均完成并断言对应反例；全部使用临时数据库，MockTransport 禁止外部请求。

在当前审查提交与项目根目录运行：

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/audit/fresh-review-0.9.0/reproduce.py
```

脚本使用已锁定项目环境和已有测试 fixture，输出合成账户结果。它是审计复现工具，未来修复后的结果应改变，不能继续把反例出现作为新版本通过条件。

- [隔离复现脚本](fresh-review-0.9.0/reproduce.py)
- [审计 manifest 与文件哈希](fresh-review-0.9.0/manifest.json)
- [资金费率反例](fresh-review-0.9.0/funding-gap.json)
- [历史／forward 政策差异](fresh-review-0.9.0/history-forward-gap.json)
- [事故／主桌差异](fresh-review-0.9.0/alert-gap.json)
- [独立捕获回放](fresh-review-0.9.0/risk-replay.json)

这些产物记录评估结果，本轮没有修复应用，也没有发布新的产品版本。
