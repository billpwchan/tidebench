# v0.10.0 共享权益与硬资本边界独立复查

[补充报告](../fresh-institutional-review-0.10.0-capital-boundary-addendum.zh-CN.md)区分原本正确的硬风险拒绝和随后提议的新执行政策。本目录所有 probe 都针对 **原始 `4b0d466e82b486256ba71006605bdd223592ec36`**；任何新政策的检查必须另建证据，不能改标本目录记录。

- [original-observations.json](original-observations.json)：独立 real runtime/database/orders/funding 复现，原 `.5/.5` 情景在第 23 步失败；预先声明的 `.4/.4` 情景在相同 source 上通过 60 个四小时步骤。
- [original-assessment.json](original-assessment.json)：从原始记录严格验证两笔其他 owner fills、共享权益变化、费用、mark、硬限额及反事实计算。
- [elapsed-failure-4b0d466.json](elapsed-failure-4b0d466.json)：主审原始 603.306 秒 soak 的逐字节副本，不是本独立审查者重跑的 soak。
- [publication-manifest.json](publication-manifest.json)：固定全部新 artifact、源码和旧 evidence 的哈希。

可以先用系统 Python 只重算已记录证据的算术：

```sh
python3 docs/audit/fresh-review-0.10.0-capital-boundary/analyze-original.py \
  docs/audit/fresh-review-0.10.0-capital-boundary/original-observations.json \
  --output /tmp/tidebench-capital-boundary-assessment-rerun.json
```

真实 probe 必须在单独的原始 source archive 运行，避免将新默认执行政策误标成旧 `4b0d466`：

```sh
audit_repo="$PWD"
audit_checkout="$(mktemp -d /tmp/tidebench-capital-boundary.XXXXXX)"
git archive 4b0d466e82b486256ba71006605bdd223592ec36 | tar -x -C "$audit_checkout"
(cd "$audit_checkout" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests \
  "$audit_repo/.venv/bin/python" "$audit_repo/docs/audit/fresh-review-0.10.0-capital-boundary/original-probe.py" \
  --source-label 4b0d466e82b486256ba71006605bdd223592ec36 --weights .5 .4 --steps 60 \
  --output /tmp/tidebench-capital-boundary-observations-rerun.json)
```

使用现有开发依赖；脚本新建并清理临时 SQLite，HTTP MockTransport 拒绝外部请求，不读取 operational 用户/账号/密钥，不下交易所订单。它记录实际 `admit_order` 参数和结果，不伪造错误或修改 guard。随机运行 ID 与异步成交交错可能不同；原记录字节保持不变。四小时合成步骤是行情时间，不是运行 wall time；reserve 情景与原情景是不同预声明策略，不替换原失败。
