"""Run the real connected local paper workflow in an isolated temporary workspace."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from test_managed_portfolios import activate, runtime
from test_pro_execution import order


async def main():
    with TemporaryDirectory(prefix='v10-independent-workflow-') as tmp:
        generator = runtime.__wrapped__(Path(tmp))
        r = await anext(generator)
        try:
            group = await activate(r, capital_pct=30)
            await r.managed_portfolios.evaluate(group['id'])
            batch = r.managed_portfolios.history(group['id'])[0]
            positions = r.book.positions('example')
            quotes = await r.snapshots_for('example', [p['inst_id'] for p in positions])
            contribution = r.book.contribution_report('example', quotes)
            assert batch['status'] == 'completed'
            assert len(positions) == 2 and contribution['reconciled']
            frozen = r.book.performance.freeze('example', 'independent-audit')
            assert r.book.performance.verify(frozen['id'])['verified']
            stopped = r.managed_portfolios.stop(group['id'], 'independent-risk-operator')
            assert stopped['capital_commitment']['status'] == 'retained'
            for p in positions:
                r.book.submit(order(p['inst_id'], side='sell', quantity=p['quantity'], leverage=1,
                                    reduce_only=True), 'audit-close-'+p['inst_id'], quotes, 'audit-operator')
            with r.store.write() as conn:
                r.book.capital.reconcile('example', conn)
            terminal = r.managed_portfolios.get(group['id'])
            assert terminal['capital_commitment']['status'] == 'released'
            assert not r.book.positions('example')
            closed = r.book.contribution_report('example', quotes)
            assert closed['reconciled']
            result = {'group_id': group['id'], 'source': 'example', 'batch_status': batch['status'],
                      'actual_order_count': len(r.book.orders('example')), 'entry_positions': len(positions),
                      'entry_contributions_reconciled': contribution['reconciled'],
                      'frozen_window_replay_verified': True,
                      'frozen_acceptance_passed': frozen['body']['acceptance']['passed'],
                      'stop_preserved_promise': stopped['capital_commitment']['status'],
                      'terminal_promise': terminal['capital_commitment']['status'],
                      'terminal_positions': len(r.book.positions('example')),
                      'terminal_contributions_reconciled': closed['reconciled']}
        finally:
            await generator.aclose()
    Path('/tmp/tidebench-v10-independent-audit/workflow.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
