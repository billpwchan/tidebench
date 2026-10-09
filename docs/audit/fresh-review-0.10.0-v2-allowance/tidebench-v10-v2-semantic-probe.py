"""Coherent lineage mutation counterexample; actual isolated financial state only."""
import argparse,asyncio,hashlib,importlib.util,json
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import tidebench
from tidebench.platform import PlatformError
from tidebench.store import dumps,encode
from tidebench.strategy_registry import digest
from test_managed_portfolios import runtime

spec=importlib.util.spec_from_file_location('lineage_probe',Path(__file__).with_name('tidebench-v10-v2-lineage-eight.py'))
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

async def main(args):
    result={'source_sha256':{n:hashlib.sha256((Path(tidebench.__file__).parent/n).read_bytes()).hexdigest()
        for n in ['managed_portfolios.py','portfolio_execution.py','account_capital.py','pro_execution.py']},
        'scope':'Coherent replacement mutation, isolated actual SQLite; no real exchange requests or orders'}
    with TemporaryDirectory(prefix='tidebench-independent-v2-semantic-') as tmp:
        root=Path(tmp);state=await helper.frozen_pending(root)
        fixture=runtime.__wrapped__(root);r=await anext(fixture)
        try:
            r.book.now=r.clock.now
            before=r.book.orders('example')
            with r.store.write() as conn:
                row=conn.execute("SELECT id,details FROM audit WHERE kind='portfolio.allowance_superseded' AND json_extract(details,'$.batch_id')=?",(state['batch_id'],)).fetchone()
                event=json.loads(row['details']);plan=event['plan'];ref=plan['replacement_commands'][0]
                command=conn.execute('SELECT payload FROM portfolio_commands WHERE id=?',(ref['id'],)).fetchone()
                payload=json.loads(command['payload']);symbol=payload['inst_id'];old=D(payload['quantity']);new=abs(D(plan['remaining_original_quantities'][symbol]))*2
                payload['quantity']=str(new)
                signed_delta=(new-old)*(1 if payload['side']=='buy' else -1)
                plan['quantities'][symbol]=str(D(plan['quantities'][symbol])+signed_delta)
                ref['payload_hash']=digest(payload);event['content_hash']=digest(plan)
                conn.execute('UPDATE portfolio_commands SET payload=?,payload_hash=? WHERE id=?',(dumps(payload),digest(payload),ref['id']))
                conn.execute('UPDATE audit SET details=? WHERE id=?',(dumps(event),row['id']))
                result['mutation']={'batch_id':state['batch_id'],'group_id':state['group_id'],'replacement_command_id':ref['id'],
                    'old_replacement_quantity':str(old),'new_replacement_quantity':str(new),'preserved_original_remaining_quantity':plan['remaining_original_quantities'][symbol],
                    'row_payload_reference_and_plan_hashes_consistent':True,'tampered_plan':plan}
            try:
                r.managed_portfolios.batch(state['batch_id'])
            except PlatformError as exc:
                result['lineage_accepted']=False;result['error']={'code':exc.code,'message':exc.message}
                assert exc.code=='portfolio_evidence_integrity'
            else:result['lineage_accepted']=True
            if args.expect=='gap':
                assert result['lineage_accepted']
                await r.managed_portfolios.evaluate(state['group_id'])
                terminal=r.managed_portfolios.history(state['group_id'])[0]
                result['actual_terminal']={'group_status':r.managed_portfolios.get(state['group_id'])['status'],'batch_status':terminal['status'],
                    'error':terminal['error'],'new_order_count':len(r.book.orders('example'))-len(before)}
                assert terminal['status']=='compensated'
            else:
                assert not result['lineage_accepted']
                try:await r.managed_portfolios.evaluate(state['group_id'])
                except PlatformError as exc:assert exc.code=='portfolio_evidence_integrity'
                assert r.book.orders('example')==before
                result['no_new_orders']=True
                assert r.managed_portfolios.stop(state['group_id'],'audit')['status']=='stopped'
                result['stop_available']=True
            result['expected']=args.expect;result['passed']=True
        finally:await fixture.aclose()
    args.output.write_text(json.dumps(encode(result),indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ['mutation','source_sha256']},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--expect',choices=['gap','fixed'],default='gap')
    p.add_argument('--output',type=Path,default=Path('/tmp/tidebench-v10-v2-semantic-before.json'))
    asyncio.run(main(p.parse_args()))
