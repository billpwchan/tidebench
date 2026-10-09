# v0.10.0 独立复查补充：v2 受限 allowance supersession

审查日期：2026-10-09。最终 runtime 固定为 **`d9418062b6521dcb3590971d786553a06c86fd8d`**，code fingerprint `070666d99e2ee4194667cc6588d89f0022c7b44db9c0c86cef930985e3b122cd`。本报告补充[原复审](fresh-institutional-review-0.10.0.zh-CN.md)、[funding 等待](fresh-institutional-review-0.10.0-funding-wait-addendum.zh-CN.md)及[原资本边界](fresh-institutional-review-0.10.0-capital-boundary-addendum.zh-CN.md)；旧 artifact 和 manifest 保持原字节。本审查者没有修改应用或项目测试文件。

## 判断与政策变化

**新 `reduce_group_v2_allowance` 在本次实际断言范围内可恢复真实权益下降后的小额未成交量；审查中发现的 P2 lineage 语义校验缺口已复现并修复。没有剩余已复现的本专项 P1/P2 阻断项。** 这不是“原资本守卫修复”：原 v1 的 `.5/.5` 共享账户拒绝是正确 hard admission，其原 603.306 秒失败仍保留。v2 是明示的新执行政策，原 `reduce_group_v1` 仍可选，旧 v1 frozen batches 不被静默升级。

新政策只在真实 `portfolio_capital_limit`、完整新鲜估值、账户权益和当前 allowance 均下降且原 risk/capital policy 未变时，生成较小的未成交 replacement；最多三次。原 targets、body、additions、原 command payload/key/hash 及已经成交的 child 不改写。每个新 child 有新 key 和链接 plan hash，仍须通过实际订单准入、原最小数量、单腿累计 child 上限和原 residual limit。funding 未结、估值缺失、政策变化、账户 total/base/gross/order 限额及真实执行失败不会被泛化为继续下单许可。

## 独立发现的 P2：引用自洽仍不能允许 replacement 放大

在未提交 v2 draft 上，审查者建隔离真实 runtime，先实际成交原第一腿，再结算另一 owner 的真实合成 funding 扣款 **13.2561205530 USDT**，触发真实 `portfolio_capital_limit` 并生成 replacement。随后故意取消执行，停在 replacement 首次成交前，保留真实原 fill 和未成交新 key。

在这个复制数据库中，同时把 replacement 改为原剩余量的两倍，重新计算实际 payload hash、plan 引用 hash、quantity 合计及 plan digest，**原 loader 仍接受**。实际执行被订单 notional guard 拒绝，然后 group `failed` / batch `compensated`，产生一笔保护性补偿订单。资金守卫没有失效，但损坏的执行证据被当成普通交易失败，破坏恢复边界。[原始反例](fresh-review-0.10.0-v2-allowance/semantic-before.json)与[精确 draft patch/source 哈希](fresh-review-0.10.0-v2-allowance/semantic-before-source.json)固定该状态；不能用基础 hash 检查通过来声称完整 lineage 安全。

修复后，实际 loader 还验证方向、每市场 replacement 不超过被 supersede 的原量、至少一项严格减少、captured min/lot、原 policy anchors 及严格下降的 budget/equity 链。同一字节脚本的同一攻击得到 **`portfolio_evidence_integrity`，没有新订单，Stop 可用**，见[修复后记录](fresh-review-0.10.0-v2-allowance/semantic-after.json)。原 draft patch 已在独立 `4b0d466` archive 实际干净应用、核对全部记录 source 哈希并再现原缺陷，见[patch replay](fresh-review-0.10.0-v2-allowance/semantic-before-publication-replay.json)。

## 最终 source 的实际断言

最终脚本输出的各个 runtime 文件 SHA-256 已逐一核对与 `git show d941806…` 相同；依赖的五个 fixture/helper 函数字节也与该 commit 相同。本次独立复核没有将后来仅增加测试／文档的 Git commit 当成实验运行时代码身份。

| 实际检查 | 结果及边界 |
|---|---|
| 干净 v2 部分成交后的重启恢复 | 原 BTC fill 与所有原不可变字段保留；ETH 原未成交量 `0.80496918` 产生新量 `0.80287565`，projected target residual 约 `0.1402132%`，小于已审阅 2%。runtime 重建后只成交一个新 replacement，原 superseded keys 未成交，重复评估无重复订单，贡献对账成功。 |
| 七个重启／引用／删除攻击 | 重新 hash 后的 wrong group、wrong batch、ref key/hash 漂移、实际 replacement payload 修改、删除 plan、删除 replacement command，全部阻断新 fill；每个损坏 clone 都能 Stop，并通过实际保护性 close 平掉本组库存。连同干净恢复共八 case，**1.73 秒**。 |
| 协同放大 replacement | 上述 P2 反例修复后，在任何新订单前拒绝；Stop 可用，**1.40 秒**。 |
| 真实 backup lineage validator | 调用 `BackupService._validate_portfolio_allowance`：clean financial state 接受，七个攻击与无匹配 financial batch 的 rehashed plan graft 均为 `backup_integrity`。共九 case，**1.82 秒**；这是 cloned SQLite 的实际 restore validator，未冒称完整 HTTP ZIP restore。 |
| 原 `.5/.5` 三组加速前向情景 | 完成 60 个四小时步骤，三组均 `running`，最终贡献对账成功。第 23 步真实 `portfolio_capital_limit` 拒绝仍在原始 trace 中，未删除或改成一次已成交原 command。最终三组 manifest 都明确为 v2。 |

[独立脚本与全部原观察](fresh-review-0.10.0-v2-allowance/README.md)使用 synthetic inputs、实际 local book/fills/funding/owner，以及隔离临时 SQLite。HTTP MockTransport 拒绝外部请求；不使用 operational 用户／账号／密钥，不提交交易所订单。合成 funding 压力率 1% 用于制造合法模型范围内的真实权益变化，不声称是实际 OKX funding rate。

本轮还运行实施者新增的两个定向测试节点，三项断言 **3 passed in 6.86s**：真实历史 shared adapter 下 v1 补偿／v2 较小新 plan，以及真实重复权益下降最多三次重规划。这些 test nodes 是 runtime commit 后的 worktree 测试增加，不声称它们存在于 `d941806`，也不将它们混入上述独立构造 case 或冒充广泛 suite。[记录](fresh-review-0.10.0-v2-allowance/targeted-tests.json)明确区分测试 source 与固定 runtime source。

## 证据与接受边界

[新发布 manifest](fresh-review-0.10.0-v2-allowance/publication-manifest.json)分别固定 draft before、最终 after、精确 source patch、所有脚本与观察及本报告的哈希；三个旧报告和全部旧 bundle 文件保持未变。旧 original pre-publication report hashes 保留其旧阶段含义，未改标为本次 runtime。

本轮独立 proof 覆盖资金变化后的版本化续执行、immutable command lineage、runtime 重建、Stop/保护、corruption detection 和实际 backup validator。原 60 步是 **240 小时合成行情**，不等于 600 秒 elapsed soak、14 天连续运行或 venue 容量。主审正在另行运行 `d941806` 的不可变源码、原 `.5/.5` 600 秒 soak；本报告发布时没有取得其终态，不声称它通过。真实 HTTP ZIP restore、最终全项目 suite、UI、公开 feed 及长期证据必须分别使用各自的实测 artifact。
