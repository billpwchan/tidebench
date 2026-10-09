# v0.10.0 独立复查补充：共享权益变动下的硬资本边界

审查日期：2026-10-09。此报告针对原 commit `4b0d466e82b486256ba71006605bdd223592ec36`，补充[原机构视角复审](fresh-institutional-review-0.10.0.zh-CN.md)与[funding 等待补充](fresh-institutional-review-0.10.0-funding-wait-addendum.zh-CN.md)。原已发布报告、源码身份和 manifest 保持原字节；没有把旧结果绑定到后续新执行政策。

## 判断

**本次 `portfolio_capital_limit` 是共享权益已经改变后的正确硬准入拒绝；没有复现“规划遗漏当前自己的 fee/spread”缺陷。** 原 `reduce_group_v1` 明确要求每个冻结子腿重新通过新鲜事务准入，并将后续真实风险拒绝视为终局失败、执行组补偿。这是该既定执行政策下的真实 adverse event，不能通过微小金额容差、取消 guard 或将原失败改标为成功来消除。

同时，原满额 `.5/.5` cash sleeve 在多个 owner 共用权益时会因其他组合真实成本变化而失败。这限制该情景的稳定运行用途；“模型符合硬限额”不等于“原满投入情景长期不会停止”。如果产品增加可审阅、受限、不可变证据完整的 replacement/supersession 行为，应作为**新的版本化执行政策**，而非宣称原守卫存在错误。

## 原 elapsed 失败及独立复现

主审原[603.306 秒失败 soak](fresh-review-0.10.0-capital-boundary/elapsed-failure-4b0d466.json)固定原 source、一次 SIGTERM restart 与三组真实本地 paper 账户；ETH SOL cash basket 失败，两组仍运行。funding wait 此次已经恢复，但这不能使资本失败被忽略。该原始 JSON 原样保留，本审查者没有重跑这次 600 秒 elapsed 测试。

独立使用同一 commit 的 `git archive`、隔离临时 SQLite 和真实 runtime，逐步运行相同三组定义、4 小时合成行情时钟、真实 `execution_once` 与并发 managed 评估。原 `.5/.5` ETH/SOL sleeve 同样在第 **23** 步因实际资本准入失败，组状态 `failed`、batch `compensated`，ETH 与 SOL 库存平掉，贡献报告对账成功。复现没有伪造错误，完整原观察保存在 [original-observations.json](fresh-review-0.10.0-capital-boundary/original-observations.json)。

资金逐项原因由[独立算术脚本](fresh-review-0.10.0-capital-boundary/analyze-original.py)从真实准入记录断言，见[计算结果](fresh-review-0.10.0-capital-boundary/original-assessment.json)：

| 原始测量 | USDT / 数量 |
|---|---:|
| 本组 additions 冻结时账户权益 | 10003.0600614599962120 |
| 本组已用 marked capital | 799.8367862391471304 |
| 8% 本组剩余预算，已涵盖完整入场成本 | 0.4080186776525666 |
| 待入场 ETH 数量 | 0.00014392 |
| ETH 实际要求 cash，含 fee | 0.4079976652752 |
| 冻结后其他 owner OKB fill 的 equity 损失，fee＋fill-to-mark | 0.0036378485000 |
| 冻结后其他 owner BTC fill 的 equity 损失，fee＋fill-to-mark | 0.0059147452388882 |
| ETH 准入前权益 | 10003.0505088662573238 |
| ETH 投影成交后权益 | 10003.0498556330468702 |
| ETH 投影成交后本组资本 | 800.2441306712118768 |
| 此时 8% 硬上限 | 800.2439884506437496 |
| 实际硬限额超额 | 0.0001422205681272 |

两笔真实其他-owner fill 的损失之和精确等于冻结后权益下降 **0.0095525937388882**；funding 在这个区间未改变，quote/mark 与合成时间也未改变。ETH 的 fee/spread 已在规划中计入：所需 cash 小于冻结预算。仅做算术反事实、保留冻结权益时，这个 ETH candidate 的 post-fill sleeve 使用为 **7.999993782033%**；真实改变后的权益使使用变为 **8.000001421772%**。反事实不是实际成交或虚构账户状态，不作为通过证据。

未补 ETH 的目标残差仅 0.4085331804073478 USDT，约冻结 sleeve 的 **0.05105099%**。金额小不能使超硬限额成交合法；它解释为什么未来一项明示的、保留原 targets/residual limit 的较小 replacement 政策可能改善运营体验，而不是许可静默改写原冻结 key/payload。

## 独立的预声明 reserve 情景

在同一原 source 上，脚本在研究／批准**之前**明确把现金组合改为 `.4/.4`，留下 **20% sleeve cash reserve**；其 capital promise 仍为 8%，另两组定义保持一致。该独立情景完成 **60 个四小时步骤（240 小时合成行情）**，三组均为 `running`、零记录的 admission rejection，最终贡献对账成功。

这个情景是有意义的 predeclared 稳定用途样本，和原 `.5/.5` 的策略不同；它不能替换原满额情景、原 elapsed 失败或证明修改后执行政策。当前审查未完成此 reserve 情景的 600 秒 wall-time soak，也没有把加速 240 小时换算成生产可用性、公开 funding 发布分布或 14 天运营验收。

## 身份与后续判断

[新发布 manifest](fresh-review-0.10.0-capital-boundary/publication-manifest.json)记录本次原始 probe、观察、算术脚本/结果、主审原 elapsed failure 的逐字节哈希；原五个关键源码哈希来自实际 probe，明确绑定 `4b0d466`。此前两套独立审查的所有文件及原发布前报告哈希继续保留，未重新写标签。

本事件不被重新命名为“P1/P2 资金守卫 bug 已修复”。当前证据支持原 hard guard 与既定 v1 失败政策的正确性，也暴露满投入、多 owner 情景的实用限制。后续任何 `v2` allowance supersession 必须独立验证其明示版本、较小数量、次数上限、完整估值、不可变 lineage、原始最小腿及残差限制、历史/前向一致性、重启与 Stop，以及真正风险失败仍补偿；其原满投入 elapsed acceptance 也必须使用新冻结 source 单独记录。
