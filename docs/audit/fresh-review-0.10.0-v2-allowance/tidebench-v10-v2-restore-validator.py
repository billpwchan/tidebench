"""Actual backup lineage validation, isolated financial-state SQLite clones."""
import asyncio,hashlib,importlib.util,json,shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import tidebench
from tidebench.platform import BackupService,PlatformError
from tidebench.store import dumps
from tidebench.strategy_registry import digest
from test_managed_portfolios import runtime

spec=importlib.util.spec_from_file_location('lineage_probe',Path(__file__).with_name('tidebench-v10-v2-lineage-eight.py'))
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

async def main():
    result={'scope':'Actual BackupService lineage validator on cloned financial SQLite; not a full HTTP ZIP import test',
        'source_sha256':{n:hashlib.sha256((Path(tidebench.__file__).parent/n).read_bytes()).hexdigest()
                           for n in ['platform.py','portfolio_execution.py','managed_portfolios.py']},'cases':[]}
    with TemporaryDirectory(prefix='tidebench-independent-v2-backup-') as tmp:
        base=Path(tmp)/'base';base.mkdir();state=await helper.frozen_pending(base)
        for kind in ['clean','wrong_group','wrong_batch','replacement_key','superseded_hash','replacement_payload','delete_plan','delete_replacement_command','standalone_graft']:
            path=Path(tmp)/kind;shutil.copytree(base,path)
            fixture=runtime.__wrapped__(path);r=await anext(fixture)
            try:
                if kind=='standalone_graft':
                    with r.store.write() as conn:
                        row=conn.execute("SELECT id,details FROM audit WHERE kind='portfolio.allowance_superseded'").fetchone()
                        event=json.loads(row['details']);event['batch_id']='nonexistent-financial-batch'
                        event['plan']['batch_id']=event['batch_id'];event['content_hash']=digest(event['plan'])
                        conn.execute('UPDATE audit SET details=? WHERE id=?',(dumps(event),row['id']))
                elif kind!='clean':helper.tamper(r,state,kind)
                try:
                    with r.store.read() as conn:BackupService._validate_portfolio_allowance(conn)
                except PlatformError as exc:
                    assert kind!='clean' and exc.code=='backup_integrity',(kind,exc.code)
                    result['cases'].append({'case':kind,'accepted':False,'error_code':exc.code,'passed':True})
                else:
                    assert kind=='clean',kind
                    result['cases'].append({'case':kind,'accepted':True,'passed':True})
            finally:await fixture.aclose()
    result['passed']=True
    Path('/tmp/tidebench-v10-v2-restore-validator-observations.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':asyncio.run(main())
