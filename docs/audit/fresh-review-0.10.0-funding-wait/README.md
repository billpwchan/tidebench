# v0.10.0 funding 等待专项独立复查

[补充报告](../fresh-institutional-review-0.10.0-funding-wait-addendum.zh-CN.md)说明发现、修复语义及验证边界。[before.json](before.json)记录原始 `205f89b2d7ed671ff52de64921cb58d4ccb04845` 的真实失败；[after.json](after.json)记录该基线上的 funding-wait 修复工作树，具体源码 SHA-256 保存在 JSON 和[新发布 manifest](publication-manifest.json)中。`passed: true` 表示该脚本对相应 `--expect` 的断言通过；before 并不表示旧实现安全。

在仓库根目录使用现有开发依赖执行修复后复现：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0-funding-wait/reproduce.py \
  --expect fixed --source-label local-rerun --output /tmp/tidebench-funding-wait-rerun.json
```

脚本使用 `tests/test_managed_portfolios.py` 中真实 runtime/research fixture，创建并清理独立临时目录及 SQLite。HTTP MockTransport 拒绝外部请求；只处理 example 合成行情、本地模拟订单和合成已实现 funding 发布，不读取 operational 用户、账号或密钥，不提交交易所订单。结果只写显式指定的 `/tmp` 路径，重跑不会覆盖已发布观察文件。各运行的随机 ID 可以不同。

原始失败可在**单独的临时目录**解压原 commit 后重现。需要该 commit 在本地 Git 对象中存在；以下不修改当前 checkout：

```sh
audit_repo="$PWD"
audit_checkout="$(mktemp -d /tmp/tidebench-funding-wait-before.XXXXXX)"
git archive 205f89b2d7ed671ff52de64921cb58d4ccb04845 | tar -x -C "$audit_checkout"
(cd "$audit_checkout" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests \
  "$audit_repo/.venv/bin/python" "$audit_repo/docs/audit/fresh-review-0.10.0-funding-wait/reproduce.py" \
  --expect before --source-label 205f89b2d7ed671ff52de64921cb58d4ccb04845 \
  --output /tmp/tidebench-funding-wait-before-rerun.json)
```

同一脚本字节用于本次 before/after。等待阶段要求 batch、body、targets、additions 哈希、原始 command ID/key/payload/hash 保持一致；真实已成交子腿保留，无新增 fill。两个 resume case 重建 runtime 后仍等待，随后由真实 `sync_funding` 读取迟到的 catalog 发布并恢复原子腿一次。其他 case 检查 Stop/保护性减仓，以及真实 risk-policy 变化引发失败补偿。研究配置预留 20% sleeve 空间，避免真实 funding 扣款独立触发资本不足；恢复仍须通过当前报价和风险准入。

本目录是独立定向复现，不是新 600 秒 soak、广泛测试或生产可靠性认证。旧 [v0.10.0 独立复审目录](../fresh-review-0.10.0/README.md)及其报告/manifest/源码哈希保持原字节，新 manifest 仅增加本专项的证据链。
