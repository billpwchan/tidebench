# v0.10.0 v2 allowance 独立复查

[补充报告](../fresh-institutional-review-0.10.0-v2-allowance-addendum.zh-CN.md)只覆盖新版本化执行政策及其独立反例。最终运行时代码固定为 **`d9418062b6521dcb3590971d786553a06c86fd8d`**，不是随后仅修改审计文档的 Git 标签。[发布 manifest](publication-manifest.json)记录每份 artifact、该 commit 的 backend 文件哈希，以及所有旧报告/bundle 的未变哈希。

最终记录：[8 项 runtime/重启/Stop/保护断言](lineage-after.json)、[一致哈希下放大 replacement 的修复后拒绝](semantic-after.json)、[9 项真实 BackupService lineage validator 断言](restore-validator.json)、[原 `.5/.5` 情景 60 步加速前向记录](forward-observations.json)。[3 项额外测试](targeted-tests.json)使用实施者随后新增的 worktree 测试节点，不冒称这些测试文件包含在 runtime commit 内。

所有独立脚本只在新建临时 SQLite/clone 上操作，HTTP MockTransport 拒绝外部请求，不读取 operational 用户/账号/密钥，不提交交易所订单。重跑输出只写 `/tmp`，不会覆盖仓库记录。随机 ID 与 async 成交交错可能变化；四小时合成行情步骤不是 wall-time acceptance。

在固定运行时代码 checkout、已安装开发依赖的仓库根目录，可执行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0-v2-allowance/tidebench-v10-v2-lineage-eight.py \
  --source-label d9418062b6521dcb3590971d786553a06c86fd8d --output /tmp/tidebench-v2-lineage-rerun.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0-v2-allowance/tidebench-v10-v2-semantic-probe.py \
  --expect fixed --output /tmp/tidebench-v2-semantic-rerun.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0-v2-allowance/tidebench-v10-v2-restore-validator.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0-v2-allowance/forward-probe.py \
  --source-label d9418062b6521dcb3590971d786553a06c86fd8d --weights .5 --steps 60 \
  --output /tmp/tidebench-v2-forward-rerun.json
```

若当前 source 已改变，先在单独临时目录 `git archive d9418062b6521dcb3590971d786553a06c86fd8d` 并解压，使用当前 repo 的 `.venv/bin/python` 和本目录脚本的绝对路径，从那个临时目录运行以上命令。脚本实际依赖的五个 fixture/helper 函数字节已核对与该 commit 相同，见 [fixture-source-binding.json](fixture-source-binding.json)。

本审查确实发现了 draft v2 的 P2：同时改 command quantity/hash 和 plan quantity/reference/hash 后，loader 只核对引用与合计，会接受超出原剩余量的 replacement。[semantic-before.json](semantic-before.json)是实际原记录；`passed:true, expected:gap` 表示预期缺陷被复现，不表示 draft 安全。[lineage-before.json](lineage-before.json)是该 draft 仅八项基础检查通过的旧记录，未绑定到修复后 commit。

[semantic-before.patch](semantic-before.patch)保存未提交 draft 的原始 backend 字节差异，基线 `4b0d466e82b486256ba71006605bdd223592ec36`；[source manifest](semantic-before-source.json)保存当时原始文件哈希。可以在新临时 archive 中 `git apply` 该 patch，再从那里运行 semantic script 的 `--expect gap`。本次已实际验证 patch 干净应用并匹配记录源码哈希，再执行原缺陷复现，见 [独立 patch replay](semantic-before-publication-replay.json)。

```sh
audit_repo="$PWD"
audit_checkout="$(mktemp -d /tmp/tidebench-v2-before.XXXXXX)"
git archive 4b0d466e82b486256ba71006605bdd223592ec36 | tar -x -C "$audit_checkout"
(cd "$audit_checkout" && git apply "$audit_repo/docs/audit/fresh-review-0.10.0-v2-allowance/semantic-before.patch" \
  && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests \
  "$audit_repo/.venv/bin/python" "$audit_repo/docs/audit/fresh-review-0.10.0-v2-allowance/tidebench-v10-v2-semantic-probe.py" \
  --expect gap --output /tmp/tidebench-v2-before-rerun.json)
```

本目录没有新 600 秒 soak 或完整 HTTP ZIP restore 证明。9 项恢复检查调用实际 `BackupService._validate_portfolio_allowance`，范围严格是 cloned financial SQLite 的实际 restore validator。原资本失败与 `.4/.4` reserve 场景在[先前独立目录](../fresh-review-0.10.0-capital-boundary/README.md)，仍属于原 `4b0d466`；新 v2 的 `.5/.5` 加速结果不替换原 603.306 秒 adverse elapsed failure。
