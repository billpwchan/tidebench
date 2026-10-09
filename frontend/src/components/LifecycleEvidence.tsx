import { useState } from 'react';
import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, number } from '../lib/format';
import { downloadBlob } from '../api';
import { DataTable, JsonDetails } from './ProWorkspace';
import { ErrorBox, Field, Status } from './workspace';
import './LifecycleEvidence.css';

const kinds = [
  'listing',
  'suspend',
  'resume',
  'rules',
  'delist',
  'cash_settlement',
  'unit_conversion',
];
const record = (value: unknown): RecordData =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as RecordData) : {};
const rows = (value: unknown): RecordData[] =>
  Array.isArray(value) ? (value as RecordData[]) : [];
function useText() {
  const { language } = useI18n();
  return (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
}
export function LifecycleScenario({
  mode,
  warmup,
  onMode,
  onWarmup,
}: {
  mode: string;
  warmup: number;
  onMode: (value: string) => void;
  onWarmup: (value: number) => void;
}) {
  const text = useText();
  return (
    <section
      className="lifecycle-scenario"
      aria-label={text('Historical universe scenario', '历史市场范围情景')}
    >
      <div className="form-grid">
        <Field label={text('Market history', '市场历史模型')}>
          <select value={mode} onChange={(e) => onMode(e.target.value)}>
            <option value="static">
              {text('Static selected markets · captured rules', '静态所选市场 · 捕获当前规则')}
            </option>
            <option value="historical_lifecycle">
              {text('Attributed lifecycle · bounded history', '来源标注生命周期 · 有界历史')}
            </option>
          </select>
        </Field>
        {mode === 'historical_lifecycle' && (
          <Field
            label={text('Per-market listing / resume warmup', '每个市场上市 / 恢复后的预热 bars')}
          >
            <input
              required
              type="number"
              min={1}
              max={400}
              step={1}
              value={warmup}
              onChange={(e) => onWarmup(Number(e.target.value))}
            />
          </Field>
        )}
      </div>
      {mode === 'historical_lifecycle' && (
        <p className="quiet-copy">
          {text(
            'Packages may start at different listing dates within the same source and interval. Unknown markets cannot trade; each listing or resume needs its own contiguous warmup. Source bytes and dates are frozen with this study. This scenario does not establish complete venue history and cannot be promoted to the current paper controller.',
            '相同数据源和周期的包可从各自上市日开始。未知市场不能交易；上市或恢复后需独立连续预热。来源原文与时间会随研究冻结。此情景不代表交易所完整历史，当前模拟控制器不支持发布该生命周期模型。',
          )}
        </p>
      )}
    </section>
  );
}
export function LifecycleImport({
  symbol,
  events,
  onChange,
}: {
  symbol: string;
  events: RecordData[];
  onChange: (events: RecordData[]) => void;
}) {
  const text = useText();
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const importFile = async (file: File) => {
    setError(null);
    setBusy(true);
    try {
      if (file.size > 2 * 1024 * 1024)
        throw new Error(
          text('Use a JSON source file no larger than 2 MB.', 'JSON 来源文件不能超过 2 MB。'),
        );
      const parsed: unknown = JSON.parse(await file.text());
      const object = record(parsed);
      const map = record(
        object.lifecycle_events ?? record(object.holdout_scenario).lifecycle_events,
      );
      const data: unknown = Array.isArray(parsed) ? parsed : map[symbol];
      if (!Array.isArray(data) || data.length > 200)
        throw new Error(
          text(
            'Import an event array or lifecycle_events[market], with at most 200 events.',
            '请导入事件数组或 lifecycle_events[市场]，最多 200 个事件。',
          ),
        );
      for (const value of data) {
        const event = record(value),
          source = record(event.source);
        if (
          event.inst_id !== symbol ||
          !kinds.includes(String(event.kind)) ||
          !Number.isSafeInteger(event.effective_ts) ||
          !Number.isSafeInteger(event.known_at)
        )
          throw new Error(
            text(
              'Every fact must match this market and contain a supported kind, effective_ts and known_at.',
              '每个事实需匹配本市场，具有支持的事件类型、effective_ts 与 known_at。',
            ),
          );
        if (
          typeof source.raw_content !== 'string' ||
          !/^[a-f0-9]{64}$/.test(String(source.content_hash))
        )
          throw new Error(
            text(
              'Every source needs raw_content and its SHA-256 content_hash.',
              '每个来源需具有 raw_content 及对应的 SHA-256 content_hash。',
            ),
          );
        const hash = [
          ...new Uint8Array(
            await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source.raw_content)),
          ),
        ]
          .map((v) => v.toString(16).padStart(2, '0'))
          .join('');
        if (hash !== source.content_hash)
          throw new Error(
            text(
              'Source bytes do not match their declared hash. The existing events were retained.',
              '来源内容与声明哈希不符，已保留原事件。',
            ),
          );
      }
      onChange(data as RecordData[]);
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className="lifecycle-import"
      aria-label={`${text('Lifecycle evidence', '生命周期证据')} · ${symbol || '—'}`}
    >
      <div className="lifecycle-import-heading">
        <strong>
          {text('Lifecycle evidence', '生命周期证据')} ·{' '}
          {symbol || text('Select a package first', '先选择数据包')}
        </strong>
        <span className="quiet-copy">
          {events.length} {text('captured facts', '条捕获事实')}
        </span>
      </div>
      <input
        type="file"
        accept=".json,application/json"
        disabled={!symbol || busy}
        aria-label={`${text('Import lifecycle JSON', '导入生命周期 JSON')} · ${symbol || '—'}`}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) void importFile(file);
        }}
      />
      <p className="quiet-copy">
        {text(
          'Import a JSON array or the selected market from a lifecycle_events map. Official HTTPS attribution and byte hashes are checked; hashes alone do not authenticate historical dates. Missing membership stays unknown.',
          '导入 JSON 数组或 lifecycle_events 映射中的当前市场。系统检查官方 HTTPS 来源标注与原文哈希；哈希本身不能证明历史日期。缺失成员证据仍为未知。',
        )}
      </p>
      {error && <ErrorBox error={error} />}
      {events.length > 0 && (
        <>
          <div className="toolbar">
            <button
              type="button"
              className="text-button"
              onClick={() =>
                downloadBlob(
                  new Blob([JSON.stringify(events, null, 2)], { type: 'application/json' }),
                  `lifecycle-${symbol}.json`,
                )
              }
            >
              {text('Export captured facts', '导出捕获事实')}
            </button>
            <button
              type="button"
              className="text-button"
              onClick={() => {
                onChange([]);
                setError(null);
              }}
            >
              {text('Clear evidence', '清空证据')}
            </button>
          </div>
          <DataTable
            rows={events}
            columns={[
              { key: 'kind', label: text('Event', '事件') },
              {
                key: 'effective_ts',
                label: text('Effective UTC', '生效 UTC'),
                render: (r) => date(Number(r.effective_ts), true),
              },
              {
                key: 'known_at',
                label: text('Known UTC', '可知 UTC'),
                render: (r) => date(Number(r.known_at), true),
              },
              {
                key: 'valid_until',
                label: text('Coverage end', '覆盖结束'),
                render: (r) => (r.valid_until ? date(Number(r.valid_until), true) : '—'),
              },
            ]}
          />
          <JsonDetails
            value={events}
            label={text('Captured source bodies and rules', '捕获的来源内容与规则')}
          />
        </>
      )}
    </div>
  );
}
export default function LifecycleEvidence({
  evidence,
  config,
}: {
  evidence?: RecordData;
  config?: RecordData;
}) {
  const text = useText();
  const [selected, setSelected] = useState('');
  if (!evidence) return null;
  const points = rows(evidence.eligibility);
  const point = points.find((p) => String(p.ts) === selected) ?? points[points.length - 1];
  const markets: RecordData[] = Object.entries(record(point?.markets)).map(([symbol, value]) => ({
    symbol,
    ...record(value),
  }));
  const applications = rows(evidence.applications),
    issues = rows(evidence.issues);
  const sourceMap = new Map<string, RecordData>();
  for (const leg of rows(config?.legs))
    for (const event of rows(leg.lifecycle_events)) {
      const source = record(event.source);
      sourceMap.set(String(source.content_hash), source);
    }
  const states: Record<string, string> = {
    eligible: text('Eligible rules', '规则有效'),
    unknown: text('Unknown coverage', '覆盖未知'),
    suspended: text('Suspended', '已暂停'),
    delisted: text('Removed from trading', '退出交易'),
  };
  const reasons: Record<string, string> = {
    eligible: text('Ready', '已就绪'),
    unknown: text('Unknown coverage', '覆盖未知'),
    suspended: text('Suspended', '已暂停'),
    delisted: text('Removed from trading', '退出交易'),
    warming_up: text('Warming up', '预热中'),
    missing_price_bar: text('Price bar unavailable', '价格 bar 缺失'),
  };
  return (
    <section
      className="lifecycle-evidence"
      aria-label={text('Causal market eligibility', '因果市场资格')}
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">{text('HISTORICAL MARKET EVIDENCE', '历史市场证据')}</span>
          <h3>{text('What was tradable at this boundary', '此时点哪些市场允许交易')}</h3>
        </div>
        <Status type={evidence.status === 'incomplete' ? 'bad' : 'neutral'}>
          {text(
            evidence.status === 'incomplete'
              ? 'Economics incomplete'
              : 'Complete within supplied scope',
            evidence.status === 'incomplete' ? '经济结果未完成' : '在所供范围内完整',
          )}
        </Status>
      </div>
      <p className={evidence.status === 'incomplete' ? 'warning-banner' : 'quiet-copy'}>
        {text(
          evidence.status === 'incomplete'
            ? 'Unresolved inventory is retained. Final equity and risk statistics are unavailable; release is blocked. No last-price settlement or zero write-off was invented.'
            : 'This result covers supplied facts for selected markets. The current paper controller does not execute historical settlement or unit conversion; promotion is blocked.',
          evidence.status === 'incomplete'
            ? '未解决持仓仍予保留，最终权益及风险统计不可用，已阻止发布。系统没有虚构末价结算或零值核销。'
            : '结果仅覆盖所选市场已提供的事实。当前模拟控制器不执行历史结算或单位转换，已阻止该情景发布。',
        )}
      </p>
      {point && (
        <>
          <Field label={text('Eligibility boundary · UTC', '市场资格时点 · UTC')}>
            <select value={String(point.ts)} onChange={(e) => setSelected(e.target.value)}>
              {points.map((p) => (
                <option key={String(p.ts)} value={String(p.ts)}>
                  {date(Number(p.ts), true)}
                </option>
              ))}
            </select>
          </Field>
          <DataTable
            rows={markets}
            columns={[
              { key: 'symbol', label: text('Market', '市场') },
              {
                key: 'state',
                label: text('Membership evidence', '成员证据'),
                render: (r) => states[String(r.state)] ?? String(r.state),
              },
              {
                key: 'tradable',
                label: text('Execution', '交易资格'),
                render: (r) => (
                  <Status type={r.tradable === true ? 'good' : 'warning'}>
                    {r.tradable === true
                      ? text('Ready', '已就绪')
                      : (reasons[String(r.reason)] ?? String(r.reason))}
                  </Status>
                ),
              },
              {
                key: 'warmup_closes',
                label: text('Causal closes', '因果 closes'),
                render: (r) => number(r.warmup_closes, 0),
              },
              {
                key: 'valid_until',
                label: text('Rule coverage end', '规则覆盖结束'),
                render: (r) => (r.valid_until ? date(Number(r.valid_until), true) : '—'),
              },
            ]}
          />
        </>
      )}
      {applications.length > 0 && (
        <>
          <h4>{text('Inventory accounting events', '持仓核算事件')}</h4>
          <DataTable
            rows={applications}
            columns={[
              { key: 'inst_id', label: text('Market', '市场') },
              { key: 'kind', label: text('Event', '事件') },
              {
                key: 'applied_at',
                label: text('Accounted UTC', '入账 UTC'),
                render: (r) => date(Number(r.applied_at), true),
              },
              {
                key: 'quantity_after',
                label: text('Quantity before → after', '数量 前 → 后'),
                render: (r) =>
                  `${String(r.quantity_before ?? '—')} → ${String(r.quantity_after ?? '—')}`,
              },
              {
                key: 'cash_delta',
                label: text('Cash change (USDT)', '现金变动 (USDT)'),
                render: (r) => (r.cash_delta == null ? '—' : number(r.cash_delta, 4)),
              },
              {
                key: 'realized_delta',
                label: text('Realized change (USDT)', '已实现变动 (USDT)'),
                render: (r) => (r.realized_delta == null ? '—' : number(r.realized_delta, 4)),
              },
            ]}
          />
        </>
      )}
      {issues.length > 0 && (
        <>
          <h4>{text('Unresolved evidence and inventory', '未解决证据与持仓')}</h4>
          <DataTable
            rows={issues}
            columns={[
              { key: 'ts', label: text('UTC', 'UTC'), render: (r) => date(Number(r.ts), true) },
              { key: 'inst_id', label: text('Market', '市场') },
              { key: 'code', label: text('Accounting obstacle', '核算障碍') },
              { key: 'quantity', label: text('Retained quantity', '保留数量') },
            ]}
          />
        </>
      )}
      {sourceMap.size > 0 && (
        <>
          <h4>{text('Pinned source evidence', '固定来源证据')}</h4>
          <DataTable
            rows={[...sourceMap.values()]}
            columns={[
              {
                key: 'url',
                label: text('Source', '来源'),
                render: (r) => (
                  <a href={String(r.url)} target="_blank" rel="noreferrer">
                    {String(r.url)}
                  </a>
                ),
              },
              {
                key: 'published_at',
                label: text('Attributed publication', '标注发布时间'),
                render: (r) => date(Number(r.published_at), true),
              },
              {
                key: 'content_hash',
                label: 'SHA-256',
                render: (r) => (
                  <span title={String(r.content_hash)}>{String(r.content_hash).slice(0, 16)}…</span>
                ),
              },
            ]}
          />
        </>
      )}
      <JsonDetails
        value={evidence}
        label={text(
          'Lifecycle trace, source hashes and accounting',
          '生命周期轨迹、来源哈希与核算',
        )}
      />
    </section>
  );
}

