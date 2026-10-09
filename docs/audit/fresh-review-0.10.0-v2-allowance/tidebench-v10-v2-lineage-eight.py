"""Independent v2 allowance/restoration and lineage tampering; isolated SQLite only."""
import argparse
import asyncio
import copy
import hashlib
import json
import shutil
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory

import tidebench
from tidebench.platform import PlatformError
from tidebench.store import dumps, encode
from tidebench.strategy_registry import digest
from test_managed_portfolios import activate, runtime
from test_pro_execution import order

CONTRACT='reduce_group_v2_allowance'
OUTSIDE='SOL-USDT-SWAP'


def identity(command):
    return {k:command[k] for k in ['id','phase','sequence','deployment_id','key','payload','payload_hash']}


def original_identity(batch, ids):
    return {'id':batch['id'],'content_hash':batch['content_hash'],'body':batch['body'],
            'additions':batch['additions'],'additions_hash':batch['additions_hash'],
            'commands':[identity(c) for c in batch['commands'] if c['id'] in ids]}


async def frozen_pending(root):
    fixture=runtime.__wrapped__(root)
    r=await anext(fixture)
    original_submit=r.book.submit
    captured={}
    try:
        r.book.now=r.clock.now
        r.book.set_risk('example',{'fee_bps':'0','slippage_bps':'0','liquidation_fee_bps':'0'},'audit')
        group=await activate(r,capital_pct=50,execution_contract=CONTRACT,
            research_policy={'fee_bps':'0','slippage_bps':'0','liquidation_fee_bps':'0'})
        outside=await r.quote_snapshot('example',OUTSIDE)
        due=r.clock.now()+1
        outside=dict(outside,funding_time=due,next_funding_time=due+3600000)
        r.book.submit(order(OUTSIDE,quantity='10',leverage=2),'audit-outside-inventory',{OUTSIDE:outside},'audit-outside')

        def inject_then_interrupt(payload,key,quotes,actor,**kwargs):
            if actor.startswith('strategy:') and not payload['reduce_only']:
                with r.store.read() as conn:
                    events=conn.execute("SELECT details FROM audit WHERE kind='portfolio.allowance_superseded' ORDER BY id").fetchall()
                for row in events:
                    event=json.loads(row['details'])
                    if event['group_id']==group['id'] and key in {c['key'] for c in event['plan']['replacement_commands']}:
                        captured['interrupted_before_replacement']=key
                        # Deliberate shutdown boundary, not a fabricated admission error.
                        raise asyncio.CancelledError()
            result=original_submit(payload,key,quotes,actor,**kwargs)
            if actor.startswith('strategy:') and not payload['reduce_only'] and 'first_order' not in captured:
                before=r.managed_portfolios.history(group['id'])[0]
                ids={c['id'] for c in before['commands']}
                captured['original_command_ids']=list(ids)
                captured['original_identity']=original_identity(before,ids)
                captured['first_order']=result
                r.clock.change(step_ms=2,expected_revision=r.clock.status()['revision'],actor='audit')
                r.book.capture_funding_obligations('example',OUTSIDE,dict(outside,ts=r.clock.now(),mark_ts=r.clock.now()))
                event={'ts':due,'rate':'.01','mark_price':outside['mark'],'mark_ts':due,
                       'mark_price_source':'independent_synthetic_realized_publication'}
                captured['real_funding']=r.book.settle_funding('example',OUTSIDE,[event],quotes|{OUTSIDE:outside})
            return result

        r.book.submit=inject_then_interrupt
        try:
            await r.managed_portfolios.evaluate(group['id'])
        except asyncio.CancelledError:
            captured['intentional_cancellation_observed']=True
        finally:
            r.book.submit=original_submit
        batch=r.managed_portfolios.history(group['id'])[0]
        assert captured.get('intentional_cancellation_observed')
        assert batch['status']=='adding' and len(batch['allowance_replans'])==1
        assert any(c['status']=='completed' and c['phase']=='add' for c in batch['commands'])
        assert any(c['status']=='superseded' and c['phase']=='add' for c in batch['commands'])
        assert any(c['status']=='pending' and c['phase']=='add' for c in batch['commands'])
        assert original_identity(batch,set(captured['original_command_ids']))==captured['original_identity']
        assert len(captured['real_funding'])==1
        captured.update(group_id=group['id'],batch_id=batch['id'],batch=batch,
                        orders=r.book.orders('example'),funding_pending=r.book.deferred_funding.pending('example'))
        assert not captured['funding_pending']
        return captured
    finally:
        await fixture.aclose()


