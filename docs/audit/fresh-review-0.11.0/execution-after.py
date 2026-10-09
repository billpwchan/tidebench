import asyncio,json,tempfile
from pathlib import Path
import httpx
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.pro_service import ProfessionalRuntime
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store, now_ms, dumps
from tidebench.platform import PlatformError
from tidebench.provenance import research_identity
import hashlib
from test_catalog import raw_instrument
from test_pro_execution import snapshot,order

SYMBOL='BTC-USDT-SWAP'

def refusal(req):
    raise AssertionError('Unexpected external request: '+str(req.url))

async def stop_after_partial(root):
    client=httpx.AsyncClient(transport=httpx.MockTransport(refusal))
    cfg=Settings(data_dir=root,_env_file=None)
    rt=ProfessionalRuntime(Store(cfg.database),MarketService(client=client),cfg)
    rt.book.set_risk('example',{'fee_bps':'0','slippage_bps':'0','liquidation_fee_bps':'0'},'audit')
    q=snapshot(ts=1000000)
    rt.book.submit(order(),'entry-001',{SYMBOL:q})
    stop=rt.book.submit(order(side='sell',quantity='100',reduce_only=True,order_type='stop_market',stop_price='95'),'stop-0001',{SYMBOL:q})
    rt.book.submit(order(side='sell',quantity='50',reduce_only=True),'reduce-50',{SYMBOL:q})
    hit=snapshot(price='94',ts=1000001)
    await rt.poll_market('example',SYMBOL,{SYMBOL:hit})
    receipt = next(o for o in rt.book.orders('example') if o['id'] == stop['id'])
    with rt.store.read() as conn:
        raw = dict(conn.execute("SELECT payload FROM pro_orders WHERE id=?",(stop['id'],)).fetchone())
    first={'remaining_position_count':len(rt.book.positions('example')),
        'stop_status':receipt['status'], 'requested_quantity':receipt['requested_quantity'],
        'filled_quantity':receipt['filled_quantity'], 'canceled_quantity':receipt['canceled_quantity'],
        'error':rt.market_errors.get(('example',SYMBOL)), 'original_command_unchanged':raw['payload']==dumps(order(side='sell',quantity='100',reduce_only=True,order_type='stop_market',stop_price='95'))}
    await rt.stop()
    rt2=ProfessionalRuntime(Store(cfg.database),MarketService(client=client),cfg)
    await rt2.poll_market('example',SYMBOL,{SYMBOL:hit})
    first['after_restart_position_count']=len(rt2.book.positions('example'))
    first['after_restart_pending_count']=len(rt2.book.pending())
    first['after_restart_error']=rt2.market_errors.get(('example',SYMBOL))
    first['receipt_unchanged_after_restart']=next(o for o in rt2.book.orders('example') if o['id']==stop['id'])==receipt
    assert first['original_command_unchanged'] and first['receipt_unchanged_after_restart']
    assert receipt['status']=='filled' and receipt['filled_quantity']=='50' and receipt['canceled_quantity']=='50'
    assert not rt2.book.positions('example') and not rt2.book.pending()
    await rt2.stop()
    await client.aclose()
    return first

async def stale_funding(root):
    observed=now_ms()
    old=observed-2*86400000
    seen=[]
    def handle(req):
        seen.append(req.url.path)
        p=req.url.path
        if p.endswith('/instruments'): rows=[raw_instrument(swap=True)]
        elif p.endswith('/mark-price'): rows=[{'instId':SYMBOL,'markPx':'100','ts':str(now_ms())}]
        elif p.endswith('/index-tickers'): rows=[{'instId':'BTC-USDT','idxPx':'100','ts':str(old)}]
        elif p.endswith('/funding-rate'): rows=[{'instId':SYMBOL,'fundingRate':'.01','fundingTime':str(old+3600000),'nextFundingTime':str(old+7200000),'ts':str(old)}]
        elif p.endswith('/ticker'): rows=[{'instId':SYMBOL,'last':'100','bidPx':'100','askPx':'100','ts':str(now_ms())}]
        elif p.endswith('/position-tiers'): rows=[{'tier':'1','minSz':'0','maxSz':'100000','mmr':'.004','imr':'.01','maxLever':'100'}]
        elif p.endswith('/funding-rate-history'): rows=[]
        else: raise AssertionError('Unexpected endpoint '+p)
        return httpx.Response(200,json={'code':'0','msg':'','data':rows})
    client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    cfg=Settings(data_dir=root,_env_file=None)
    rt=ProfessionalRuntime(Store(cfg.database),MarketService(client=client),cfg)
    rt.book.set_risk('okx',{'fee_bps':'0','slippage_bps':'0','liquidation_fee_bps':'0'},'audit')
    cmd=order(source='okx')
    blocked=[]
    for key in ('stale-funding-01','stale-funding-02'):
        try:
            await rt.submit(cmd,key,'audit')
        except PlatformError as exc:
            assert exc.code=='funding_schedule_unavailable'
            blocked.append(exc.code)
        else:
            raise AssertionError('Stale funding schedule admitted risk')
    current=await rt.catalog.get_market_snapshot(SYMBOL,'okx')
    assert not rt.book.positions('okx') and not rt.book.orders('okx')
    good=current | {'funding_ts':now_ms(), 'funding_time':now_ms()+3600000, 'next_funding_time':now_ms()+7200000}
    rt.book.submit(cmd,'fresh-funding-01',{SYMBOL:good})
    account=rt.book.account('okx',{SYMBOL:current})
    close=rt.book.submit(order(source='okx',side='sell',reduce_only=True),'stale-protect-01',{SYMBOL:current})
    result={'rejected_orders':blocked,'funding_age_ms':now_ms()-current['funding_ts'],
        'index_age_ms':now_ms()-current['index_ts'], 'valuation_status':account['valuation_status'],
        'economic_status':account['economic_status'],'pending_funding':account['pending_funding'],
        'equity':account['equity'], 'unavailable_funding_schedules':account['unavailable_funding_schedules'],
        'reduce_only_exit_status':close['status'], 'final_position_count':len(rt.book.positions('okx')),
        'endpoints':seen}
    assert account['economic_status']=='funding_schedule_unavailable' and account['equity'] is None
    assert close['status']=='filled' and not rt.book.positions('okx')
    await rt.stop()
    await client.aclose()
    return result

async def main():
    with tempfile.TemporaryDirectory(prefix='tidebench-v11-readonly-',dir='/tmp') as path:
        root=Path(path)
        result={'implementation':research_identity(),'scope':'Temporary SQLite, synthetic MockTransport data fixtures; no private data, credentials or venue orders','stop_after_partial':await stop_after_partial(root/'stop'),'stale_funding':await stale_funding(root/'funding')}
        Path('/tmp/tidebench-v11-data-execution-after-observations.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
asyncio.run(main())