export function CapturedLifecycleSources({ config }: { config: RecordData }) {
  const text = useText();
  if (config.universe_mode !== 'historical_lifecycle') return null;
  const facts = rows(config.legs).flatMap((leg) => rows(leg.lifecycle_events));
  const sources = new Map<string, RecordData>();
  for (const event of facts) {
    const source = record(event.source);
    sources.set(String(source.content_hash), source);
  }
  return (
    <section
      className="lifecycle-evidence"
      aria-label={text('Frozen lifecycle sources', '已冻结生命周期来源')}
    >
      <h3>{text('Frozen lifecycle sources', '已冻结生命周期来源')}</h3>
      <p className="quiet-copy">
        {text(
          'Known-at and effective boundaries, source bytes, coverage ends and event rules are pinned to this evaluation. Missing membership remains unknown; source hashes prove integrity, not independently authenticated chronology.',
          '可知 / 生效时点、来源内容、覆盖截止和事件规则均已固定到本次评估。缺失成员证据保持未知；来源哈希证明内容完整性，不独立证明历史时间真实性。',
        )}
      </p>
      <DataTable
        rows={[...sources.values()]}
        columns={[
          {
            key: 'url',
            label: text('Source', '来源'),
            render: (r) => (
              <a href={String(r.url)} target="_blank" rel="noreferrer">
                {String(r.url)}
              </a>
            ),
          },
          {
            key: 'published_at',
            label: text('Attributed publication', '标注发布时间'),
            render: (r) => date(Number(r.published_at), true),
          },
          {
            key: 'content_hash',
            label: 'SHA-256',
            render: (r) => (
              <span title={String(r.content_hash)}>{String(r.content_hash).slice(0, 16)}…</span>
            ),
          },
        ]}
      />
      <JsonDetails value={facts} label={text('Frozen event bodies', '已冻结事件内容')} />
    </section>
  );
}
