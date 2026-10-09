"""Independent cash-sleeve admission trace; isolated databases and no external HTTP."""
import argparse
import asyncio
import hashlib
import json
from decimal import Decimal as D, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory

import tidebench
from tidebench.account_capital import unit
from tidebench.contributions import ContributionBook
from tidebench.engine import ACCOUNTING_CONTEXT
from tidebench.platform import PlatformError
from tidebench.store import encode
from test_managed_portfolios import activate, runtime, HOUR


async def probe(root, cash_weight, max_steps):
    fixture = runtime.__wrapped__(root)
    r = await anext(fixture)
    trace = []
    step = 0
    original = r.account_capital.admit_order if hasattr(r, 'account_capital') else r.managed_portfolios.capital.admit_order
    capital = r.managed_portfolios.capital

    def admission(conn, source, actor, account, metadata, signed_quantity, fill_price, **kwargs):
        with localcontext(ACCOUNTING_CONTEXT):
            owner = ContributionBook.owner(conn, actor)
            actual = capital._actual(conn, source, account)
            used = actual.get(owner, {'capital': D(0)})['capital']
            mark = D(str(kwargs.get('mark_price', fill_price)))
            fill = D(str(fill_price))
            quantity = D(str(signed_quantity))
            fee = D(str(kwargs.get('fee', 0)))
            projected = D(account['equity']) - fee + quantity*unit(metadata)*(mark-fill)
            added = abs(quantity)*unit(metadata)*(mark if metadata['inst_type']=='SPOT' else fill/D(str(kwargs.get('leverage',1))))
            promise = capital.get(source, owner, conn)
            item = encode({'step': step, 'clock': r.clock.now(), 'owner':owner,
                'inst_id':metadata['inst_id'], 'signed_quantity':quantity,
                'fill_price':fill,'mark_price':mark,'fee':fee,
                'pre_equity':account['equity'],'pre_owner_capital':used,
                'post_equity':projected,'candidate_capital':added,
                'post_owner_capital_pct':(used+added)/projected*100,
                'admitted_pct':promise['body']['capital_pct'] if promise else None,
                'account_positions':account['positions'],
                'account_funding_paid':account['funding_paid'],
                'account_fees_paid':account['fees_paid']})
            try:
                result = original(conn, source, actor, account, metadata, signed_quantity, fill_price, **kwargs)
            except PlatformError as e:
                item.update(outcome='rejected',code=e.code,message=e.message)
                trace.append(item)
                raise
            item['outcome']='admitted'
            trace.append(item)
            return result

    capital.admit_order = admission
    definitions = [
        ('BTC basis carry', {'mode':'funding_carry','capital_pct':'8','carry_threshold':'-.01','rebalance_bars':2,
          'legs':[{'inst_id':'BTC-USDT','weight':'.5'},{'inst_id':'BTC-USDT-SWAP','weight':'-.5','leverage':'2','direction':'short_only'}]}),
        ('ETH SOL cash basket', {'mode':'fixed_weights','capital_pct':'8','rebalance_bars':2,
          'legs':[{'inst_id':'ETH-USDT','weight':cash_weight},{'inst_id':'SOL-USDT','weight':cash_weight}]}),
        ('Independent swap exposure', {'mode':'fixed_weights','capital_pct':'8','rebalance_bars':2,
          'legs':[{'inst_id':'OKB-USDT-SWAP','weight':'.25','leverage':'2'},
                  {'inst_id':'DOGE-USDT-SWAP','weight':'-.25','leverage':'2','direction':'short_only'}]}),
    ]
    groups=[]
    samples=[]
    try:
        for name,definition in definitions:
            g=await activate(r,research_bars=96,**definition)
            groups.append({'name':name,'id':g['id'],'definition':definition})
            await r.managed_portfolios.evaluate(g['id'])
            assert r.managed_portfolios.get(g['id'])['status']=='running'
        for step in range(1,max_steps+1):
            r.clock.change(step_ms=4*HOUR,expected_revision=r.clock.status()['revision'],actor='audit')
            await r.execution_once()
            await asyncio.gather(*(r.managed_portfolios.evaluate(g['id']) for g in groups))
            quotes=await r.snapshots_for('example')
            account=r.book.account('example',quotes)
            current=[r.managed_portfolios.get(g['id']) for g in groups]
            samples.append({'step':step,'clock':r.clock.now(),'equity':account['equity'],
                'funding_paid':account['funding_paid'],'fees_paid':account['fees_paid'],
                'groups':[{'id':g['id'],'status':g['status'],'last_bar':g['last_bar'],'last_error':g['last_error']} for g in current]})
            if any(g['status']!='running' for g in current):
                break
        current=[r.managed_portfolios.get(g['id']) for g in groups]
        return {'cash_leg_weight':cash_weight,'steps_completed':step,'scope':'Accelerated synthetic clock; no elapsed soak claim',
                'groups':groups,'final_groups':current,'samples':samples,'admission_trace':trace,
                'latest_batches':{g['name']:r.managed_portfolios.history(g['id'])[:1] for g in groups},
                'final_contributions':r.book.contribution_report('example',await r.snapshots_for('example'))}
    finally:
        await fixture.aclose()


async def main(args):
    result={'source_revision':args.source_label,'source_sha256':{name:hashlib.sha256((Path(tidebench.__file__).parent/name).read_bytes()).hexdigest()
        for name in ['managed_portfolios.py','portfolio_targets.py','portfolio_execution.py','account_capital.py','pro_execution.py']},'cases':[]}
    with TemporaryDirectory(prefix='tidebench-independent-capital-boundary-') as tmp:
        for i,w in enumerate(args.weights):
            p=Path(tmp)/str(i);p.mkdir()
            result['cases'].append(await probe(p,w,args.steps))
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({'output':str(args.output),'cases':[{'cash_weight':c['cash_leg_weight'],'steps':c['steps_completed'],
         'groups':[{'name':g['name'],'status':next(x['status'] for x in c['final_groups'] if x['id']==g['id'])} for g in c['groups']],
         'rejections':[{k:t[k] for k in ['step','inst_id','code','pre_equity','post_equity','pre_owner_capital','candidate_capital','post_owner_capital_pct','admitted_pct']} for t in c['admission_trace'] if t['outcome']=='rejected']} for c in result['cases']]},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--weights',nargs='+',default=['.5','.4'])
    p.add_argument('--steps',type=int,default=60)
    p.add_argument('--source-label',default='4b0d466e82b486256ba71006605bdd223592ec36')
    p.add_argument('--output',type=Path,default=Path('/tmp/tidebench-v10-capital-boundary-observations.json'))
    asyncio.run(main(p.parse_args()))
