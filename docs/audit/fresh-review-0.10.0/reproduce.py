"""Temp-only independent audit: run from project root with PYTHONPATH=backend:tests."""
import asyncio
import json
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tidebench.historical_lifecycle import apply_inventory_event
from tidebench.liquidity_evidence import LiquidityCalibrationInput, LiquidityCaptureInput, LiquidityEvidence
from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store
from test_account_capital import reserve
from test_historical_lifecycle import fact, populated_book
from test_pro_execution import order, snapshot

OUT = Path('/tmp/tidebench-v10-independent-audit')


def capital_case(root):
    book = SimulationBook(Store(root / 'capital.sqlite3'))
    with book.store.write() as conn:
        conn.executescript('''
          CREATE TABLE IF NOT EXISTS managed_portfolios(id TEXT PRIMARY KEY,source TEXT,status TEXT,manifest TEXT,manifest_hash TEXT);
          CREATE TABLE IF NOT EXISTS portfolio_batches(id TEXT PRIMARY KEY,group_id TEXT);
          CREATE TABLE IF NOT EXISTS portfolio_commands(id TEXT PRIMARY KEY,batch_id TEXT,status TEXT);
        ''')
    book.set_risk('example', {'fee_bps': '100', 'slippage_bps': '0', 'max_order_notional': '10000'}, 'audit')
    reserve(book, 'group70', 70)
    quote = snapshot('SOL-USDT')
    quote['instrument']['base'] = 'SOL'
    try:
        fill = book.submit(order('SOL-USDT', quantity='30'), 'audit-fee-boundary', {'SOL-USDT': quote}, 'alice')
    except PlatformError as exc:
        assert exc.code == 'account_capital_limit'
        assert not book.positions('example')
        return {'fixed': True, 'rejected': exc.code, 'positions': book.positions('example')}
    account = book.account('example', {'SOL-USDT': quote})
    with book.store.read() as conn:
        evidence = book.capital.exposure(conn, 'example', account)
    return {'fixed': False, 'fill': fill, 'equity': account['equity'], 'capital': evidence}


def conversion_case(root, side):
    case = root / side
    case.mkdir()
    book, clock, quote = populated_book(case, side=side)
    event = fact('BTC-USDT-SWAP', 'unit_conversion', 5, quantity_ratio='.5')
    event['instrument']['ct_val'] = '.02'
    application = apply_inventory_event(book, 'example', event)
    quote = dict(quote, instrument=event['instrument'], ts=clock[0], mark_ts=clock[0])
    result = book.settle_funding('example', 'BTC-USDT-SWAP', [{'ts': clock[0], 'rate': '.01', 'mark_price': '100'}], {'BTC-USDT-SWAP': quote})
    account = book.account('example', {'BTC-USDT-SWAP': quote})
    correct_margin = D(49 if side == 'buy' else 51)
    return {'fixed': D(account['positions'][0]['margin']) == correct_margin and D(result[0]['cash_payment']) == 0,
            'expected_margin': str(correct_margin), 'application': application, 'funding': result,
            'cash': account['cash'], 'equity': account['equity'], 'actual_margin': account['positions'][0]['margin']}


def negative_margin_case(root):
    clock = [1000000]
    book = SimulationBook(Store(root / 'negative-margin.sqlite3'), clock=lambda: clock[0])
    book.set_risk('example', {'fee_bps': '0', 'slippage_bps': '0', 'liquidation_fee_bps': '0',
                             'max_leverage': '50', 'max_order_notional': '10000'}, 'audit')
    btc = snapshot()
    book.submit(order(leverage=50), 'audit-margin-entry', {'BTC-USDT-SWAP': btc}, 'alice')
    clock[0] = 2000000
    btc.update(ts=clock[0], mark_ts=clock[0])
    book.settle_funding('example', 'BTC-USDT-SWAP', [{'ts': clock[0], 'rate': '.03', 'mark_price': '100'}], {'BTC-USDT-SWAP': btc})
    before = book.account('example', {'BTC-USDT-SWAP': btc})
    quotes, admitted, rejected = {'BTC-USDT-SWAP': btc}, [], None
    for symbol in ('SOL-USDT', 'ETH-USDT'):
        quote = snapshot(symbol, ts=clock[0])
        quote['instrument']['base'] = symbol.split('-')[0]
        quotes[symbol] = quote
        try:
            book.submit(order(symbol, quantity='49.99'), 'audit-margin-add-'+symbol, quotes, 'alice')
            admitted.append(symbol)
        except PlatformError as exc:
            rejected = exc.code
            break
    account = book.account('example', quotes)
    spot = sum((D(p['market_value']) for p in account['positions'] if p['inst_type'] == 'SPOT'), D(0))
    return {'fixed': spot <= D(account['equity']), 'admitted': admitted, 'rejected': rejected,
            'before_cash': before['cash'], 'before_equity': before['equity'],
            'before_margin': before['positions'][0]['margin'],
            'maintenance_margin': before['positions'][0]['maintenance_margin'],
            'after_cash': account['cash'], 'after_equity': account['equity'], 'after_spot_value': str(spot)}


