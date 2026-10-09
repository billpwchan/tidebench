# v0.10.0 独立机构研究与交易风险复审

审查日期：2026-10-09（Asia/Singapore）  
范围：当前未提交的实现，指定现货与线性 USDT 永续，历史研究、公开数据证据、本地模拟。基线 HEAD：`6436a279e0b6fc9e7fe0500ab6544ee14bf369c7`。这不是一个已提交版本的全仓库认证。

## 判断

**在本轮实际复核的经济与执行范围内，研究 → 审阅 → 本地 paper → 贡献对账 → 停止／减仓闭环已经可用；本轮发现的四项具体缺陷均经修复后独立复测。没有剩余已复现的 P1／P2 代码阻断项。** 适合有人监督、明确市场与假设的研究和 paper 实验。当前证据仍不足以批准真实资金容量、宣称稳健 alpha 或多周生产可靠性。

这个结论来自实际代码、隔离账户和反例，不来自功能数量、截图或总测试数。未把缺少实盘订单、微秒系统或分布式数据库作为本地研究产品的缺陷。审查者没有 Jane Street 任职或内部标准背景；这里的机构视角是对资金守恒、风险准入、时间因果、证据完整性和可处置事故提出公开可解释的要求。

先独立审查并形成反例，再阅读 [v0.9.0 审计](fresh-institutional-review-0.9.0.zh-CN.md)。发现后把复现交给实施者；审查者没有修改应用实现。最终复测覆盖修复后的代码，未把修复前的反例继续列为当前缺陷。

## 本轮真实反例及最终状态

P1 表示会改变关键经济／清算结果；P2 表示会破坏所声明的风险或证据准入。以下优先级描述发现时的问题，四项现在均已关闭。

| 发现 | 最小复现和原结果 | 修复后独立观察 |
|---|---|---|
| **P2：新增订单用成交前权益准入** | 10,000 USDT，运行中组合承诺 70%；手动现货买入 3,000，费用 100 bps、滑点零。订单成交，权益降至 9,970；实际使用加承诺为 **100.0902708124%**，超过硬性 100% 预算。 | 原订单返回 `account_capital_limit`，没有持仓。准入投影包含费用以及成交价相对 mark 的权益变化；现货资本按 mark 计量。该反例使用配置允许的压力费用，不声称是用户真实 OKX 费率。 |
| **P1：单位转换后 funding 错放保证金／现金** | 100 张、每张 0.01 BTC，转换为 50 张、每张 0.02 BTC，仍持有 1 BTC、保证金 50。同一边界 funding 为 1。原代码按张数比例 50/100，只扣保证金 0.5，另扣现金 0.5；总权益正确，但隔离保证金应为 49，却为 49.5，会改变清算路径。 | 按当前／冻结的经济 base 数量计算保留比例。多仓保证金 **49**、空仓 **51**，现金 funding 支付均为零；原到期数量与总费用仍保持正确。 |
| **P2：负隔离保证金抵销同一 owner 的正资本** | 同一手动 owner：100 张、每张 0.01 BTC、价格 100、50 倍，保证金 2；允许范围内的合成压力 funding 3 使保证金为 −1、权益 9,997、现金 9,998。再各买 4,999 USDT 的 SOL、ETH 现货，原代码两笔都成交，现货合计 9,998 大于权益，资本记录却显示 100%。不同 owner 的结果不同。 | 原新增风险返回 `existing_margin_breach`。任何已知隔离仓位跌破 mark 维护保证金及费用缓冲时，账户新增风险被阻断；保护性减仓仍能成交。资本使用对每个仓位的 posted margin 单独取非负值；真实负余额／损失仍保留在财务权益中。 |
| **P2：流动性窗口可漏掉已捕获的失败样本** | 330 秒内 12 个新鲜、独立、厚盘口，再加入窗口中间的一个薄 bid。只选厚盘口时原报告为 `observational_pass`、规模 100,000；选全部样本时为 `exceeds_limits`、无通过规模。 | 仅提交原 12 个 ID 也会纳入第 13 个已捕获样本，同样返回 `exceeds_limits`。报告冻结完整窗口、纳入的遗漏 ID、选择审计与边界空缺；不能用选择 ID 隐藏窗口内已知失败。 |

