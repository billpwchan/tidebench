"""Independent funding-wait audit; only temporary SQLite, no real HTTP/orders.

Run from a checkout with PYTHONPATH=backend:tests. The same bytes can run against
the archived pre-fix checkout with --expect before and against fixed code.
"""
import argparse
import asyncio
import copy
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

import tidebench
from tidebench.pro_execution import base_size
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store
from tidebench.strategy_registry import digest
from test_managed_portfolios import activate, runtime
from test_pro_execution import order

OUTSIDE = 'SOL-USDT-SWAP'


def command_identity(command):
    return {key: command[key] for key in
            ('id', 'phase', 'sequence', 'deployment_id', 'key', 'payload', 'payload_hash')}


def frozen_identity(batch):
    return {'id': batch['id'], 'bar': batch['bar'], 'content_hash': batch['content_hash'],
            'body_digest': digest(batch['body']), 'targets': batch['body']['targets'],
            'additions_hash': batch['additions_hash'], 'additions': copy.deepcopy(batch['additions']),
            'commands': [command_identity(c) for c in batch['commands'] if c['phase'] != 'compensate']}


def group_fills(r, group):
    symbols = {leg['inst_id'] for leg in group['manifest']['legs']}
    return [o for o in r.book.orders('example') if o['inst_id'] in symbols and o['status'] == 'filled']


def own_positions(r, group):
    symbols = {leg['inst_id'] for leg in group['manifest']['legs']}
    return [p for p in r.book.positions('example') if p['inst_id'] in symbols]


async def unpublished(*args, **kwargs):
    return []


def publish_funding(runtime, event):
    calls = []

    async def published(symbol, start, end, source):
        calls.append({'inst_id': symbol, 'start': start, 'end': end, 'source': source})
        return [event] if symbol == OUTSIDE and start <= event['ts'] < end else []

    runtime.catalog.funding_history = published
    return calls


def settled_obligations(runtime):
    with runtime.store.read() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT source,inst_id,ts,status,payment FROM pro_funding_obligations WHERE source='example' AND inst_id=? ORDER BY ts",
            (OUTSIDE,))]


