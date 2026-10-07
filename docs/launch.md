# Launch notes and distribution draft

Publish the repository and release first. The text below is a draft for the maintainer to adapt; Tidebench does not automatically post to social networks or message communities.

## Repository positioning

**Short description:** An inspectable crypto research & paper-trading workspace. OKX data, reproducible backtests, transactional risk controls. Self-hosted.

**Topics:** crypto, quantitative-finance, algorithmic-trading, backtesting, paper-trading, okx, self-hosted, fastapi, react, python, typescript.

## Suggested launch post

I built Tidebench, an open-source crypto research and paper-trading workspace.

The question I wanted it to answer: can I trace a result back to its data and execution assumptions, then reproduce it without fetching a different dataset?

It starts with OKX public data, runs long-only spot backtests with explicit fees and slippage, saves input snapshots, and provides a local paper ledger with durable idempotency and risk controls. It also includes an explicit synthetic mode that works without exchange access or keys.

This is a developer preview. It does not submit real exchange orders, promise profitable strategies, or claim institutional execution fidelity. I would especially welcome reviews of the causal replay tests, accounting invariants and data-quality behavior.

Repository: https://github.com/billpwchan/tidebench

## 演示脚本

1. 以明确标识的 Example/Synthetic 模式打开工作台。
2. 选择 BTC-USDT / 1H，修改 SMA 参数与手续费。
3. 执行回测，展示样本期间、含成本收益、回撤与成交流水。
4. 打开 manifest，导出结果，执行 snapshot replay。
5. 在独立示例模拟账户提交订单，展示账本、风控和停止后的拒单。
6. 说明真实 OKX 行情可按区域连接，而此演示既不是实盘，也不是投资业绩。

## Sustainable discovery

Use real screenshots, clear installation steps, a small readable core and reproducible examples. Link directly to contribution entry points and limitations. Release notes should say what changed and how it was verified. Do not buy stars, invent users, copy unsupported performance claims, or post the same advertisement into unrelated communities. Stars are a possible outcome of useful software and credible maintenance, not a guaranteed launch metric.