隔离 funding 的经济判断可核对 [OKX funding 机制](https://www.okx.com/en-us/help/perps-funding-fee-mechanism)：线性合约金额包含张数、面值、乘数与 mark；隔离模式的 funding 记入该仓位保证金。这用于校准经济含义，本地模型没有被认证为交易所全规则实现。

流动性审阅现在进入 portfolio release 的预览与批准内容：固定具体报告 ID、内容哈希、市场、申报 child／sleeve 大小及独立观察窗口，并核验其 capture 依赖。批准记录不再仅依赖界面随后展示的最新报告。其角色明确是 `informational_local_paper_review`；缺少或旧报告不能自动制造可执行容量。该审阅不会把公开盘口当成 example 源的成交证据。

## 核心工作流确实运行了什么

本轮另建完全独立的临时工作区，使用真实 runtime、SQLite、研究计算和模拟账本；MockTransport 禁止外部请求。实际完成两市场版本化研究、预览、审阅批准、激活、managed 执行、贡献对账、冻结 performance 回放、停止及操作者减仓。

- 入场批次为 `completed`，得到两个真实模拟持仓；入场和最终贡献报告都对账成功。
- 共四笔实际模拟订单：两笔入场、两笔退出。停止 controller 时资本承诺为 `retained`；实际平仓后为 `released`，终态库存为零。
- 冻结的 performance 输入窗口重新计算验证成功；合成 source 正确不能通过公开 OKX 多周运营验收。

因此，“可用”有真实执行含义，包含停止后的库存责任，而非仅能打开表单。这个样本覆盖现货组合 happy path 与资金释放，不能替代永续、补偿、重启和长期网络失败的全部运行验证；这些分支另由下述定向反例测试覆盖。

历史生命周期实现也有实质经济契约：按 `known_at` 限制上市／恢复和 warmup；暂停不制造成交；未知、过期规则或缺失 mark 不伪造可交易市场；没有受支持退市结算时保留库存并将完整权益／收益置空。有来源的现金结算与同身份线性单位转换生成幂等财务分录。当前 managed controller 不支持历史生命周期迁移时，release 硬阻断，未把历史支持误当成前向支持。

历史与 managed 共享的批次执行契约保留最小新腿拒绝、订单大小、费用预算、残差上限与失败补偿。已有同方向仓位的小额维护量在预算为零时能按已审阅残差政策保留；这不能允许新市场的无效最小腿被跳过。相关定向测试均通过。

## 证据的边界仍需交易员实际面对

**历史成员覆盖尚不能证明无幸存者偏差。** 归属导入的原文哈希能证明捕获内容未变，不能独立证明提交者宣称的发布时间或完整交易所历史成员。有限生命周期样本、规则边界和正确结算已经可执行；全市场、多年、可核验成员覆盖仍需真实数据。不能从今天的 BTC／ETH／SOL 候选直接推出历史横截面选币优势。

**收益解释已经可对账，edge 仍未成立。** 新 economics 展示实际数量上的价格损益、记录的报价短缺、费用、funding 及现金／被动 underlying 对照；beta 诊断明确只是全窗口事后描述。被动参考没有被宣称风险匹配；永续 trade-price proxy 不是已融资可执行现货；生命周期缺少对应参考时返回 unavailable。会计恒等式和可重复结果不是独立统计验证，本轮没有重新证明此前公开 28 个实验的盈利能力。

**全窗口 evidence 修复了分页失真，但没有制造运营时间。** 汇总读取整个固定 ID 范围，旧 drawdown 或经济缺口不会因只显示最近页而消失；冻结记录固定有序观察哈希和 audit 边界，追加新观察不会修改旧结果。funding 待结、覆盖间隔、时钟／财务回退会断开收益链；没有现金流时点估值时，只提供明确标记的边界估计。收益与外部现金流的计算区别可参照 [GIPS handbook](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/)，产品未宣称 GIPS 合规。

14 天真实 wall time、观察覆盖、经济覆盖与恢复事件的默认验收，是合理且可失败的观察目标。合成时钟快进和短 integration test 无法满足它。本轮没有提供 14 天真实运行证据，未测量 HTTP 可用性／尾延迟或跨主机恢复，不能将账户观察覆盖称为这些能力的证明。

**盘口窗口通过不等于资金容量批准。** 新报告能证明指定有限窗口内已捕获显示盘口对双方与申报规模的观察结果；未知的未捕获时段、隐藏流动性、撤单、补充、冲击、排队和真实部分成交仍未测量。历史研究的成本仍是场景参数，公开缓存 L2 不能回填多年容量。真实资金准入还需要目标资金规模、可执行成交数据、历史／当前成本适用性、压力与 out-of-sample 证据；这里没有留下一个假装已关闭的实盘验收项。

## 本轮验证及可复现范围

最终在修复后的工作树执行 **128 项定向测试全部通过，12.14 秒**：pending funding、account capital、historical lifecycle、research economics、forward full window、liquidity evidence、portfolio execution、managed attention。测试使用临时存储；唯一 warning 为既有 Starlette／httpx 测试客户端弃用提示。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_pending_funding.py tests/test_account_capital.py \
  tests/test_historical_lifecycle.py tests/test_research_economics.py \
  tests/test_forward_full_window.py tests/test_liquidity_evidence.py \
  tests/test_portfolio_execution.py tests/test_managed_attention.py
```

独立复现工具与当时记录的输出现发布于 [v0.10.0 复现目录](fresh-review-0.10.0/README.md)：[金额与窗口反例脚本](fresh-review-0.10.0/reproduce.py)、[观察结果](fresh-review-0.10.0/observations.json)、[连接工作流脚本](fresh-review-0.10.0/workflow.py)、[工作流结果](fresh-review-0.10.0/workflow.json)及[原始审查 manifest](fresh-review-0.10.0/manifest.json)。前者包含转换多／空仓、负保证金与窗口遗漏；后者执行真实连接工作流。脚本仅在新建临时目录建库，输出写入 `/tmp/tidebench-v10-independent-audit/`；不使用主工作区用户、数据或密钥，不发送交易所订单。已发布的 JSON 保留原审查记录，不会随脚本重跑更新。

本轮独立 agent 没有运行完整前端视觉／浏览器验收，也没有重跑所有后端测试。页面交互、手机布局、真实公开行情 smoke 和最终全项目验收应以各自的实测记录为准。并发前端文件修改引起的临时 implementation fingerprint 变化未被当成经济缺陷。

最终判断只覆盖已陈述的模型、反例及 evidence 边界；不提供“行业顶级”认证，不批准真实资金，不把没有发现更多反例等同于不存在未知缺陷。


## 补充复查：声明准入也必须计算已有手动资本

主审随后提出一个额外账户预算边界，本 agent 独立复现，**P2，现已在职业 release／activation 路径关闭**。此前的逐订单财务准入可以阻止超预算成交，但不代表事先保留的资本承诺有效。

原反例：账户权益 10,000，其他市场 SOL 的手动现货已用 3,000；新 BTC／ETH 组合申请 80%。原声明 preview 无 blocker，reserve 成功，随后账户实际使用加承诺为 **110%**。因为手动仓位在不同市场，没有同市场库存／策略归属冲突帮忙阻断；问题是声明层只累加组合 promises。该反例没有产生资金损失，但会让交易员批准不可履行的预算，直到逐腿执行才失败。

修复后，职业 release preview 和同一写事务中的 activation 都提供新鲜完整账户，按各 owner 的 `max(实际已用＋pending, 已承诺)` 加拟议新承诺检验资本、gross 和 underlying 限额。本次额外独立验证直接 declaration 接口及真实 `ProfessionalRuntime` 工作流，共六种结果：

| 已有手动使用 | 拟议新承诺／估值 | 直接 preview／reserve | 实际 release／activation |
|---|---|---|---|
| 30% | 60%，完整新鲜估值 | 接受 | 审阅、批准、激活成功 |
| 30% | 80%，完整新鲜估值 | `account_capital_overcommitted` | 同样阻断，零新承诺 |
| 30% | 60%，缺少已有市场估值 | `account_capital_valuation` | 同样阻断，零新承诺 |

接口边界应保留准确表述：底层 `AccountCapital.preview/reserve` 的 `account` 参数仍为可选；未提供账户的兼容调用只能做声明检查，不能被解释为完整财务准入。本次确认直接接口**提供完整账户时**的行为，并检查职业应用的 release／activation 确实始终传入完整账户。未来新增调用方也必须保留这个契约。

补充产物也已发布：[声明复查脚本](fresh-review-0.10.0/declaration.py)、[修复前记录](fresh-review-0.10.0/declaration-before.json)、[修复后记录](fresh-review-0.10.0/declaration-after.json)及[补充审查 manifest](fresh-review-0.10.0/declaration-manifest.json)。六种断言全部通过，耗时约 1.75 秒；没有重跑或把此前 128 项测试计数冒充本次修改后的全项目验证。此前公开 paper 产物的旧 implementation 身份保持其原意；最终项目检查另行覆盖最新源码。


公开整理只改本报告的产物链接，并从原始审查 manifest 删除个人工作树绝对路径字段 `reviewed_worktree`；历史源码哈希、原始脚本和观察 JSON 均未改写。[发布 manifest](fresh-review-0.10.0/publication-manifest.json)记录这项 manifest-only redaction、公开文件哈希，以及两个 **original pre-publication report hashes（原始发布前报告哈希）**。原 manifest 中同名报告哈希属于其各自记录阶段，不是当前已调整链接的报告哈希。

本次公开整理实际执行了 README 的三条已发布脚本命令，退出码均为 0；金额／流动性反例状态、连接工作流及六种声明断言通过。新输出只写 `/tmp`，没有覆盖仓库中当时记录的观察 JSON，也没有新增广泛测试声明。