async def scenario(root, inject_at, disposition, expect):
    generator = runtime.__wrapped__(root)
    r = await anext(generator)
    restored = None
    try:
        r.book.now = r.clock.now
        r.book.set_risk('example', {'fee_bps': '0', 'slippage_bps': '0', 'liquidation_fee_bps': '0'}, 'audit')
        # Leave sleeve reserve so a real funding debit cannot itself make the
        # unchanged frozen child fail its separate capital admission.
        group = await activate(r, capital_pct=30,
            legs=[{'inst_id': s, 'weight': '.4', 'strategy': {'kind': 'buy_hold'}}
                  for s in ('BTC-USDT', 'ETH-USDT')],
            research_policy={'fee_bps': '0', 'slippage_bps': '0', 'liquidation_fee_bps': '0'})
        outside = await r.quote_snapshot('example', OUTSIDE)
        assert outside is not None
        due = r.clock.now() + 1
        outside = dict(outside, funding_time=due, next_funding_time=due+3600000)
        quantity = max(D('10'), D(outside['instrument']['min_size']))
        lot = D(outside['instrument']['lot_size'])
        quantity = (quantity / lot).to_integral_value() * lot
        r.book.submit(order(OUTSIDE, quantity=str(quantity), leverage=2), 'audit-unrelated-inventory',
                      {OUTSIDE: outside}, 'audit-outside-owner')
        r.catalog.funding_history = unpublished
        original_submit = r.book.submit
        injected = {'done': False, 'adds': 0}
        original_frozen = None

        def introduce_due_obligation(payload, key, quotes, actor, **kwargs):
            nonlocal original_frozen
            if actor.startswith('strategy:') and not payload['reduce_only']:
                injected['adds'] += 1
                if not injected['done'] and injected['adds'] == inject_at:
                    original_frozen = frozen_identity(r.managed_portfolios.history(group['id'])[0])
                    r.clock.change(step_ms=2, expected_revision=r.clock.status()['revision'], actor='audit')
                    r.book.capture_funding_obligations('example', OUTSIDE,
                        dict(outside, ts=r.clock.now(), mark_ts=r.clock.now()))
                    injected['done'] = True
            return original_submit(payload, key, quotes, actor, **kwargs)

        r.book.submit = introduce_due_obligation
        await r.managed_portfolios.evaluate(group['id'])
        r.book.submit = original_submit
        assert injected['done'] and original_frozen is not None
        waiting = r.managed_portfolios.history(group['id'])[0]
        shown = r.managed_portfolios.get(group['id'])
        pending = r.book.deferred_funding.pending('example')
        assert len(pending) == 1 and pending[0]['inst_id'] == OUTSIDE
        observation = {'inject_before_add_number': inject_at, 'disposition': disposition,
                       'group_status_after_due': shown['status'], 'batch_status_after_due': waiting['status'],
                       'last_error_after_due': shown['last_error'], 'pending_funding': pending,
                       'frozen_original': original_frozen,
                       'original_identity_preserved_after_due': frozen_identity(waiting) == original_frozen,
                       'group_fills_after_due': len(group_fills(r, group)),
                       'group_positions_after_due': own_positions(r, group),
                       'command_states_after_due': [{'key': c['key'], 'phase': c['phase'], 'status': c['status'], 'order_id': c['order_id']} for c in waiting['commands']]}
        if expect == 'before':
            assert shown['status'] == 'failed' and waiting['status'] == 'compensated'
            assert not own_positions(r, group)
            return observation

        assert shown['status'] == 'running' and waiting['status'] == 'adding'
        assert frozen_identity(waiting) == original_frozen
        assert len(group_fills(r, group)) == inject_at - 1
        assert shown['attention'] is not None
        observation['waiting_attention'] = shown['attention']
        await r.managed_portfolios.evaluate(group['id'])
        assert frozen_identity(r.managed_portfolios.history(group['id'])[0]) == original_frozen
        assert len(group_fills(r, group)) == inject_at - 1

        if disposition == 'stop':
            stopped = r.managed_portfolios.stop(group['id'], 'audit-operator')
            quotes = await r.snapshots_for('example', [OUTSIDE])
            for position in own_positions(r, group):
                r.book.submit(order(position['inst_id'], side='sell', quantity=position['quantity'],
                                    leverage=1, reduce_only=True), 'audit-protect-'+position['inst_id'], quotes, 'audit-operator')
            assert not own_positions(r, group)
            assert r.book.deferred_funding.pending('example')
            before_publication_fills = len(group_fills(r, group))
            event = {'ts': due, 'rate': '.001', 'mark_price': str(outside['mark']), 'mark_ts': due,
                     'mark_price_source': 'independent_synthetic_settlement_fixture'}
            publication_calls = publish_funding(r, event)
            await r.execution_once()
            paid = settled_obligations(r)
            assert len(paid) == 1 and paid[0]['status'] == 'settled'
            assert not r.book.deferred_funding.pending('example')
            await r.managed_portfolios.evaluate(group['id'])
            terminal = r.managed_portfolios.history(group['id'])[0]
            assert stopped['status'] == 'stopped' and terminal['status'] == 'canceled'
            assert len(group_fills(r, group)) == before_publication_fills
            assert frozen_identity(terminal) == original_frozen
            observation.update(terminal_group_status=r.managed_portfolios.get(group['id'])['status'],
                               terminal_batch_status=terminal['status'], protection_fills=before_publication_fills-(inject_at-1),
                               late_funding=paid, late_publication_requests=publication_calls,
                               original_identity_preserved_final=True,
                               pending_command_states=[c['status'] for c in terminal['commands'] if c['phase']=='add' and c['order_id'] is None])
            return observation

        await r.stop()
        restored = ProfessionalRuntime(Store(r.settings.database), r.market, r.settings)
        restored.book.now = restored.clock.now
        restored.catalog.funding_history = unpublished
        await restored.managed_portfolios.evaluate(group['id'])
        restarted = restored.managed_portfolios.history(group['id'])[0]
        assert frozen_identity(restarted) == original_frozen
        assert len(group_fills(restored, group)) == inject_at - 1
        observation['restart_wait_preserved'] = True
        quotes = await restored.snapshots_for('example', [OUTSIDE])
        event = {'ts': due, 'rate': '.001', 'mark_price': str(outside['mark']), 'mark_ts': due,
                 'mark_price_source': 'independent_synthetic_settlement_fixture'}
        publication_calls = publish_funding(restored, event)
        if disposition == 'execution_error':
            await restored.sync_funding('example', OUTSIDE, quotes)
            restored.book.set_risk('example', {'max_order_notional': '10'}, 'audit-risk-reviewer')
        await restored.managed_portfolios.evaluate(group['id'])
        funding = settled_obligations(restored)
        assert len(funding) == 1 and funding[0]['status'] == 'settled'
        assert any(c['inst_id'] == OUTSIDE and c['start'] <= due < c['end'] for c in publication_calls)
        expected_payment = quantity * base_size(outside['instrument']) * D(str(outside['mark'])) * D('.001')
        assert D(funding[0]['payment']) == expected_payment
        assert restored.book.settle_funding('example', OUTSIDE, [event], quotes) == []
        final = restored.managed_portfolios.history(group['id'])[0]
        terminal_group = restored.managed_portfolios.get(group['id'])
        assert frozen_identity(final) == original_frozen
        orders_before_repeat = len(restored.book.orders('example'))
        await restored.managed_portfolios.evaluate(group['id'])
        assert len(restored.book.orders('example')) == orders_before_repeat
        if disposition == 'execution_error':
            assert final['status'] == 'compensated' and terminal_group['status'] == 'failed'
            assert not own_positions(restored, group)
            assert any(c['phase'] == 'compensate' and c['status'] == 'completed' for c in final['commands'])
        else:
            assert final['status'] == 'completed' and terminal_group['status'] == 'running'
            assert len(group_fills(restored, group)) == len(original_frozen['commands'])
            assert all(c['status'] == 'completed' for c in final['commands'])
            assert len({c['order_id'] for c in final['commands']}) == len(final['commands'])
        report = restored.book.contribution_report('example', await restored.snapshots_for('example', [OUTSIDE]))
        assert report['reconciled']
        observation.update(terminal_group_status=terminal_group['status'], terminal_batch_status=final['status'],
                           terminal_error=terminal_group['last_error'], late_funding=funding,
                           late_publication_requests=publication_calls,
                           original_identity_preserved_final=True, repeat_did_not_duplicate=True,
                           final_group_fills=len(group_fills(restored, group)),
                           final_command_states=[{'key': c['key'], 'phase': c['phase'], 'status': c['status'], 'order_id': c['order_id']} for c in final['commands']],
                           contributions_reconciled=report['reconciled'])
        return observation
    finally:
        if restored is not None:
            await restored.stop()
        await generator.aclose()