class FakePublicMarket:
    region = 'audit_fixture'
    clock = 1767225600000
    thin = False

    async def _get(self, path, params):
        if path == '/api/v5/public/instruments':
            return [{'instId': 'BTC-USDT', 'instType': 'SPOT', 'state': 'live', 'baseCcy': 'BTC', 'quoteCcy': 'USDT',
                     'tickSz': '.01', 'lotSz': '.01', 'minSz': '.01'}]
        if path == '/api/v5/market/books':
            return [{'ts': str(self.clock - 10), 'asks': [['100.01', '10000', '0', '1']],
                     'bids': [['99.99', '1' if self.thin else '10000', '0', '1']]}]
        raise AssertionError('Audit cannot call other endpoints')


async def selection_case(root):
    market = FakePublicMarket()
    service = LiquidityEvidence(Store(root / 'liquidity.sqlite3'), market)
    t = market.clock
    with patch('tidebench.liquidity_evidence.time.time_ns', lambda: market.clock * 1000000), \
         patch('tidebench.liquidity_evidence.time.monotonic_ns', lambda: 1000), \
         patch('tidebench.liquidity_evidence.now_ms', lambda: market.clock):
        good = []
        for i in range(12):
            market.clock = t + i * 30000
            good.append(await service.capture(LiquidityCaptureInput(inst_id='BTC-USDT'), 'audit'))
            if i == 5:
                market.clock, market.thin = t + 165000, True
                bad = await service.capture(LiquidityCaptureInput(inst_id='BTC-USDT'), 'audit')
                market.thin = False
        selected = service.calibrate(LiquidityCalibrationInput(inst_id='BTC-USDT', capture_ids=[c['id'] for c in good], window_start=t, window_end=t+330000), 'audit')
        all_samples = service.calibrate(LiquidityCalibrationInput(inst_id='BTC-USDT', capture_ids=[c['id'] for c in good]+[bad['id']], window_start=t, window_end=t+330000), 'audit')
        assert all_samples['approved_observed_notional'] is None
        return {'fixed': selected['approved_observed_notional'] is None,
                'selected_report': selected, 'all_samples_report': all_samples,
                'omitted_capture_id': bad['id'], 'omitted_known_at': bad['known_at'], 'omitted_evidence': bad['evidence']}


async def main():
    with TemporaryDirectory(prefix='v10-independent-audit-') as tmp:
        root = Path(tmp)
        result = {'capital_after_fee': capital_case(root),
                  'conversion_long': conversion_case(root, 'buy'),
                  'conversion_short': conversion_case(root, 'sell'),
                  'negative_margin_capital': negative_margin_case(root),
                  'liquidity_selection': await selection_case(root)}
    OUT.mkdir(exist_ok=True)
    (OUT / 'observations.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'capital_fixed': result['capital_after_fee']['fixed'],
                      'conversion_long_fixed': result['conversion_long']['fixed'],
                      'conversion_short_fixed': result['conversion_short']['fixed'],
                      'negative_margin_fixed': result['negative_margin_capital']['fixed'],
                      'selected_liquidity': result['liquidity_selection']['selected_report']['status'],
                      'all_liquidity': result['liquidity_selection']['all_samples_report']['status']}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
