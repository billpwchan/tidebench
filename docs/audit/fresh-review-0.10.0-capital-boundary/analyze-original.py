"""Verify arithmetic in the recorded independent original-source probe."""
import argparse
import json
from decimal import Decimal as D, localcontext
from pathlib import Path


def analyze(path):
    raw=json.loads(path.read_text())
    full,reserve=raw['cases']
    assert full['cash_leg_weight']=='.5' and reserve['cash_leg_weight']=='.4'
    failure=next(x for x in full['admission_trace'] if x['outcome']=='rejected')
    assert failure['code']=='portfolio_capital_limit'
    batch=full['latest_batches']['ETH SOL cash basket'][0]
    budget=batch['additions']['capital_budget']
    quote=batch['additions']['quotes'][failure['inst_id']]
    assert quote['ts']==failure['clock'] and D(quote['mark'])==D(failure['mark_price'])
    index=full['admission_trace'].index(failure)
    prior=full['admission_trace'][index-2:index]
    assert {x['inst_id'] for x in prior}=={'OKB-USDT-SWAP','BTC-USDT'}
    assert all(x['owner']!=failure['owner'] and x['step']==failure['step'] and x['outcome']=='admitted' for x in prior)
    assert D(prior[0]['pre_equity'])==D(budget['account_equity'])
    assert D(prior[0]['post_equity'])==D(prior[1]['pre_equity'])
    assert D(prior[1]['post_equity'])==D(failure['pre_equity'])
    assert len({x['account_funding_paid'] for x in prior+[failure]})==1
    assert batch['status']=='compensated'
    assert full['final_contributions']['reconciled']
    assert reserve['steps_completed']==60 and all(g['status']=='running' for g in reserve['final_groups'])
    assert not any(x['outcome']=='rejected' for x in reserve['admission_trace'])
    assert reserve['final_contributions']['reconciled']
    with localcontext() as ctx:
        ctx.prec=50
        P=D(failure['admitted_pct'])/100
        frozenE,preE,postE=map(D,[budget['account_equity'],failure['pre_equity'],failure['post_equity']])
        C,Q,M,F,fee=map(D,[failure['pre_owner_capital'],failure['signed_quantity'],failure['mark_price'],failure['fill_price'],failure['fee']])
        counterfactualE=frozenE-fee+Q*(M-F)
        Cafter=C+D(failure['candidate_capital'])
        own_cash=Q*F+fee
        assert own_cash<=D(budget['budget_cash'])
        assert Cafter/counterfactualE*100<=D(failure['admitted_pct'])
        assert Cafter>postE*P
        losses=[D(x['pre_equity'])-D(x['post_equity']) for x in prior]
        assert sum(losses,D(0))==frozenE-preE
        residual=sum(abs(D(q)-D(next(p['quantity'] for p in failure['account_positions'] if p['inst_id']==s)))*D(batch['additions']['quotes'][s]['last']) for s,q in batch['body']['targets'].items())
        return {'passed':True,'source_revision':raw['source_revision'],
            'classification':'Correct hard admission following measured shared-equity changes under reduce_group_v1; not a missing own-fee planner defect',
            'failure_step':failure['step'],'quote_and_clock_unchanged':True,'funding_unchanged_between_freeze_and_rejection':True,
            'freeze_equity':str(frozenE),'pre_admission_equity':str(preE),'post_fill_equity':str(postE),
            'other_fill_equity_losses':[{'inst_id':x['inst_id'],'fee':x['fee'],'equity_loss_including_fill_to_mark':str(loss)} for x,loss in zip(prior,losses)],
            'shared_equity_decline':str(frozenE-preE),'frozen_budget_cash':budget['budget_cash'],
            'candidate_required_cash_including_fee':str(own_cash),'candidate_quantity':str(Q),
            'pre_owned_marked_capital':str(C),'projected_owned_marked_capital':str(Cafter),
            'post_fill_capital_ceiling':str(postE*P),'actual_guard_overage':str(Cafter-postE*P),
            'actual_guard_projected_owner_pct':failure['post_owner_capital_pct'],
            'counterfactual_if_no_other_fills':{'basis':'Algebraic evaluation only; not an executed fill or altered account',
                'post_fill_equity':str(counterfactualE),'projected_owner_pct':str(Cafter/counterfactualE*100),'within_eight_pct':True},
            'unfilled_target_residual_notional':str(residual),
            'unfilled_target_residual_pct_of_frozen_sleeve':str(residual/D(batch['body']['capital'])*100),
            'predeclared_reserve_case':{'cash_leg_weights':['.4','.4'],'sleeve_cash_reserve_pct':'20','steps':60,
                'synthetic_hours':240,'groups_running':3,'admission_rejections':0,'contributions_reconciled':True,
                'scope':'Separate predeclared scenario, same exact source; not a new elapsed soak or replacement of the adverse original'}}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('observations',type=Path)
    parser.add_argument('--output',type=Path,default=Path('/tmp/tidebench-v10-capital-boundary-assessment.json'))
    args=parser.parse_args()
    result=analyze(args.observations)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
