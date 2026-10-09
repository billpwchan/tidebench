# v0.10.0 独立复查补充：跨市场 funding 等待与不可变批次

审查日期：2026-10-09（Asia/Singapore）。本报告补充[原独立复审](fresh-institutional-review-0.10.0.zh-CN.md)，只审查随后暴露的 funding 调度故障；原报告及其证据 manifest 不改写。本次审查者没有修改应用或项目测试文件。

## 结论与原缺陷

**P2：未公布的账户 funding 被错误当成终局执行失败，现已在本次定向复现范围内关闭。** `funding_pending` 阻止新增风险是正确的；原 managed `_resume` 将此等待条件走普通失败补偿路径，取消仍有效的原子腿，甚至平掉已真实成交的第一腿。它不绕过资金准入，但会把外部证据延迟变成组合失败和非预期往返成交，破坏已批准执行意图的持续性。

主审在原 commit `205f89b2d7ed671ff52de64921cb58d4ccb04845` 上的[原失败 soak](v0.10.0-soak-funding-wait-failure.json)记录 **602.977 秒真实 elapsed time**、一次 SIGTERM restart；其中 Independent swap exposure 因另一组 funding 到期未结而失败，原 OKB 新增 command 被取消。该失败产物原样保留。本审查没有重跑这次 600 秒 soak，也不将其改标为修复后通过。

独立最小反例使用同一 commit 的隔离 `git archive`：BTC/ETH 现货组合的第一笔实际模拟 fill 已提交，在第二个原始新增 command 准入前捕获另一 owner 的 SOL 永续真实到期义务，catalog 尚无已实现 rate。实际结果为 `failed` / `compensated`：首腿被补偿平仓，第二腿取消，待结 funding 仍存在。[before 观察](fresh-review-0.10.0-funding-wait/before.json)保存冻结 body/hash、additions、原始 command key/payload 及真实终态，不通过伪造 `PlatformError` 制造故障。

## 安全恢复语义与独立实测

等待必须可见、可重启、仍受调度，但不生成新决策、重新缩放原冻结子腿、把未知 funding 当零或放松新增风险。已成交子腿及资本责任保留；原 batch ID、decision/body、targets、additions 和 command 身份保持不变。迟到证据恢复后，原 command 用当前新鲜报价和风险准入继续；真实政策、资本或执行失败仍可以补偿。Stop 必须取消待续 command 并保留库存责任，保护性减仓应始终能够走正确安全路径。

审查者独立执行同一字节的[复现脚本](fresh-review-0.10.0-funding-wait/reproduce.py)，在真实 runtime、真实 SQLite、真实模拟 fills/owner/funding 记账上控制合成时钟及 catalog 发布；HTTP MockTransport 拒绝外部请求。四个修复后 case 全部通过，耗时 **6.07 秒**；旧 commit 的预期失败断言通过，耗时 **1.66 秒**。[after 观察](fresh-review-0.10.0-funding-wait/after.json)保留每个 case 的具体身份与金额。

| 定向 case | 独立实测结果 |
|---|---|
| 第一个新增子腿前，其他市场 funding 未公布 | group 为 `running`、batch 为 `adding`，原子腿均未新增成交；重复评估仍等待，具有 `funding_pending` attention；重建 runtime 后冻结身份与零 fill 保持。 |
| 第一腿已成交，第二腿前 funding 未公布 | 原第一笔 fill 保留，没有补偿；重复评估与 runtime 重建都保留相同 body/additions/command key/payload/hash。迟到 catalog 发布由真实 funding sync 获取，原批次 `completed`；重复调用没有新增订单，每个原 command 对应唯一 order。 |
| 部分成交等待时 Stop 与保护性平仓 | Stop 后 batch `canceled`；保护性 close 在其他 owner funding 仍待结时实际成交。后台收到迟到发布后不恢复原新增 command，组合保持 `stopped`。 |
| 等待恢复后真实执行拒绝 | 真实风险政策修改触发 `strategy_policy_changed`；group `failed`、batch `compensated`，本组库存平仓，补偿 command 完成；重复评估没有重复订单。 |

本次迟到 funding 为 **1.32561205530 USDT**，按真实 fixture 的张数、base 单位与历史 mark 算出；每个隔离 case 仅结算一个已捕获义务，重复 settle 返回空。两个 resume case 和真实错误补偿 case 的贡献报告均对账成功。这里明确把最后一个 case 的政策变化视为真实执行拒绝，而不是仅验证“所有异常都等待”。

研究时已预留 20% sleeve 空间，以隔离等待语义和真实 funding 扣款后的资本变化；这不修改已冻结的目标。刚好用满预算的批次在扣款后可能正当被资本准入拒绝，本报告没有要求在资金不足时强制继续。

## 证据身份与边界

[本专项新发布 manifest](fresh-review-0.10.0-funding-wait/publication-manifest.json)固定本次脚本、before/after JSON、补充报告、原失败 soak 的哈希，以及原 commit 与修复工作树的源码哈希。修复后记录仍是 `205f89b… + funding-wait working-tree fix`，不是伪造一个尚不存在的修复 commit。旧 [发布 manifest](fresh-review-0.10.0/publication-manifest.json)及所有原观察保持原字节；两个旧报告哈希继续明确标为 original pre-publication report hashes，没有重新绑定到本次源码。

在这四个实际断言覆盖的等待／恢复／停止／真错误范围内，没有剩余已复现的本故障 P1/P2 阻断项。**这不是对新源码的长期运行认证**：本次未运行广泛 suite、前端验收或修复后 600 秒 soak；短时间合成输入不能证明真实公开来源的发布延迟分布、14 天连续运行、venue 容量或生产 SLA。后续这些实测必须各自固定新源码身份，旧 source 的实验结果不能改标为当前 source。