def tamper(r,state,kind):
    with r.store.write() as conn:
        row=conn.execute("SELECT id,details FROM audit WHERE kind='portfolio.allowance_superseded' AND json_extract(details,'$.batch_id')=?",(state['batch_id'],)).fetchone()
        event=json.loads(row['details'])
        plan=event['plan']
        if kind=='delete_plan':
            conn.execute('DELETE FROM audit WHERE id=?',(row['id'],));return
        if kind=='delete_replacement_command':
            conn.execute('DELETE FROM portfolio_commands WHERE id=?',(plan['replacement_commands'][0]['id'],));return
        if kind=='wrong_group':plan['group_id']='wrong-group'
        elif kind=='wrong_batch':plan['batch_id']='wrong-batch'
        elif kind=='replacement_key':plan['replacement_commands'][0]['key']='unbound-key'
        elif kind=='superseded_hash':plan['superseded_commands'][0]['payload_hash']='0'*64
        elif kind=='replacement_payload':
            identifier=plan['replacement_commands'][0]['id']
            command=conn.execute('SELECT payload FROM portfolio_commands WHERE id=?',(identifier,)).fetchone()
            payload=json.loads(command['payload']);payload['quantity']=str(D(payload['quantity'])*2)
            conn.execute('UPDATE portfolio_commands SET payload=?,payload_hash=? WHERE id=?',(dumps(payload),digest(payload),identifier));return
        else:raise AssertionError(kind)
        # Re-hash edited content to test semantic binding, not merely bad SHA.
        event['content_hash']=digest(plan)
        conn.execute('UPDATE audit SET details=? WHERE id=?',(dumps(event),row['id']))


async def restore_case(root,state,kind):
    fixture=runtime.__wrapped__(root);r=await anext(fixture)
    try:
        r.book.now=r.clock.now
        before_orders=r.book.orders('example')
        if kind=='resume':
            await r.managed_portfolios.evaluate(state['group_id'])
            batch=r.managed_portfolios.history(state['group_id'])[0]
            assert batch['status']=='completed'
            assert len(batch['allowance_replans'])==1
            assert original_identity(batch,set(state['original_command_ids']))==state['original_identity']
            assert all(c['status'] in {'completed','superseded'} for c in batch['commands'])
            retired={c['key'] for c in batch['commands'] if c['phase']=='add' and c['status']=='superseded'}
            with r.store.read() as conn:
                filled_keys={row[0] for row in conn.execute("SELECT key FROM pro_orders WHERE source='example' AND status='filled'")}
            assert not retired & filled_keys
            assert sum(o['id']==state['first_order']['id'] for o in r.book.orders('example'))==1
            orders=r.book.orders('example')
            await r.managed_portfolios.evaluate(state['group_id'])
            assert r.book.orders('example')==orders
            contributions=r.book.contribution_report('example',await r.snapshots_for('example'))
            assert contributions['reconciled']
            return {'case':kind,'passed':True,'group_status':r.managed_portfolios.get(state['group_id'])['status'],
                    'batch_status':batch['status'],'original_identity_preserved':True,'superseded_keys_never_filled':True,
                    'first_fill_exactly_once':True,'repeat_no_duplicate':True,'reconciled':True,
                    'batch':batch,'new_order_count':len(orders)-len(before_orders)}
        tamper(r,state,kind)
        try:
            r.managed_portfolios.batch(state['batch_id'])
        except PlatformError as exc:
            assert exc.code=='portfolio_evidence_integrity',exc.code
            integrity={'code':exc.code,'message':exc.message}
        else:raise AssertionError('Tampered lineage accepted: '+kind)
        try:
            await r.managed_portfolios.evaluate(state['group_id'])
        except PlatformError as exc:
            assert exc.code=='portfolio_evidence_integrity',exc.code
        assert r.book.orders('example')==before_orders
        stopped=r.managed_portfolios.stop(state['group_id'],'audit-operator')
        assert stopped['status']=='stopped'
        markets={leg['inst_id'] for leg in stopped['manifest']['legs']}
        quotes=await r.snapshots_for('example')
        protected=0
        for position in r.book.positions('example'):
            if position['inst_id'] in markets:
                r.book.submit(order(position['inst_id'],side='sell',quantity=position['quantity'],reduce_only=True),
                              'audit-protect-'+kind+'-'+position['inst_id'],quotes,'audit-operator')
                protected+=1
        assert protected>0 and not any(p['inst_id'] in markets for p in r.book.positions('example'))
        return {'case':kind,'passed':True,'integrity_error':integrity,'no_new_risk_fill':True,
                'stop_available':True,'protective_close_fills':protected,'portfolio_inventory_flat':True}
    finally:
        await fixture.aclose()


async def main(args):
    result={'scope':'Synthetic publication, actual book/runtime, isolated temporary DB clones; no external HTTP or venue orders',
            'contract':CONTRACT,'source_label':args.source_label,
            'source_sha256':{n:hashlib.sha256((Path(tidebench.__file__).parent/n).read_bytes()).hexdigest()
                for n in ['managed_portfolios.py','portfolio_execution.py','portfolio_targets.py','portfolio_registry.py','portfolio_research.py','account_capital.py','pro_execution.py']},'cases':[]}
    with TemporaryDirectory(prefix='tidebench-independent-v2-lineage-') as tmp:
        base=Path(tmp)/'base';base.mkdir()
        state=await frozen_pending(base)
        result['frozen_pending_state']=state
        for kind in ['resume','wrong_group','wrong_batch','replacement_key','superseded_hash','replacement_payload','delete_plan','delete_replacement_command']:
            clone=Path(tmp)/kind;shutil.copytree(base,clone)
            result['cases'].append(await restore_case(clone,state,kind))
    result['passed']=True
    args.output.write_text(json.dumps(encode(result),indent=2))
    print(json.dumps({'passed':True,'cases':[{'case':c['case'],'passed':c['passed']} for c in result['cases']]},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-label',default='working-tree-v2-allowance')
    parser.add_argument('--output',type=Path,default=Path('/tmp/tidebench-v10-v2-lineage-observations.json'))
    asyncio.run(main(parser.parse_args()))
