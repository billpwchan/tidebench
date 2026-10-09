"""Independent audit reproducer; writes only disposable /tmp databases.

Run from the repository root at the reviewed v0.9.0 commit:
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/audit/fresh-review-0.9.0/reproduce.py
All exchange requests are forbidden by MockTransport. No users are created.
"""
import asyncio
import importlib.util
import json
import tempfile
from decimal import Decimal as D, localcontext
from pathlib import Path

import httpx
from tidebench.config import Settings
from tidebench.engine import ACCOUNTING_CONTEXT, Candle
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.portfolio_research import PortfolioInput, _simulate_portfolio
from tidebench.pro_execution import SimulationBook
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, encode


def load(label, path):
    spec = importlib.util.spec_from_file_location(label, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


f = load('audit_execution_fixture', 'tests/test_pro_execution.py')
m = load('audit_managed_fixture', 'tests/test_managed_portfolios.py')


def funding():
    clock = [1_000_000]
    with tempfile.TemporaryDirectory(prefix='tidebench-fresh-funding-') as folder:
        book = SimulationBook(Store(Path(folder) / 'book.sqlite3'), clock=lambda: clock[0])
        book.set_risk('example', {'fee_bps': '0', 'slippage_bps': '0', 'liquidation_fee_bps': '0'}, 'audit')
        book.submit(f.order(), 'audit-entry-1', {f.SYMBOL: f.snapshot()})
        clock[0] = 2_000_001
        quote = f.snapshot(price='80', ts=clock[0], funding_time=3_000_000, next_funding_time=4_000_000)
        outcomes = {}
        for liquidation in (False, True):
            try:
                book.submit(f.order(side='sell', reduce_only=True), 'audit-exit-' + str(liquidation), {f.SYMBOL: quote}, liquidation=liquidation)
                outcomes[str(liquidation)] = 'filled'
            except PlatformError as exc:
                outcomes[str(liquidation)] = exc.code
        print(json.dumps({'case': 'funding', 'outcomes': outcomes, 'account': book.account('example', {f.SYMBOL: quote})}))


def historical():
    start, interval = 1767225600000, 3600000
    config = encode(PortfolioInput.model_validate(dict(name='minimum leg audit', hypothesis='Both assets must receive planned capital or fail as a group.', legs=[dict(package_id=str(i + 1) * 32, weight='.5', strategy=dict(kind='buy_hold')) for i in range(2)], capital_pct=100, fee_bps=0, slippage_bps=0, rebalance_bars=1, max_daily_loss_pct=50)).model_dump())
    legs = []
    for symbol, price, minimum, leg in zip(['BTC-USDT', 'ETH-USDT'], ['100', '100000'], ['.01', '1'], config['legs'], strict=True):
        meta = f.snapshot(symbol=symbol)['instrument']
        meta.update(base=symbol.split('-')[0], min_size=minimum)
        bars = [Candle(ts=start + i * interval, open=D(price), high=D(price), low=D(price), close=D(price), volume=D(100), confirmed=True) for i in range(4)]
        legs.append(dict(config=leg, instrument=meta, candles=bars, marks=bars, funding=[], tiers=[]))
    with localcontext(ACCOUNTING_CONTEXT):
        result = _simulate_portfolio(config, dict(source='example', bar='1H', start=start, end=start + 4 * interval, universe_scope='explicit'), legs)
    print(json.dumps({'case': 'historical_minimum', 'orders': result['orders'], 'errors': result['execution_rejections'], 'positions': result['final_account']['positions']}))


async def managed(case):
    with tempfile.TemporaryDirectory(prefix='tidebench-fresh-managed-') as folder:
        settings = Settings(data_dir=Path(folder), worker_enabled=False, _env_file=None)
        def forbidden(_):
            raise RuntimeError('External requests forbidden')
        client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
        runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
        try:
            runtime.book.set_risk('example', {'fee_bps': '0', 'slippage_bps': '0', 'liquidation_fee_bps': '0'}, 'audit')
            group = await m.activate(runtime, rebalance_bars=1)
            if case == 'minimum':
                ts = runtime.clock.now()
                async def quote(symbol, source):
                    value = f.snapshot(symbol=symbol, source=source, price='100' if symbol == 'BTC-USDT' else '100000', ts=ts)
                    value['instrument'].update(base=symbol.split('-')[0], min_size='.01' if symbol == 'BTC-USDT' else '1')
                    return value
                runtime.catalog.get_market_snapshot = quote
            else:
                original = runtime.book.submit
                def fail(order, key, snapshots, actor):
                    if order['reduce_only'] or order['inst_id'] == 'ETH-USDT':
                        raise PlatformError('audit_refusal', 'Cannot execute the second leg or compensate now.', 409)
                    return original(order, key, snapshots, actor)
                runtime.book.submit = fail
            await runtime.managed_portfolios.evaluate(group['id'])
            runtime.backups.create('audit')
            runtime.monitor_conditions()
            members = [dict(inst_id=p['inst_id'], status=p['status'], last_error=p['last_error']) for p in runtime.deployments('example')]
            ops = runtime.operations()
            print(json.dumps({'case': 'managed_' + case, 'group': runtime.managed_portfolios.get(group['id'])['status'], 'batch': runtime.managed_portfolios.history(group['id'])[0]['status'], 'positions': runtime.book.positions('example'), 'members': members, 'health': ops['health'], 'incidents': ops['incidents']}))
        finally:
            await runtime.stop()
            await client.aclose()


if __name__ == '__main__':
    funding()
    historical()
    asyncio.run(managed('minimum'))
    asyncio.run(managed('blocked_compensation'))
