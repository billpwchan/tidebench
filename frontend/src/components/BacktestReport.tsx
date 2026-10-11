import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowDownToLine, ArrowUpRight, Info } from 'lucide-react';
import { downloadBlob, downloadCsv } from '../api';
import { proApi, type ProResult, type ProRun, type RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, number, percent, tone } from '../lib/format';
import { DataTable, JsonDetails, WorkspaceTabs } from './ProWorkspace';
import { ErrorBox, Loading } from './workspace';
import ResearchChart from './ResearchChart';

const record = (value: unknown): RecordData =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as RecordData) : {};
const rows = (value: unknown): RecordData[] =>
  Array.isArray(value)
    ? (value.filter((item) => item !== null && typeof item === 'object') as RecordData[])
    : [];
const numeric = (value: unknown) =>
  value === undefined || value === null || value === ''
    ? null
    : Number.isFinite(Number(value))
      ? Number(value)
      : null;

export default function BacktestReport({
  run,
  result,
  variant,
  scope,
  onInspect,
}: {
  run: ProRun;
  result: ProResult;
  variant: string;
  scope: string;
  onInspect: (variant: string) => void;
}) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const [section, setSection] = useState('overview');
  const [chartMetric, setChartMetric] = useState<'equity' | 'returns'>('returns');
  const [calendarYear, setCalendarYear] = useState('all');
  const [tradeFilter, setTradeFilter] = useState('all');
  const [matrixMetric, setMatrixMetric] = useState('total_return_pct');
  const equityRows = useMemo(() => rows(result.equity), [result.equity]);
  const metrics = result.metrics ?? {};
  const reasons = record(metrics.metric_reasons);
  const report = useQuery({
    queryKey: ['backtest-report', run.source, run.id, variant, run.manifest?.result_hash],
    queryFn: async () => {
      const response = await proApi.backtestReport(run.id, variant);
      if (
        response.run_id !== run.id ||
        response.source !== run.source ||
        response.variant !== variant ||
        (run.manifest?.result_hash && response.result_hash !== run.manifest.result_hash)
      )
        throw new Error(
          text(
            'The diagnostic report does not match the selected captured result.',
            '诊断报告与当前选择的已保存结果不匹配。',
          ),
        );
      return response;
    },
    enabled: run.status === 'completed' && !!variant,
    staleTime: 300000,
  });
  const diagnostics = record(report.data?.diagnostics);
  const sample = record(diagnostics.sample);
  const calendar = record(diagnostics.calendar);
  const drawdowns = record(diagnostics.drawdowns);
  const trades = record(diagnostics.trades);
  const contribution = record(diagnostics.contribution);
  const exposure = record(diagnostics.exposure);
  const validation = record(report.data?.validation);
  const risk = record(diagnostics.rolling_risk);
  const dailyDistribution = record(diagnostics.daily_distribution);
  const duration = (value: unknown) => {
    const n = numeric(value);
    return n === null
      ? '—'
      : n >= 86400000
        ? `${number(n / 86400000, 1)} ${text('days', '天')}`
        : `${number(n / 3600000, 1)} ${text('hours', '小时')}`;
  };
  const reason = (key: string) => {
    const code = String(reasons[key] ?? '');
    if (code === 'nonpositive_equity')
      return text('Unavailable after zero or negative equity.', '权益曾为零或负数，指标不可用。');
    if (code.includes('insufficient_complete_utc_days'))
      return `${number(metrics.complete_utc_days, 0)} ${text(key === 'cagr' || key === 'calmar' ? 'complete UTC days; 365 required' : 'complete UTC days; 30 required', key === 'cagr' || key === 'calmar' ? '个完整 UTC 日；需要 365 日' : '个完整 UTC 日；需要 30 日')}`;
    if (code === 'zero_variance')
      return text('No variation in observed returns.', '观察收益没有变动。');
    if (code === 'no_downside_variation')
      return text('No observed downside variation.', '未观察到下行收益变动。');
    if (code === 'no_completed_losing_round_trips')
      return text('No completed losing trades; ratio undefined.', '无已完成亏损交易，比率未定义。');
    if (code === 'no_completed_round_trips')
      return text('No completed trades.', '没有已完成的交易。');
    if (code === 'zero_drawdown')
      return text('No observed drawdown; ratio undefined.', '未观察到回撤，比率未定义。');
    return code ? text('Unavailable for this sample.', '该样本无法计算。') : '';
  };
  const statistic = (label: string, value: unknown, unit = '', note?: string, digits = 2) => (
    <div className="backtest-stat" key={label}>
      <dt>{label}</dt>
      <dd>
        {unit === '%' ? percent(value) : number(value, digits)}
        {unit && unit !== '%' && <small> {unit}</small>}
      </dd>
      {note && <span>{note}</span>}
    </div>
  );
  const comparison =
    numeric(metrics.total_return_pct) !== null && numeric(metrics.benchmark_return_pct) !== null
      ? Number(metrics.total_return_pct) - Number(metrics.benchmark_return_pct)
      : null;
  const benchmarkNote = text(
    'Passive proxy: cost-adjusted, unlevered buy & hold. Allocation and exposure can differ. For perpetuals, the proxy uses underlying trade prices and excludes funding.',
    '被动基准：计入交易成本、不加杠杆的买入持有。资金分配与敞口可能不同；永续基准使用标的成交价格且不计资金费。',
  );
  const monthly = rows(calendar.monthly);
  const terminalDrawdown = rows(result.equity).at(-1)?.drawdown_pct;
  const years = [...new Set(monthly.map((row) => String(row.period).slice(0, 4)))];
  const trips = rows(result.round_trips).filter(
    (trip) =>
      tradeFilter === 'all' ||
      (tradeFilter === 'wins' ? Number(trip.net_pnl) > 0 : Number(trip.net_pnl) < 0),
  );
  const experiments = rows(run.result?.experiments);
  const matrixDimensions =
    run.config.mode === 'cost_stress'
      ? {
          x: 'slippage_bps',
          y: 'fee_bps',
          xLabel: text('Slippage · bps', '滑点 · bps'),
          yLabel: text('Fee · bps', '费用 · bps'),
        }
      : run.config.strategy.kind === 'sma_cross'
        ? {
            x: 'slow',
            y: 'fast',
            xLabel: text('Slow window', '慢线窗口'),
            yLabel: text('Fast window', '快线窗口'),
          }
        : undefined;
  const parameter = (experiment: RecordData, key: string) =>
    run.config.mode === 'cost_stress'
      ? record(experiment.parameters)[key]
      : record(record(experiment.parameters).strategy)[key];
  const xValues = matrixDimensions
    ? [
        ...new Set(
          experiments.map((experiment) => String(parameter(experiment, matrixDimensions.x))),
        ),
      ].sort((a, b) => Number(a) - Number(b))
    : [];
  const yValues = matrixDimensions
    ? [
        ...new Set(
          experiments.map((experiment) => String(parameter(experiment, matrixDimensions.y))),
        ),
      ].sort((a, b) => Number(a) - Number(b))
    : [];
  const matrixCoordinates = matrixDimensions
    ? experiments.map((experiment) =>
        JSON.stringify([
          parameter(experiment, matrixDimensions.x),
          parameter(experiment, matrixDimensions.y),
        ]),
      )
    : [];
  const uniqueMatrix = matrixCoordinates.length === new Set(matrixCoordinates).size;

  return (
    <section className="backtest-report" aria-label={text('Backtest analysis', '回测分析')}>
      <div className="backtest-outcomes" aria-label={text('Performance summary', '绩效摘要')}>
        <div className="backtest-outcome main">
          <span>{text('Net return', '净收益')}</span>
          <strong className={tone(metrics.total_return_pct)}>
            {percent(metrics.total_return_pct)}
          </strong>
          <small>
            {text('Final equity', '期末权益')} {number(metrics.final_equity)} USDT
          </small>
        </div>
        <div className="backtest-outcome">
          <span>{text('Passive benchmark', '被动基准')}</span>
          <strong>{percent(metrics.benchmark_return_pct)}</strong>
          <small title={benchmarkNote}>
            {comparison === null ? '—' : `${comparison > 0 ? '+' : ''}${number(comparison)} pp`}{' '}
            {text('return difference', '收益差额')}
          </small>
        </div>
        <div className="backtest-outcome">
          <span>{text('Max drawdown', '最大回撤')}</span>
          <strong className="negative">
            {metrics.max_drawdown_pct == null
              ? '—'
              : percent(-Math.abs(Number(metrics.max_drawdown_pct)))}
          </strong>
          <small>{text('Observed at bar close', '按 K 线收盘观察')}</small>
        </div>
        <div className="backtest-outcome">
          <span>{text('Sharpe ratio', '夏普比率')}</span>
          <strong>{number(metrics.sharpe)}</strong>
          <small>
            {metrics.sharpe == null ? reason('sharpe') : text('Daily · √365', '日收益 · √365')}
          </small>
        </div>
        <div className="backtest-outcome">
          <span>{text('Completed trades', '完整交易')}</span>
          <strong>{number(metrics.round_trips, 0)}</strong>
          <small>
            {number(metrics.complete_utc_days, 0)} {text('complete UTC days', '个完整 UTC 日')}
          </small>
        </div>
      </div>
      <div className="backtest-scope-note">
        <Info size={14} />
        <span>
          {scope === 'out_of_sample'
            ? text(
                'Out-of-sample window. Review recorded holdout usage before claiming untouched independence.',
                '样本外时段。需复核保留样本使用记录，才能确认未受研究过程影响。',
              )
            : scope === 'training'
              ? text(
                  'Training candidate. These results are development evidence.',
                  '训练候选。这些结果仅是开发阶段证据。',
                )
              : text(
                  'Development evidence. A full-window result is not an independent validation.',
                  '开发阶段证据。全时段结果不构成独立验证。',
                )}{' '}
          {Number(metrics.round_trips) < 30
            ? text(
                'Fewer than 30 completed trades; trade estimates are based on a small sample.',
                '完整交易少于 30 笔，交易统计基于小样本。',
              )
            : ''}
        </span>
      </div>
      {(Number(metrics.liquidations) > 0 ||
        Number(metrics.insurance_shortfall) > 0 ||
        Number(metrics.final_equity) <= 0) && (
        <div className="backtest-critical" role="status">
          {text('Capital impairment', '资金损失风险')} · {number(metrics.liquidations, 0)}{' '}
          {text('liquidations', '次强平')} · {text('Insurance liability', '保险责任欠额')}{' '}
          {number(metrics.insurance_shortfall)} USDT.{' '}
          {text(
            'Inspect the ledger and risk assumptions before evaluating further.',
            '继续评估前请检查账本与风险假设。',
          )}
        </div>
      )}
      <WorkspaceTabs
        value={section}
        onChange={setSection}
        items={[
          { key: 'overview', label: text('Performance', '绩效') },
          { key: 'returns', label: text('Return calendar', '收益日历') },
          { key: 'risk', label: text('Risk & recovery', '风险与恢复') },
          { key: 'trades', label: text('Trade analysis', '交易分析') },
          { key: 'validation', label: text('Robustness', '稳健性') },
          { key: 'economics', label: text('Economics', '经济核对') },
        ]}
      />
      <div
        className="backtest-report-body"
        role="tabpanel"
        aria-label={text('Selected analysis', '当前分析')}
      >
        {section === 'overview' && report.isSuccess && (
          <>
            <ResearchChart
              title={text('Growth of capital', '资金走势')}
              metricControls={
                <div className="segmented">
                  <button
                    aria-pressed={chartMetric === 'returns'}
                    className={chartMetric === 'returns' ? 'selected' : ''}
                    onClick={() => setChartMetric('returns')}
                  >
                    {text('Return · %', '收益 · %')}
                  </button>
                  <button
                    aria-pressed={chartMetric === 'equity'}
                    className={chartMetric === 'equity' ? 'selected' : ''}
                    onClick={() => setChartMetric('equity')}
                  >
                    {text('Equity · USDT', '权益 · USDT')}
                  </button>
                </div>
              }
              rows={equityRows}
              metric={chartMetric}
              initialCash={metrics.initial_cash}
              startTs={numeric(sample.start_ts) ?? undefined}
              linkedDrawdown
            />
            <p className="backtest-method-note">
              {benchmarkNote}{' '}
              {text(
                'The return difference is descriptive, not risk-adjusted alpha.',
                '收益差额是描述性比较，不是经过风险调整的超额收益。',
              )}{' '}
              {text(
                'Sharpe uses daily returns, √365 and a zero risk-free rate.',
                '夏普比率使用日收益、√365 年化和零无风险利率。',
              )}
            </p>
            <dl className="backtest-stat-grid">
              {statistic(text('Sortino', '索提诺比率'), metrics.sortino, '', reason('sortino'))}
              {statistic(
                text('Annualized volatility', '年化波动'),
                metrics.annualized_volatility_pct,
                '%',
                reason('annualized_volatility'),
              )}
              {statistic(text('Exposure time', '持仓时间占比'), metrics.exposure_pct, '%')}
              {statistic(
                text('Notional turnover', '成交名义金额周转'),
                metrics.turnover,
                '×',
                text('Executed notional / starting capital', '成交名义金额 / 初始资金'),
              )}
              {statistic(text('Win rate', '胜率'), metrics.win_rate_pct, '%', reason('win_rate'))}
              {statistic(
                text('Profit factor', '盈亏比因子'),
                metrics.profit_factor,
                '',
                reason('profit_factor'),
              )}
            </dl>
          </>
        )}
        {report.isPending && (
          <Loading
            label={text(
              'Calculating diagnostics from the captured result…',
              '正在根据已保存结果计算诊断…',
            )}
          />
        )}
        {report.isError && <ErrorBox error={report.error} onRetry={() => void report.refetch()} />}
        {section === 'returns' && report.data && (
          <>
            <div className="backtest-section-heading">
              <div>
                <h3>{text('Monthly return map', '月度收益图')}</h3>
                <p>
                  {text(
                    'UTC calendar periods. Partial months are marked; missing months are not zero returns.',
                    'UTC 自然时段；明确标注不完整月份，缺失月份不视为零收益。',
                  )}
                </p>
              </div>
              <label className="backtest-inline-control">
                {text('Year', '年份')}
                <select
                  aria-label={text('Return calendar year', '收益日历年份')}
                  value={calendarYear}
                  onChange={(event) => setCalendarYear(event.target.value)}
                >
                  <option value="all">{text('All years', '全部年份')}</option>
                  {years.map((year) => (
                    <option key={year}>{year}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="table-scroll backtest-calendar">
              <table>
                <caption>{text('Monthly net strategy returns · %', '策略月度净收益 · %')}</caption>
                <thead>
                  <tr>
                    <th>{text('Year', '年份')}</th>
                    {Array.from({ length: 12 }, (_, month) => (
                      <th key={month}>
                        {new Intl.DateTimeFormat(language === 'zh-CN' ? 'zh-CN' : 'en', {
                          month: 'short',
                          timeZone: 'UTC',
                        }).format(new Date(Date.UTC(2025, month, 1)))}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {years
                    .filter((year) => calendarYear === 'all' || calendarYear === year)
                    .map((year) => (
                      <tr key={year}>
                        <th>{year}</th>
                        {Array.from({ length: 12 }, (_, month) => {
                          const cell = monthly.find(
                            (row) => row.period === `${year}-${String(month + 1).padStart(2, '0')}`,
                          );
                          return (
                            <td
                              key={month}
                              className={cell ? tone(cell.return_pct) : 'missing'}
                              title={
                                cell
                                  ? `${cell.period}: ${percent(cell.return_pct)} · ${cell.complete ? text('Complete calendar month', '完整自然月') : text('Partial calendar month', '不完整自然月')}`
                                  : text('No observation', '无观察数据')
                              }
                            >
                              {cell ? (
                                <>
                                  {percent(cell.return_pct)}
                                  {!cell.complete && <small>*</small>}
                                </>
                              ) : (
                                '—'
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            {!monthly.length && (
              <p className="backtest-method-note">
                {text('No calendar observations are available.', '暂无自然时段观察。')}
              </p>
            )}
            <p className="backtest-method-note">
              *{' '}
              {text(
                'Partial period. Returns use observed boundary equity, not inferred missing prices.',
                '不完整时段；按实际观察到的边界权益计算，不推断缺失价格。',
              )}
            </p>
            <div className="backtest-section-heading">
              <h3>{text('Daily return observations', '日收益观察')}</h3>
              <button
                className="text-button"
                disabled={!rows(calendar.daily).length}
                onClick={() =>
                  downloadCsv(
                    rows(calendar.daily),
                    `tidebench-${run.id}-${variant}-daily-returns.csv`,
                  )
                }
              >
                <ArrowDownToLine size={14} />
                {Number(calendar.daily_omitted_count) > 0
                  ? text('Export displayed days', '导出显示日')
                  : text('Export CSV', '导出 CSV')}
              </button>
            </div>
            <dl className="backtest-stat-grid">
              {statistic(
                text('Median daily return', '日收益中位数'),
                dailyDistribution.median_return_pct,
                '%',
              )}
              {statistic(
                text('Best complete day', '最佳完整日'),
                dailyDistribution.best_return_pct,
                '%',
                String(dailyDistribution.best_day ?? ''),
              )}
              {statistic(
                text('Worst complete day', '最差完整日'),
                dailyDistribution.worst_return_pct,
                '%',
                String(dailyDistribution.worst_day ?? ''),
              )}
              {statistic(
                text('5th percentile', '第 5 百分位'),
                dailyDistribution.p05_return_pct,
                '%',
                Number(dailyDistribution.count) < 30
                  ? text('30 complete days required', '需要 30 个完整日')
                  : undefined,
              )}
              {statistic(
                text('95th percentile', '第 95 百分位'),
                dailyDistribution.p95_return_pct,
                '%',
              )}
              {statistic(
                text('Worst 5% mean', '最差 5% 均值'),
                dailyDistribution.empirical_lower_tail_mean_pct,
                '%',
                text('Historical · requires 100 days', '历史描述 · 需要 100 日'),
              )}
            </dl>
            {Number(calendar.daily_omitted_count) > 0 && (
              <p className="backtest-method-note">
                {text('Displaying and exporting the latest', '当前显示与导出最近')}{' '}
                {rows(calendar.daily).length} / {number(calendar.daily_count, 0)}{' '}
                {text(
                  'observed days. Distribution statistics use the entire eligible sample; export captured financial evidence for the full path.',
                  '个观察日。分布统计使用全部有效样本；完整路径可通过原始金融证据导出。',
                )}
              </p>
            )}
            {Number(calendar.monthly_omitted_count) > 0 && (
              <p className="backtest-method-note">
                {text('Monthly map retains the latest', '月度图保留最近')} {monthly.length} /{' '}
                {number(calendar.monthly_count, 0)} {text('observed months.', '个观察月。')}
              </p>
            )}
            <DataTable
              rows={rows(calendar.daily)}
              pageSize={30}
              columns={[
                { key: 'period', label: text('UTC day', 'UTC 日'), sortable: true },
                {
                  key: 'return_pct',
                  label: text('Net return', '净收益'),
                  render: (row) => (
                    <span className={tone(row.return_pct)}>{percent(row.return_pct)}</span>
                  ),
                  sortable: true,
                  sortType: 'number',
                },
                {
                  key: 'benchmark_return_pct',
                  label: text('Passive return', '被动收益'),
                  render: (row) => percent(row.benchmark_return_pct),
                },
                {
                  key: 'active_return_pct',
                  label: text('Difference · pp', '差额 · 百分点'),
                  render: (row) => number(row.active_return_pct),
                },
                {
                  key: 'complete',
                  label: text('Observation', '观察范围'),
                  render: (row) =>
                    row.complete ? text('Full day', '完整日') : text('Partial day', '不完整日'),
                },
              ]}
            />
          </>
        )}
        {section === 'risk' && report.data && (
          <>
            <div className="backtest-section-heading">
              <div>
                <h3>{text('Drawdown and recovery', '回撤与恢复')}</h3>
                <p>
                  {text(
                    'Loss depth is only half the question. Inspect time underwater and whether capital recovered.',
                    '损失深度只是问题的一半；同时检查水下时长与资金是否恢复。',
                  )}
                </p>
              </div>
            </div>
            <dl className="backtest-stat-grid">
              {statistic(text('CAGR', '复合年化收益'), metrics.cagr_pct, '%', reason('cagr'))}
              {statistic(text('Calmar', '卡玛比率'), metrics.calmar, '', reason('calmar'))}
              <div className="backtest-stat">
                <dt>{text('Longest recovery interval', '最长回撤时段')}</dt>
                <dd>{duration(drawdowns.longest_duration_ms)}</dd>
                <span>{text('Including unfinished episodes', '包含尚未恢复的时段')}</span>
              </div>
              {statistic(
                text('Underwater observations', '水下观察占比'),
                drawdowns.underwater_observation_pct,
                '%',
              )}
              {statistic(
                text('Recovered episodes', '已恢复回撤'),
                drawdowns.recovered_count,
                '',
                undefined,
                0,
              )}
              <div className="backtest-stat">
                <dt>{text('Current drawdown', '期末回撤')}</dt>
                <dd>
                  {numeric(terminalDrawdown) === null
                    ? '—'
                    : percent(-Math.abs(Number(terminalDrawdown)))}
                </dd>
              </div>
            </dl>
            <ResearchChart rows={equityRows} metric="drawdown" height={220} />
            <h3 className="backtest-subheading">
              {text('Deepest drawdown episodes', '最深回撤时段')}
            </h3>
            {Number(drawdowns.omitted_episode_count) > 0 && (
              <p className="backtest-method-note">
                {text('Displaying the deepest', '显示最深的')} {rows(drawdowns.episodes).length} /{' '}
                {number(drawdowns.episode_count, 0)}{' '}
                {text(
                  'observed episodes; durations include unfinished episodes.',
                  '个观察到的回撤时段；时长包含尚未恢复的时段。',
                )}
              </p>
            )}
            <DataTable
              rows={rows(drawdowns.episodes)}
              columns={[
                {
                  key: 'peak_ts',
                  label: text('Peak · UTC', '峰值 · UTC'),
                  render: (row) => date(Number(row.peak_ts), true),
                },
                {
                  key: 'trough_ts',
                  label: text('Trough · UTC', '谷底 · UTC'),
                  render: (row) => date(Number(row.trough_ts), true),
                },
                {
                  key: 'depth_pct',
                  label: text('Depth', '深度'),
                  render: (row) => (
                    <span className="negative">
                      {numeric(row.depth_pct) === null
                        ? '—'
                        : percent(-Math.abs(Number(row.depth_pct)))}
                    </span>
                  ),
                },
                {
                  key: 'duration_ms',
                  label: text('Time underwater', '水下时长'),
                  render: (row) => duration(row.duration_ms),
                },
                {
                  key: 'recovery_ts',
                  label: text('Recovery · UTC', '恢复 · UTC'),
                  render: (row) =>
                    row.recovery_ts == null
                      ? text('Unrecovered at end', '期末尚未恢复')
                      : date(Number(row.recovery_ts), true),
                },
              ]}
            />
            <p className="backtest-method-note">
              {text(
                'Bar-close observations can miss intrabar losses. Unrecovered episodes are right-censored at the evaluation end; their eventual recovery is unknown.',
                '收盘观察可能遗漏 K 线内损失；尚未恢复的回撤在评估结束时截断，其最终恢复时间未知。',
              )}
            </p>
            <h3 className="backtest-subheading">{text('Rolling 30-day risk', '滚动 30 日风险')}</h3>
            {Number(risk.omitted_point_count) > 0 && (
              <p className="backtest-method-note">
                {rows(risk.points).length} / {number(risk.eligible_window_count, 0)}{' '}
                {text(
                  'eligible windows displayed, evenly sampled across the full history; intervening windows are omitted.',
                  '个有效窗口被均匀抽样显示，覆盖全部历史；中间部分窗口未显示。',
                )}
              </p>
            )}
            {rows(risk.points).length ? (
              <DataTable
                rows={rows(risk.points)}
                pageSize={15}
                columns={[
                  {
                    key: 'ts',
                    label: text('Window end · UTC', '窗口结束 · UTC'),
                    render: (row) => date(Number(row.ts), true),
                  },
                  {
                    key: 'sharpe',
                    label: text('Sharpe', '夏普比率'),
                    render: (row) => number(row.sharpe),
                  },
                  {
                    key: 'annualized_volatility_pct',
                    label: text('Volatility', '年化波动'),
                    render: (row) => percent(row.annualized_volatility_pct),
                  },
                ]}
              />
            ) : (
              <p className="backtest-method-note">
                {text(
                  'Rolling risk requires 30 consecutive complete UTC daily returns and positive equity.',
                  '滚动风险需要连续 30 个完整 UTC 日收益，且权益保持为正。',
                )}
              </p>
            )}
            <p className="backtest-method-note">
              {text(
                '√365 annualization assumes comparable daily observations; Sharpe is descriptive and is not adjusted for serial dependence or repeated trials.',
                '√365 年化基于可比较的日收益观察；夏普是描述性统计，未对序列相关或多次试验作调整。',
              )}
            </p>
          </>
        )}
        {section === 'trades' && (
          <>
            <div className="backtest-section-heading">
              <div>
                <h3>{text('Where returns came from', '收益来自哪些交易')}</h3>
                <p>
                  {text(
                    'Completed round trips, distinct from fills. Open inventory remains in final equity.',
                    '完整交易与成交记录分开统计；未平仓头寸仍计入期末权益。',
                  )}
                </p>
              </div>
            </div>
            <dl className="backtest-stat-grid">
              {statistic(
                text('Completed trades', '完整交易'),
                metrics.round_trips,
                '',
                undefined,
                0,
              )}
              {statistic(text('Win rate', '胜率'), metrics.win_rate_pct, '%', reason('win_rate'))}
              {statistic(
                text('Profit factor', '盈亏比因子'),
                metrics.profit_factor,
                '',
                reason('profit_factor'),
              )}
              {statistic(text('Mean net trade P&L', '平均净交易损益'), trades.expectancy, 'USDT')}
              {statistic(text('Largest gain', '最大单笔盈利'), trades.largest_winner, 'USDT')}
              {statistic(text('Largest loss', '最大单笔亏损'), trades.largest_loser, 'USDT')}
            </dl>
            {report.data && (
              <dl className="backtest-stat-grid">
                {statistic(
                  text('Top 5 winners / gross profit', '前五盈利交易 / 毛盈利'),
                  trades.top_five_winner_share_of_gross_profit_pct,
                  '%',
                )}
                {statistic(
                  text('Largest winner / gross profit', '最大盈利交易 / 毛盈利'),
                  trades.largest_winner_share_of_gross_profit_pct,
                  '%',
                )}
                {statistic(text('Median trade P&L', '交易损益中位数'), trades.median_pnl, 'USDT')}
                {statistic(
                  text('Trade P&L · 5th percentile', '交易损益 · 第 5 百分位'),
                  trades.p05_pnl,
                  'USDT',
                )}
                <div className="backtest-stat">
                  <dt>{text('Mean holding time', '平均持仓时长')}</dt>
                  <dd>{duration(trades.mean_holding_ms)}</dd>
                </div>
                <div className="backtest-stat">
                  <dt>{text('Median holding time', '持仓时长中位数')}</dt>
                  <dd>{duration(trades.median_holding_ms)}</dd>
                </div>
              </dl>
            )}
            {rows(trades.histogram).length > 0 && (
              <div
                className="backtest-histogram"
                role="img"
                aria-label={text('Distribution of completed trade net P&L', '完整交易净损益分布')}
              >
                {rows(trades.histogram).map((bin, index) => (
                  <div
                    key={index}
                    title={`${number(bin.lower)} → ${number(bin.upper)} USDT · ${bin.count}`}
                  >
                    <span>{String(bin.count)}</span>
                    <i
                      className={Number(bin.upper) <= 0 ? 'loss' : 'gain'}
                      style={{
                        height: `${Math.max(2, (Number(bin.count) / Math.max(...rows(trades.histogram).map((row) => Number(row.count)), 1)) * 100)}px`,
                      }}
                    />
                    <small>{number(bin.lower, 0)}</small>
                  </div>
                ))}
              </div>
            )}
            {report.data && (
              <div className="backtest-trade-breakdowns">
                {(['by_direction', 'by_exit_reason'] as const).map((key) => (
                  <section key={key}>
                    <h3 className="backtest-subheading">
                      {key === 'by_direction'
                        ? text('Direction contribution', '方向贡献')
                        : text('Exit reason contribution', '退出原因贡献')}
                    </h3>
                    <DataTable
                      rows={rows(trades[key])}
                      columns={[
                        {
                          key: 'label',
                          label:
                            key === 'by_direction'
                              ? text('Direction', '方向')
                              : text('Exit reason', '退出原因'),
                        },
                        {
                          key: 'count',
                          label: text('Trades', '交易笔数'),
                          render: (row) => number(row.count, 0),
                        },
                        {
                          key: 'net_pnl',
                          label: text('Net P&L · USDT', '净损益 · USDT'),
                          render: (row) => (
                            <span className={tone(row.net_pnl)}>{number(row.net_pnl)}</span>
                          ),
                        },
                      ]}
                    />
                  </section>
                ))}
              </div>
            )}
            <div className="backtest-section-heading">
              <h3>{text('Completed-trade ledger', '完整交易账簿')}</h3>
              <div className="backtest-actions">
                <label className="backtest-inline-control">
                  {text('Show', '筛选')}
                  <select
                    value={tradeFilter}
                    aria-label={text('Completed trade filter', '完整交易筛选')}
                    onChange={(event) => setTradeFilter(event.target.value)}
                  >
                    <option value="all">{text('All trades', '全部交易')}</option>
                    <option value="wins">{text('Winners', '盈利交易')}</option>
                    <option value="losses">{text('Losers', '亏损交易')}</option>
                  </select>
                </label>
                <button
                  className="text-button"
                  disabled={!trips.length}
                  onClick={() =>
                    downloadCsv(trips, `tidebench-${run.id}-${variant}-completed-trades.csv`)
                  }
                >
                  <ArrowDownToLine size={14} />
                  {text('Export CSV', '导出 CSV')}
                </button>
              </div>
            </div>
            <DataTable
              rows={trips}
              pageSize={20}
              columns={[
                {
                  key: 'entry_ts',
                  label: text('Entry · UTC', '入场 · UTC'),
                  render: (row) => date(Number(row.entry_ts), true),
                  sortable: true,
                  sortType: 'number',
                },
                {
                  key: 'exit_ts',
                  label: text('Exit · UTC', '退出 · UTC'),
                  render: (row) => date(Number(row.exit_ts), true),
                },
                { key: 'direction', label: text('Direction', '方向') },
                {
                  key: 'net_pnl',
                  label: text('Net P&L · USDT', '净损益 · USDT'),
                  render: (row) => <span className={tone(row.net_pnl)}>{number(row.net_pnl)}</span>,
                  sortable: true,
                  sortType: 'number',
                },
                { key: 'fees', label: text('Fees', '费用'), render: (row) => number(row.fees) },
                {
                  key: 'funding_payment',
                  label: text('Funding paid', '支付资金费'),
                  render: (row) => number(row.funding_payment),
                },
                { key: 'exit_reason', label: text('Exit reason', '退出原因') },
              ]}
            />
          </>
        )}
        {section === 'validation' && (
          <>
            <div className="backtest-section-heading">
              <div>
                <h3>{text('Evidence across windows and assumptions', '跨时段与假设的证据')}</h3>
                <p>
                  {text(
                    'Look for repeatability and sensitivity, not only the highest return.',
                    '检查可重复性与敏感性，同时避免只关注最高收益。',
                  )}
                </p>
              </div>
            </div>
            {rows(validation.folds).length > 0 && (
              <>
                <dl className="backtest-stat-grid">
                  {statistic(
                    text('Test folds', '测试时段'),
                    validation.test_fold_count,
                    '',
                    undefined,
                    0,
                  )}
                  {statistic(
                    text('Positive folds', '正收益时段'),
                    validation.positive_test_folds,
                    '',
                    undefined,
                    0,
                  )}
                  {statistic(
                    text('Above passive reference', '超过被动基准时段'),
                    validation.benchmark_outperforming_folds,
                    '',
                    undefined,
                    0,
                  )}
                  {statistic(
                    text('Median test return', '测试收益中位数'),
                    validation.median_return_pct,
                    '%',
                  )}
                  {statistic(
                    text('Training evaluations', '训练候选评估'),
                    validation.candidate_evaluations,
                    '',
                    undefined,
                    0,
                  )}
                  {statistic(
                    text('Unused tail bars', '未使用尾部 K 线'),
                    validation.unused_tail_bars,
                    '',
                    undefined,
                    0,
                  )}
                </dl>
                <DataTable
                  rows={rows(validation.folds)}
                  columns={[
                    {
                      key: 'id',
                      label: text('Test fold', '测试时段'),
                      render: (row) => (
                        <button className="text-button" onClick={() => onInspect(`${row.id}:test`)}>
                          {String(row.id)}
                          <ArrowUpRight size={12} />
                        </button>
                      ),
                    },
                    {
                      key: 'test_start_ts',
                      label: text('Start · UTC', '开始 · UTC'),
                      render: (row) => date(Number(row.test_start_ts), true),
                    },
                    {
                      key: 'test_end_ts',
                      label: text('End · UTC', '结束 · UTC'),
                      render: (row) => date(Number(row.test_end_ts), true),
                    },
                    {
                      key: 'return_pct',
                      label: text('Net return', '净收益'),
                      render: (row) => percent(row.return_pct),
                    },
                    {
                      key: 'return_difference_pp',
                      label: text('Difference · pp', '差额 · 百分点'),
                      render: (row) => number(row.return_difference_pp),
                    },
                    { key: 'complete_utc_days', label: text('Complete days', '完整日') },
                    { key: 'round_trips', label: text('Completed trades', '完整交易') },
                  ]}
                />
                <p className="backtest-method-note">
                  {text(
                    'Each fold starts with its own flat account. These returns are not compounded and do not represent one investable track record. Reused test windows may be contaminated by earlier research.',
                    '每个时段以独立空仓账户开始，收益不复合，不代表一条可投资的连续记录。重复使用的测试时段可能受之前研究影响。',
                  )}
                </p>
              </>
            )}
            {!!experiments.length && matrixDimensions && uniqueMatrix && (
              <>
                <div className="backtest-section-heading">
                  <h3>
                    {run.config.mode === 'cost_stress'
                      ? text('Cost sensitivity', '成本敏感性')
                      : text('Parameter landscape', '参数表现分布')}
                  </h3>
                  <label className="backtest-inline-control">
                    {text('Measure', '指标')}
                    <select
                      value={matrixMetric}
                      aria-label={text('Sensitivity measure', '敏感性指标')}
                      onChange={(event) => setMatrixMetric(event.target.value)}
                    >
                      <option value="total_return_pct">{text('Net return', '净收益')}</option>
                      <option value="max_drawdown_pct">{text('Max drawdown', '最大回撤')}</option>
                      <option value="sharpe">{text('Sharpe ratio', '夏普比率')}</option>
                    </select>
                  </label>
                </div>
                <div className="table-scroll backtest-matrix">
                  <table>
                    <caption>
                      {matrixDimensions.yLabel} × {matrixDimensions.xLabel} ·{' '}
                      {text(
                        'Select a cell to inspect its captured result',
                        '选择单元格查看该候选已保存结果',
                      )}
                    </caption>
                    <thead>
                      <tr>
                        <th>
                          {matrixDimensions.yLabel} ↓ / {matrixDimensions.xLabel} →
                        </th>
                        {xValues.map((x) => (
                          <th key={x}>{x}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {yValues.map((y) => (
                        <tr key={y}>
                          <th>{y}</th>
                          {xValues.map((x) => {
                            const candidate = experiments.find(
                              (experiment) =>
                                String(parameter(experiment, matrixDimensions.y)) === y &&
                                String(parameter(experiment, matrixDimensions.x)) === x,
                            );
                            const value = record(record(candidate?.result).metrics)[matrixMetric];
                            return (
                              <td key={x}>
                                {candidate ? (
                                  <button
                                    className={
                                      matrixMetric === 'max_drawdown_pct' ? 'negative' : tone(value)
                                    }
                                    aria-label={`${y}, ${x}: ${matrixMetric} ${number(value)}`}
                                    aria-pressed={variant === candidate.id}
                                    onClick={() => onInspect(String(candidate.id))}
                                  >
                                    {matrixMetric === 'sharpe' ? number(value) : percent(value)}
                                  </button>
                                ) : (
                                  '—'
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            {!rows(validation.folds).length && !experiments.length && (
              <p className="backtest-method-note">
                {text(
                  'This run has no test folds or sensitivity experiments. Create a next trial using Train / test, Walk-forward or Cost stress to examine robustness.',
                  '本次运行没有测试时段或敏感性试验；下一次可使用训练／测试、滚动前推或成本压力测试评估稳健性。',
                )}
              </p>
            )}
            {!!experiments.length && (!matrixDimensions || !uniqueMatrix) && (
              <>
                <h3 className="backtest-subheading">
                  {text('All sensitivity candidates', '全部敏感性候选')}
                </h3>
                <p className="backtest-method-note">
                  {text(
                    'The grid has additional parameter dimensions. Every candidate is retained below instead of combining different configurations into one cell.',
                    '网格包含额外参数维度。以下保留每个候选，避免将不同配置合并为同一单元格。',
                  )}
                </p>
                <DataTable
                  rows={experiments}
                  pageSize={20}
                  columns={[
                    {
                      key: 'id',
                      label: text('Candidate', '候选'),
                      render: (row) => (
                        <button className="text-button" onClick={() => onInspect(String(row.id))}>
                          {String(row.id)}
                          <ArrowUpRight size={12} />
                        </button>
                      ),
                    },
                    {
                      key: 'parameters',
                      label: text('Parameters', '参数'),
                      render: (row) => <code>{JSON.stringify(row.parameters)}</code>,
                    },
                    {
                      key: 'return',
                      label: text('Net return', '净收益'),
                      render: (row) => percent(record(record(row.result).metrics).total_return_pct),
                    },
                    {
                      key: 'drawdown',
                      label: text('Max drawdown', '最大回撤'),
                      render: (row) => percent(record(record(row.result).metrics).max_drawdown_pct),
                    },
                    {
                      key: 'sharpe',
                      label: text('Sharpe', '夏普比率'),
                      render: (row) => number(record(record(row.result).metrics).sharpe),
                    },
                  ]}
                />
              </>
            )}
            <p className="backtest-method-note">
              {text(
                'Candidate counts describe this run, not every prior trial. Selection-adjusted confidence requires the full research history; no significance or alpha-quality score is inferred here.',
                '候选计数仅描述本次运行，不代表全部历史试验。选择偏差调整需要完整研究历史，这里不推断统计显著性或策略质量分数。',
              )}
            </p>
          </>
        )}
        {section === 'economics' && report.data && (
          <>
            <div className="backtest-section-heading">
              <div>
                <h3>{text('Net economic result', '净经济结果')}</h3>
                <p>
                  {text(
                    'Recorded costs and closed-trade contribution, reconciled against the captured final equity.',
                    '根据记录的成本与完整交易贡献，核对已保存期末权益。',
                  )}
                </p>
              </div>
            </div>
            <dl className="backtest-economics">
              <div>
                <dt>{text('Starting capital', '初始资金')}</dt>
                <dd>{number(metrics.initial_cash)} USDT</dd>
              </div>
              <div>
                <dt>{text('Total equity P&L', '总权益损益')}</dt>
                <dd className={tone(contribution.total_equity_pnl)}>
                  {number(contribution.total_equity_pnl)} USDT
                </dd>
              </div>
              <div>
                <dt>{text('Completed-trade net P&L', '完整交易净损益')}</dt>
                <dd>{number(contribution.closed_net_pnl)} USDT</dd>
              </div>
              <div>
                <dt>{text('Unclosed economic residual', '未完成交易经济残差')}</dt>
                <dd>{number(contribution.unclosed_pnl_residual)} USDT</dd>
              </div>
              <div>
                <dt>{text('All recorded fees', '全部记录费用')}</dt>
                <dd>{number(contribution.fees_paid)} USDT</dd>
              </div>
              <div>
                <dt>{text('Signed funding paid', '支付资金费（带符号）')}</dt>
                <dd>{number(contribution.funding_paid)} USDT</dd>
              </div>
              <div>
                <dt>{text('Modeled execution-price impact', '模型成交价格影响')}</dt>
                <dd>{number(contribution.execution_price_impact)} USDT</dd>
              </div>
              <div className="total">
                <dt>{text('Final net equity', '期末净权益')}</dt>
                <dd>{number(metrics.final_equity)} USDT</dd>
              </div>
            </dl>
            <p className="backtest-method-note">
              {text(
                'Fees and funding are already embedded in equity and completed-trade net P&L. The unclosed residual includes open entry costs, funding and mark-to-market P&L. Execution-price impact includes tick rounding and modeled slippage; it is not an additional deduction.',
                '权益和完整交易净损益已经包含费用与资金费。未完成交易残差包括未平仓入场成本、资金费和盯市损益。成交价格影响包含报价精度取整与模型滑点，不应再次扣除。',
              )}
            </p>
            <dl className="backtest-stat-grid">
              {statistic(text('Exposure time', '持仓时间占比'), metrics.exposure_pct, '%')}
              {statistic(text('Notional turnover', '成交名义金额周转'), metrics.turnover, '×')}
              {statistic(
                text('Mean gross leverage', '平均总杠杆'),
                exposure.mean_gross_leverage,
                '×',
              )}
            </dl>
            <JsonDetails
              value={contribution}
              label={text('Reconciliation definitions', '核对口径')}
            />
          </>
        )}
      </div>
      <details className="backtest-mobile-basis">
        <summary>{text('Summary measurement basis', '摘要指标口径')}</summary>
        <dl className="record-grid">
          <div>
            <dt>{text('Final equity', '期末权益')}</dt>
            <dd>{number(metrics.final_equity)} USDT</dd>
          </div>
          <div>
            <dt>{text('Return difference', '收益差额')}</dt>
            <dd>
              {comparison === null ? '—' : `${comparison > 0 ? '+' : ''}${number(comparison)} pp`}
            </dd>
          </div>
          <div>
            <dt>{text('Drawdown observation', '回撤观察')}</dt>
            <dd>{text('Observed at bar close', '按 K 线收盘观察')}</dd>
          </div>
          <div>
            <dt>{text('Sharpe basis', '夏普口径')}</dt>
            <dd>
              {metrics.sharpe == null
                ? reason('sharpe')
                : text('Daily · √365 · zero risk-free rate', '日收益 · √365 · 无风险利率 0')}
            </dd>
          </div>
          <div>
            <dt>{text('Sample period', '样本时长')}</dt>
            <dd>
              {number(metrics.complete_utc_days, 0)} {text('complete UTC days', '个完整 UTC 日')}
            </dd>
          </div>
        </dl>
      </details>
      {report.data && (
        <details className="backtest-report-evidence">
          <summary>
            {text('Diagnostic method & evidence identity', '诊断方法与证据身份')} ·{' '}
            {String(report.data.diagnostics_version)}
          </summary>
          <p>
            {text(
              'Derived from verified captured results; original financial artifacts are unchanged.',
              '基于校验后的已保存结果推导，原始金融记录保持不变。',
            )}
          </p>
          <button
            className="text-button"
            onClick={() =>
              downloadBlob(
                new Blob([JSON.stringify(report.data, null, 2)], { type: 'application/json' }),
                `tidebench-${run.id}-${variant}-diagnostics.json`,
              )
            }
          >
            <ArrowDownToLine size={14} />
            {text('Export diagnostic report', '导出诊断报告')}
          </button>
          <dl>
            <dt>{text('Result hash', '结果摘要')}</dt>
            <dd>
              <code>{String(report.data.result_hash)}</code>
            </dd>
            <dt>{text('Report code hash', '报告代码摘要')}</dt>
            <dd>
              <code>{String(report.data.report_implementation_hash)}</code>
            </dd>
          </dl>
          <ul>
            {rows(diagnostics.limitations).map((item, index) => (
              <li key={index}>{String(item.message ?? item.code)}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
