"""Real public OKX schedule admission and actual isolated local-paper receipts."""
import asyncio, hashlib, json, time
from pathlib import Path
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.pro_service import ProfessionalRuntime
from tidebench.provenance import research_identity
from tidebench.store import Store, encode, now_ms
from tidebench.strategy_registry import digest

ROOT=Path('/tmp/tidebench-v11-public-perpetual')
SYMBOL='BTC-USDT-SWAP'
async def main():
    ROOT.mkdir(mode=0o700)
    cfg=Settings(data_dir=ROOT,worker_enabled=False,_env_file=None)
    r=ProfessionalRuntime(Store(cfg.database),MarketService(),cfg)
    start=time.monotonic()
    report={'scope':'Real public REST observations and isolated local-paper orders; no credentials or venue orders; no funding-boundary or operating-duration claim.', 'implementation':research_identity(),'started_at':now_ms(),'passed':False}
    try:
        q=await r.catalog.get_market_snapshot(SYMBOL,'okx')
        report['captured_snapshot']=encode(q)
        report['snapshot_hash']=digest(encode(q))
        report['schedule_admission']=r.book.funding_schedule(q)
        command={'source':'okx','inst_id':SYMBOL,'side':'buy','quantity':'1','leverage':2,'reduce_only':False,'order_type':'market','margin_mode':'isolated'}
        report['entry']=await r.submit(command,'public-perp-entry-0001','isolated-auditor')
        report['close']=await r.submit(command|{'side':'sell','reduce_only':True},'public-perp-close-0001','isolated-auditor')
        quotes=await r.snapshots_for('okx')
        account=r.book.observe('okx',quotes)
        report['account']=account
        report['contribution_reconciliation']=r.book.contributions.report('okx',account)
        report['passed']=report['entry']['status']=='filled' and report['close']['status']=='filled' and not account['positions'] and report['contribution_reconciliation']['reconciled']
        assert report['passed'], 'Public perpetual local-paper path incomplete'
    except Exception as exc:
        report['error']=str(exc)
        raise
    finally:
        report['ended_at']=now_ms();report['elapsed_seconds']=round(time.monotonic()-start,3)
        report['implementation_at_end']=research_identity()
        report['implementation_unchanged']=report['implementation']==report['implementation_at_end']
        report['driver_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report['passed']=report['passed'] and report['implementation_unchanged']
        await r.stop();await r.market.close()
        (ROOT/'summary.json').write_text(json.dumps(encode(report),indent=2,allow_nan=False)+'\n')
asyncio.run(main())