async def main(args):
    result = {'expected_implementation': args.expect, 'source_label': args.source_label,
              'scope': 'Synthetic inputs, real book and real runtime; no external requests; isolated temporary databases',
              'source_sha256': {name: hashlib.sha256((Path(tidebench.__file__).parent/name).read_bytes()).hexdigest()
                                for name in ('managed_portfolios.py','pro_service.py','pro_execution.py','pending_funding.py','account_capital.py')},
              'cases': []}
    with TemporaryDirectory(prefix='tidebench-independent-funding-wait-') as tmp:
        cases = [(2, 'resume')] if args.expect == 'before' else [(1, 'resume'), (2, 'resume'), (2, 'stop'), (2, 'execution_error')]
        for index, (inject_at, disposition) in enumerate(cases):
            root = Path(tmp)/str(index)
            root.mkdir()
            result['cases'].append(await scenario(root, inject_at, disposition, args.expect))
    result['passed'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'expected_implementation': args.expect,
                      'cases': [{'inject_before_add_number': c['inject_before_add_number'], 'disposition': c['disposition'],
                                 'after_due': c['group_status_after_due'], 'terminal': c.get('terminal_batch_status',c['batch_status_after_due'])} for c in result['cases']]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect', choices=('before','fixed'), default='fixed')
    parser.add_argument('--source-label', default='working-tree-after-funding-wait-fix')
    parser.add_argument('--output', type=Path, default=Path('/tmp/tidebench-v10-funding-wait-after.json'))
    asyncio.run(main(parser.parse_args()))
