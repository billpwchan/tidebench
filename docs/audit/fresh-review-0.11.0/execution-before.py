import asyncio,json,tempfile
from pathlib import Path
import httpx
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.pro_service import ProfessionalRuntime
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store, now_ms
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
    first={'position':rt.book.positions('example')[0]['quantity'],'stop_status':rt.book.existing('example','stop-0001',json.dumps(order(side='sell',quantity='100',reduce_only=True,order_type='stop_market',stop_price='95'),sort_keys=True,separators=(',',':'))) if False else [json.loads(x['body'])['status'] for x in rt.book.pending() if x['id']==stop['id']][0],'error':rt.market_errors.get(('example',SYMBOL))}
    await rt.stop()
    rt2=ProfessionalRuntime(Store(cfg.database),MarketService(client=client),cfg)
    await rt2.poll_market('example',SYMBOL,{SYMBOL:hit})
    first['after_restart_position']=rt2.book.positions('example')[0]['quantity']
    first['after_restart_pending_count']=len(rt2.book.pending())
    first['after_restart_error']=rt2.market_errors.get(('example',SYMBOL))
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
    first=await rt.submit(cmd,'stale-funding-01','audit')
    second=await rt.submit(cmd,'stale-funding-02','audit')
    current=await rt.catalog.get_market_snapshot(SYMBOL,'okx')
    account=rt.book.account('okx',{SYMBOL:current})
    await rt.catalog.poll_once(SYMBOL,'okx')
    feed=rt.catalog.health('okx')['items'][0]
    result={'first_order':first['status'],'second_order':second['status'],'funding_age_ms':now_ms()-current['funding_ts'],'index_age_ms':now_ms()-current['index_ts'],'valuation_status':account['valuation_status'],'economic_status':account['economic_status'],'pending_funding':account['pending_funding'],'equity':account['equity'],'position_meta':json.loads(rt.book.positions('okx')[0]['metadata']),'watch_state':feed['state'],'watch_stale_fields':feed['stale_fields'],'endpoints':seen}
    await rt.stop()
    await client.aclose()
    return result

async def main():
    with tempfile.TemporaryDirectory(prefix='tidebench-v11-readonly-',dir='/tmp') as path:
        root=Path(path)
        result={'source':'8ef6b98','stop_after_partial':await stop_after_partial(root/'stop'),'stale_funding':await stale_funding(root/'funding')}
        Path('/tmp/tidebench-v11-data-execution-observations.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
asyncio.run(main())
