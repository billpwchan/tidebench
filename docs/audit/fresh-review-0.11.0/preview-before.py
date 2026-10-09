import json,tempfile,hashlib
from pathlib import Path
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store
from test_pro_execution import snapshot,order
cases=[('spot_sell', 'BTC-USDT','buy','1','50','110',1),('swap_long','BTC-USDT-SWAP','buy','50','100','110',10),('swap_short','BTC-USDT-SWAP','sell','50','100','90',10),('swap_debt','BTC-USDT-SWAP','buy','100','100','1',10)]
observations=[]
with tempfile.TemporaryDirectory(dir='/tmp',prefix='tidebench-preview-before-') as directory:
    for label,symbol,side,close_qty,entry,exit,leverage in cases:
        book=SimulationBook(Store(Path(directory)/(label+'.sqlite')))
        book.set_risk('example',{'fee_bps':'0','slippage_bps':'0','liquidation_fee_bps':'0'},'audit')
        q=snapshot(symbol,price=entry)
        initial=order(symbol,side=side,quantity='100' if symbol.endswith('SWAP') else '2',leverage=leverage)
        book.submit(initial,'entry-0001',{symbol:q})
        close=order(symbol,side='sell' if side=='buy' else 'buy',quantity=close_qty,leverage=leverage,reduce_only=True)
        q=snapshot(symbol,price=exit)
        preview=book.preview(close,{symbol:q})
        fill=book.submit(close,'close-0001',{symbol:q})
        observations.append({'case':label,'estimated_cash_after':preview['estimated_cash_after'],'cash_after':fill['cash_after'],'insurance_debt':fill['insurance_debt']})
result={'source_sha256':hashlib.sha256(Path('backend/tidebench/pro_execution.py').read_bytes()).hexdigest(),'observations':observations}
Path('/tmp/tidebench-v11-preview-before.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
