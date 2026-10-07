import {
  Activity,
  ArrowRight,
  BarChart3,
  Check,
  CircleAlert,
  Database,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { Children, cloneElement, isValidElement, useId } from 'react';
import type { ReactNode } from 'react';
import type { Bar, Source, Strategy } from '../api';
import { ApiError } from '../api';
import { bars, symbols } from '../lib/config';
import { barLabel } from '../lib/format';
import { useI18n } from '../lib/i18n';

export function Logo() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
export function Status({
  children,
  type = 'neutral',
}: {
  children: ReactNode;
  type?: 'neutral' | 'good' | 'warning' | 'bad';
}) {
  const { t } = useI18n();
  return (
    <span className={`status status-${type}`}>
      <span />
      {typeof children === 'string' ? t(children) : children}
    </span>
  );
}
export function Metric({
  label,
  value,
  unit,
  note,
  className = '',
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  note?: ReactNode;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <div className="metric">
      <span className="metric-label">{t(label)}</span>
      <div className={`metric-value ${className}`}>
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {note && <span className="metric-note">{note}</span>}
    </div>
  );
}
export function Empty({
  title,
  children,
  icon: Icon = BarChart3,
}: {
  title: string;
  children: ReactNode;
  icon?: typeof Activity;
}) {
  const { t } = useI18n();
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon size={22} strokeWidth={1.5} />
      </span>
      <h3>{t(title)}</h3>
      <p>{typeof children === 'string' ? t(children) : children}</p>
    </div>
  );
}
export function Loading({ label = 'Loading workspace data…' }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="loading-state" role="status">
      <Loader2 className="spin" size={19} />
      <span>{t(label)}</span>
    </div>
  );
}
export function ErrorBox({
  error,
  onRetry,
  onExample,
}: {
  error: unknown;
  onRetry?: () => void;
  onExample?: () => void;
}) {
  const { t } = useI18n();
  const err = error as Error;
  return (
    <div className="error-box" role="alert">
      <CircleAlert size={19} />
      <div>
        <strong>
          {error instanceof ApiError && error.status === 401
            ? t('Authentication required')
            : t('This data is unavailable')}
        </strong>
        <p>{err?.message ?? 'An unexpected error occurred.'}</p>
        {error instanceof ApiError && error.requestId && <code>Request {error.requestId}</code>}
        <div className="error-actions">
          {onRetry && (
            <button className="text-button" onClick={onRetry}>
              <RefreshCw size={13} />
              {t('Retry')}
            </button>
          )}
          {onExample && (
            <button className="text-button" onClick={onExample}>
              {t('Use synthetic example')}
              <ArrowRight size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
function labelControls(children: ReactNode, label: string, hintId?: string): ReactNode {
  return Children.map(children, (child) => {
    if (
      !isValidElement<{ children?: ReactNode; 'aria-label'?: string; 'aria-describedby'?: string }>(
        child,
      )
    )
      return child;
    if (typeof child.type === 'string' && ['input', 'select', 'textarea'].includes(child.type))
      return cloneElement(child, {
        'aria-label': child.props['aria-label'] ?? label,
        ...(hintId
          ? {
              'aria-describedby': [child.props['aria-describedby'], hintId]
                .filter(Boolean)
                .join(' '),
            }
          : {}),
      });
    return child.props.children
      ? cloneElement(child, { children: labelControls(child.props.children, label, hintId) })
      : child;
  });
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  const { t } = useI18n();
  const hintId = useId();
  return (
    <label className="field">
      <span>{t(label)}</span>
      {labelControls(children, t(label), hint ? hintId : undefined)}
      {hint && (
        <small id={hintId} aria-hidden="true">
          {t(hint)}
        </small>
      )}
    </label>
  );
}
export function ActionNote({ text, error = false }: { text: string | null; error?: boolean }) {
  return text ? (
    <div className={`action-note ${error ? 'is-error' : ''}`} role={error ? 'alert' : 'status'}>
      {error ? <CircleAlert size={15} /> : <Check size={15} />}
      <span>{text}</span>
    </div>
  ) : null;
}
export function SourceBadge({ source }: { source: Source }) {
  const { t } = useI18n();
  return (
    <span className={`source-badge ${source === 'example' ? 'synthetic' : ''}`}>
      <Database size={12} />
      {t(source === 'example' ? 'Example · synthetic' : 'OKX · public market data')}
    </span>
  );
}
export function StrategyFields({
  value,
  onChange,
}: {
  value: Strategy;
  onChange: (s: Strategy) => void;
}) {
  const { t } = useI18n();
  const patch = (key: keyof Strategy, v: string | number) => onChange({ ...value, [key]: v });
  return (
    <>
      <Field label="Strategy">
        <select value={value.kind} onChange={(e) => patch('kind', e.target.value)}>
          <option value="sma_cross">{t('Moving average crossover')}</option>
          <option value="rsi_reversion">{t('RSI mean reversion')}</option>
          <option value="buy_hold">{t('Buy & hold')}</option>
        </select>
      </Field>
      {value.kind === 'sma_cross' && (
        <div className="form-grid">
          <Field label="Fast window">
            <input
              type="number"
              min="2"
              max="200"
              required
              value={value.fast}
              onChange={(e) => patch('fast', Number(e.target.value))}
            />
          </Field>
          <Field label="Slow window">
            <input
              type="number"
              min="3"
              max="400"
              required
              value={value.slow}
              onChange={(e) => patch('slow', Number(e.target.value))}
            />
          </Field>
        </div>
      )}
      {value.kind === 'rsi_reversion' && (
        <>
          <Field label="RSI period">
            <input
              type="number"
              min="2"
              max="200"
              required
              value={value.rsi_period}
              onChange={(e) => patch('rsi_period', Number(e.target.value))}
            />
          </Field>
          <div className="form-grid">
            <Field label="Entry below">
              <input
                type="number"
                min="0"
                max="99"
                required
                value={value.entry}
                onChange={(e) => patch('entry', e.target.value)}
              />
            </Field>
            <Field label="Exit above">
              <input
                type="number"
                min="1"
                max="100"
                required
                value={value.exit}
                onChange={(e) => patch('exit', e.target.value)}
              />
            </Field>
          </div>
        </>
      )}
      <Field
        label="Capital allocation"
        hint="Fraction of available cash allocated on entry, including fees."
      >
        <div className="input-suffix">
          <input
            type="number"
            min="1"
            max="100"
            step="1"
            required
            value={Number(value.allocation) * 100}
            onChange={(e) => patch('allocation', String(Number(e.target.value) / 100))}
          />
          <span>%</span>
        </div>
      </Field>
    </>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{t(eyebrow)}</div>
        <h1>{t(title)}</h1>
        <p>{t(description)}</p>
      </div>
      {children && <div className="heading-actions">{children}</div>}
    </div>
  );
}
export function SymbolSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select aria-label="Trading pair" value={value} onChange={(e) => onChange(e.target.value)}>
      {symbols.map((s) => (
        <option key={s} value={s}>
          {s.replace('-', ' / ')}
        </option>
      ))}
    </select>
  );
}
export function BarSwitch({ value, onChange }: { value: Bar; onChange: (b: Bar) => void }) {
  return (
    <div className="segmented" aria-label="Candle interval">
      {bars.map((b) => (
        <button
          key={b}
          onClick={() => onChange(b)}
          className={value === b ? 'selected' : ''}
          aria-pressed={value === b}
        >
          {barLabel(b)}
        </button>
      ))}
    </div>
  );
}
