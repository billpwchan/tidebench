import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  Check,
  ChevronDown,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  Square,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Risk, Source } from '../api';
import { api } from '../api';
import {
  ActionNote,
  Empty,
  ErrorBox,
  Field,
  Loading,
  PageHeading,
  Status,
} from '../components/workspace';
import { date } from '../lib/format';

export default function Risk({ source }: { source: Source }) {
  const qc = useQueryClient();
  const risk = useQuery({ queryKey: ['risk', source], queryFn: () => api.risk(source) });
  const audit = useQuery({
    queryKey: ['audit', source],
    queryFn: () => api.audit(source),
    refetchInterval: 15000,
  });
  const [limits, setLimits] = useState({
    max_order_notional: '2500',
    max_position_pct: 50,
    max_daily_loss_pct: 5,
  });
  const [reason, setReason] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  useEffect(() => {
    if (risk.data) {
      const { max_order_notional, max_position_pct, max_daily_loss_pct } = risk.data;
      setLimits({ max_order_notional, max_position_pct, max_daily_loss_pct });
    }
  }, [risk.data]);
  const refresh = (r: Risk) => {
    qc.setQueryData(['risk', source], r);
    void qc.invalidateQueries({ queryKey: ['audit', source] });
  };
  const save = useMutation({
    mutationFn: api.saveRisk,
    onSuccess: (r) => {
      refresh(r);
      setNotice('Risk limits saved. These gates apply to manual and strategy orders.');
    },
  });
  const halt = useMutation({
    mutationFn: api.halt,
    onSuccess: (r) => {
      refresh(r);
      setReason('');
      setNotice(
        r.kill_switch
          ? 'All paper fills are halted. Existing positions remain unchanged.'
          : 'Paper execution resumed. Strategies evaluate only the latest confirmed bar.',
      );
      void qc.invalidateQueries({ queryKey: ['deployments', source] });
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="CONTROL COMES FIRST"
        title="Risk & activity"
        description="Set the boundaries. See every decision. Keep execution accountable."
      >
        <span className="subtle-tag">{source.toUpperCase()} PAPER ACCOUNT</span>
      </PageHeading>
      <ActionNote text={notice} />
      <div className="risk-layout">
        <section className="risk-limits">
          <div className="section-heading">
            <div>
              <h2>Execution limits</h2>
              <p className="section-description">
                Checked in the same transaction as each paper fill.
              </p>
            </div>
            <ShieldCheck size={21} className="muted-icon" />
          </div>
          {risk.isPending ? (
            <Loading />
          ) : risk.isError ? (
            <ErrorBox error={risk.error} onRetry={() => void risk.refetch()} />
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate({ source, ...limits });
              }}
            >
              <div className="limit-field">
                <div>
                  <strong>Maximum order notional</strong>
                  <p>The largest allowed increase in exposure per order.</p>
                </div>
                <div className="input-suffix">
                  <input
                    aria-label="Maximum order notional"
                    required
                    type="number"
                    min="1"
                    step="0.01"
                    value={limits.max_order_notional}
                    onChange={(e) => setLimits({ ...limits, max_order_notional: e.target.value })}
                  />
                  <span>USDT</span>
                </div>
              </div>
              <div className="limit-field">
                <div>
                  <strong>Maximum position allocation</strong>
                  <p>Limit a single asset's share of account equity.</p>
                </div>
                <div className="input-suffix">
                  <input
                    aria-label="Maximum position allocation"
                    required
                    type="number"
                    min="1"
                    max="100"
                    step="0.1"
                    value={limits.max_position_pct}
                    onChange={(e) =>
                      setLimits({ ...limits, max_position_pct: Number(e.target.value) })
                    }
                  />
                  <span>%</span>
                </div>
              </div>
              <div className="limit-field">
                <div>
                  <strong>Maximum daily loss</strong>
                  <p>Stop increasing risk after this daily equity decline.</p>
                </div>
                <div className="input-suffix">
                  <input
                    aria-label="Maximum daily loss"
                    required
                    type="number"
                    min="0.1"
                    max="50"
                    step="0.1"
                    value={limits.max_daily_loss_pct}
                    onChange={(e) =>
                      setLimits({ ...limits, max_daily_loss_pct: Number(e.target.value) })
                    }
                  />
                  <span>%</span>
                </div>
              </div>
              <div className="form-bottom-row">
                <p className="form-footnote">Ordinary limits allow sales that reduce exposure.</p>
                <button className="button button-dark" type="submit" disabled={save.isPending}>
                  {save.isPending ? <Loader2 size={14} className="spin" /> : <Check size={15} />}
                  Save limits
                </button>
              </div>
              {save.isError && <ErrorBox error={save.error} />}
            </form>
          )}
        </section>
        <section className={`kill-switch-panel ${risk.data?.kill_switch ? 'halted' : ''}`}>
          <span className="kill-icon">
            {risk.data?.kill_switch ? <ShieldOff size={26} /> : <ShieldCheck size={26} />}
          </span>
          <Status type={!risk.data ? 'neutral' : risk.data.kill_switch ? 'bad' : 'good'}>
            {!risk.data
              ? 'STATUS UNAVAILABLE'
              : risk.data.kill_switch
                ? 'EXECUTION HALTED'
                : 'EXECUTION ENABLED'}
          </Status>
          <h2>The kill switch.</h2>
          <p>
            A persistent halt for all paper fills and strategy evaluation. It never liquidates
            existing positions.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              halt.mutate({ source, active: !risk.data?.kill_switch, reason });
            }}
          >
            <Field label={risk.data?.kill_switch ? 'Reason to resume' : 'Reason for halt'}>
              <input
                required
                minLength={3}
                maxLength={300}
                placeholder={
                  risk.data?.kill_switch ? 'Checks complete…' : 'Describe why you are halting…'
                }
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </Field>
            <button
              className={`button full-width ${risk.data?.kill_switch ? 'button-citrus' : 'button-danger'}`}
              disabled={halt.isPending || !risk.data || reason.trim().length < 3}
              aria-label={risk.data?.kill_switch ? 'Resume paper desk' : 'Halt paper desk'}
            >
              {halt.isPending ? (
                <Loader2 size={15} className="spin" />
              ) : risk.data?.kill_switch ? (
                <Play size={15} />
              ) : (
                <Square size={14} />
              )}
              {risk.data?.kill_switch ? 'Resume paper execution' : 'Halt all paper execution'}
            </button>
            {halt.isError && <ErrorBox error={halt.error} />}
          </form>
        </section>
      </div>
      <section className="audit-section">
        <div className="section-heading">
          <div>
            <h2>Activity ledger</h2>
            <p className="section-description">
              A persistent record of orders, risk changes, and strategy actions.
            </p>
          </div>
          <button className="text-button" onClick={() => void audit.refetch()}>
            <RefreshCw size={13} />
            Refresh
          </button>
        </div>
        {audit.isPending ? (
          <Loading />
        ) : audit.isError ? (
          <ErrorBox error={audit.error} onRetry={() => void audit.refetch()} />
        ) : audit.data.items.length ? (
          <div className="audit-list">
            {audit.data.items.map((item) => (
              <div className="audit-item" key={item.id}>
                <button
                  className="audit-row"
                  onClick={() => setExpanded(expanded === item.id ? null : item.id)}
                  aria-expanded={expanded === item.id}
                >
                  <span
                    className={`audit-icon ${item.kind.includes('reject') || item.kind.includes('halt') ? 'is-warning' : ''}`}
                  >
                    <Activity size={15} />
                  </span>
                  <span className="audit-summary">
                    <strong>{item.summary}</strong>
                    <small>{item.kind.replaceAll('_', ' ')}</small>
                  </span>
                  <time>{date(item.ts)}</time>
                  <ChevronDown className={expanded === item.id ? 'rotated' : ''} size={15} />
                </button>
                {expanded === item.id && (
                  <pre className="audit-details">{JSON.stringify(item.details, null, 2)}</pre>
                )}
              </div>
            ))}
          </div>
        ) : (
          <Empty title="A quiet ledger" icon={Activity}>
            Paper orders, deployments, and risk changes will leave an inspectable record here.
          </Empty>
        )}
      </section>
    </>
  );
}
