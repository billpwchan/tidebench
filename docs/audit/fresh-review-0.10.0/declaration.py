"""Additional declaration audit, isolated storage; run with PYTHONPATH=backend:tests."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store, dumps
from test_account_capital import definition
from test_managed_portfolios import approve, research, runtime
from test_pro_execution import order, snapshot


def prepared(root):
    book = SimulationBook(Store(root / 'declaration.sqlite3'))
    with book.store.write() as conn:
        conn.executescript('''
          CREATE TABLE IF NOT EXISTS managed_portfolios(id TEXT PRIMARY KEY,source TEXT,status TEXT,manifest TEXT,manifest_hash TEXT);
          CREATE TABLE IF NOT EXISTS portfolio_batches(id TEXT PRIMARY KEY,group_id TEXT);
          CREATE TABLE IF NOT EXISTS portfolio_commands(id TEXT PRIMARY KEY,batch_id TEXT,status TEXT);
        ''')
    book.set_risk('example', {'fee_bps': '0', 'slippage_bps': '0', 'max_order_notional': '10000'}, 'audit')
    quote = snapshot('SOL-USDT')
    quote['instrument']['base'] = 'SOL'
    book.submit(order('SOL-USDT', quantity='30'), 'audit-manual-thirty', {'SOL-USDT': quote}, 'alice')
    return book, quote


def declaration_case(root, pct, missing=False):
    book, quote = prepared(root)
    account = book.account('example', {} if missing else {'SOL-USDT': quote})
    preview = book.capital.preview('example', definition(pct), account=account)
    expected = 'account_capital_valuation' if missing else 'account_capital_overcommitted' if pct == 80 else None
    assert (expected in preview['blockers']) if expected else not preview['blockers']
    reserved, rejected = False, None
    try:
        with book.store.write() as conn:
            book.capital.reserve(conn, 'example', 'portfolio:new', definition(pct), 'audit', account=account)
            reserved = True
    except PlatformError as exc:
        rejected = exc.code
    assert reserved == (expected is None)
    return {'proposed_pct': pct, 'missing_valuation': missing, 'blockers': preview['blockers'],
            'reserved': reserved, 'rejected': rejected, 'actual_admission': preview['actual_admission']}


async def professional_case(root, pct, missing=False):
    generator = runtime.__wrapped__(root)
    r = await anext(generator)
    try:
        r.book.set_risk('example', {'fee_bps': '0', 'slippage_bps': '0', 'max_order_notional': '10000'}, 'audit')
        quote = snapshot('SOL-USDT')
        quote['instrument']['base'] = 'SOL'
        r.snapshots[('example', 'SOL-USDT')] = quote
        r.book.submit(order('SOL-USDT', quantity='30'), 'audit-existing-manual', {'SOL-USDT': quote}, 'alice')
        run = await research(r, capital_pct=pct, research_policy={'fee_bps': '0', 'slippage_bps': '0'})
        if missing:
            r.snapshots.clear()
        preview = r.portfolio_releases.preview(run['id'])
        expected = 'account_capital_valuation' if missing else 'account_capital_overcommitted' if pct == 80 else None
        assert (expected in preview['blockers']) if expected else not preview['blockers']
        activated, rejected = False, None
        try:
            release = approve(r, run)
            r.portfolio_releases.activate(release['id'], 'audit')
            activated = True
        except PlatformError as exc:
            rejected = exc.code
        assert activated == (expected is None)
        return {'proposed_pct': pct, 'missing_valuation': missing,
                'release_blockers': preview['blockers'], 'activated': activated, 'rejected': rejected,
                'commitments_count': len(r.book.capital.commitments('example', active_only=True)),
                'actual_admission': preview['capital_admission']['actual_admission']}
    finally:
        await generator.aclose()


async def main():
    results = []
    with TemporaryDirectory(prefix='v10-declaration-independent-') as tmp:
        root = Path(tmp)
        for pct, missing in ((60, False), (80, False), (60, True)):
            direct, professional = root / f'direct-{pct}-{missing}', root / f'professional-{pct}-{missing}'
            direct.mkdir(); professional.mkdir()
            results.append({'direct': declaration_case(direct, pct, missing),
                            'professional': await professional_case(professional, pct, missing)})
    Path('/tmp/tidebench-v10-independent-audit/declaration-after.json').write_text(json.dumps(results, indent=2))
    print(json.dumps([{'pct': r['direct']['proposed_pct'], 'missing_valuation': r['direct']['missing_valuation'],
                       'direct_reserved': r['direct']['reserved'], 'release_activated': r['professional']['activated'],
                       'direct_blockers': r['direct']['blockers'], 'release_blockers': r['professional']['release_blockers']} for r in results], indent=2))


if __name__ == '__main__':
    asyncio.run(main())
