# v0.10.0 独立审查复现

这组产物保留审查时的原始脚本、观察 JSON 与历史源码哈希。`manifest.json` 仅删除个人绝对路径字段 `reviewed_worktree`；其他记录未修改。`declaration-manifest.json` 保留原字节。`publication-manifest.json` 另记这项 redaction、原始／发布 manifest 哈希和公开产物哈希。

两个历史 manifest 的 `artifact_sha256["fresh-institutional-review-0.10.0.zh-CN.md"]` 是 **original pre-publication report hashes（原始发布前报告哈希）**，分别对应补充复查前和公开整理前的报告，不是当前调整产物链接后的报告哈希。历史 `source_sha256` 描述当时审查的未提交代码；不声称匹配当前实现。

在项目根目录、已安装项目依赖后按下面顺序运行；第一条会建立共同输出目录，后两条使用它：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0/reproduce.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0/workflow.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:tests .venv/bin/python docs/audit/fresh-review-0.10.0/declaration.py
```

脚本只创建隔离的临时 SQLite 库，结束后删除库。市场输入为合成 fixture；真实 runtime 工作流用 MockTransport 禁止外部请求。它们不使用主工作区用户、账户、密钥或财务数据，不发送交易所订单；审查脚本不修改 backend、frontend 或测试。

输出写入 `/tmp/tidebench-v10-independent-audit/`，可能覆盖该目录的上次脚本输出；不会改写本公开目录。标识符和捕获哈希含新生成 ID，重跑 JSON 不要求逐字相同，应核对金额、拒绝原因、状态和脚本断言。

- `reproduce.py`：费用后资本准入、转换多／空仓 funding、负隔离保证金准入和流动性遗漏样本。
- `workflow.py`：真实研究、审阅、激活、模拟成交、贡献对账、冻结回放、停止与资金释放。
- `declaration.py`：直接 declaration 及职业 release／activation 的 60% 接受、80% 拒绝、缺估值阻断，共六种断言。

[报告](../fresh-institutional-review-0.10.0.zh-CN.md)区分当时的 128 项定向测试、补充六种复查与证据边界。本次公开整理没有重跑广泛测试，也不将重跑合成脚本当成真实市场容量、alpha 或多周运营验收。
