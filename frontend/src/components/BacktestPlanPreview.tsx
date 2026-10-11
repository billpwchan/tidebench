import { useI18n } from '../lib/i18n';
import { number } from '../lib/format';
import type { ResearchMode } from '../proApi';

const intervals: Record<string, number> = {
  '1m': 60000,
  '5m': 300000,
  '15m': 900000,
  '1H': 3600000,
  '4H': 14400000,
  '1Dutc': 86400000,
};
export default function BacktestPlanPreview({
  bar,
  start,
  end,
  mode,
  trainFraction,
  trainBars,
  testBars,
  stepBars,
  purgeBars,
  cases,
}: {
  bar?: string;
  start: string;
  end: string;
  mode: ResearchMode;
  trainFraction: number;
  trainBars: number;
  testBars: number;
  stepBars: number;
  purgeBars: number;
  cases: number;
}) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const interval = intervals[bar ?? ''];
  const startTs = Date.parse(`${start}Z`),
    endTs = Date.parse(`${end}Z`);
  if (!interval || !Number.isFinite(startTs) || !Number.isFinite(endTs) || endTs <= startTs)
    return null;
  const size = Math.floor((endTs - startTs) / interval);
  const training = mode === 'train_test' ? Math.floor(size * trainFraction) : trainBars;
  const testing = mode === 'train_test' ? size - training - purgeBars : testBars;
  const testStart = startTs + (training + purgeBars) * interval;
  const utcDays = Math.max(
    0,
    Math.floor((testStart + testing * interval) / 86400000) - Math.ceil(testStart / 86400000),
  );
  const folds =
    mode === 'walk_forward' && stepBars > 0
      ? Math.max(0, Math.floor((size - training - purgeBars - testing) / stepBars) + 1)
      : mode === 'train_test'
        ? 1
        : 0;
  const oos = mode === 'train_test' || mode === 'walk_forward';
  const overlap = mode === 'walk_forward' && stepBars < testBars;
  const duration = (bars: number) =>
    `${number((bars * interval) / 86400000, 1)} ${text('days', '天')}`;
  return (
    <div
      className="backtest-plan-preview"
      aria-label={text('Evaluation plan preview', '评估计划预览')}
    >
      <strong>{text('Evaluation contract', '评估契约')}</strong>
      <dl>
        <div>
          <dt>{text('Captured window', '数据时段')}</dt>
          <dd>
            {number(size, 0)} {text('bars', '根 K 线')} · {duration(size)}
          </dd>
        </div>
        <div>
          <dt>{text('Candidate evaluations', '候选评估')}</dt>
          <dd>
            {number(cases, 0)}
            {folds > 1 ? ` × ${folds} ${text('folds', '个时段')}` : ''}
          </dd>
        </div>
        {oos && (
          <>
            <div>
              <dt>{text('Training / test', '训练／测试')}</dt>
              <dd>
                {duration(training)} / {duration(testing)}
              </dd>
            </div>
            <div>
              <dt>{text('Purge gap', '隔离间隔')}</dt>
              <dd>
                {purgeBars} {text('bars', '根 K 线')} · {duration(purgeBars)}
              </dd>
            </div>
          </>
        )}
      </dl>
      {oos && (
        <div
          className="backtest-window-timeline"
          aria-label={text(
            'Training, purge and test window proportions',
            '训练、隔离与测试时段比例',
          )}
        >
          <span className="train" style={{ flex: Math.max(training, 1) }}>
            {text('Train', '训练')}
          </span>
          <span
            className="purge"
            style={{ flex: Math.max(purgeBars, 1) }}
            title={text('Purge gap', '隔离间隔')}
          />
          <span className="test" style={{ flex: Math.max(testing, 1) }}>
            {text('Test', '测试')}
          </span>
        </div>
      )}
      <p>
        {overlap
          ? text(
              'Test windows overlap. Increase Step bars to at least Test bars before submitting.',
              '测试时段重叠，请将前推步长调整为不小于测试时段。',
            )
          : oos && folds < 1
            ? text(
                'The selected window cannot fit a complete training/test fold.',
                '所选数据时段无法容纳完整训练／测试时段。',
              )
            : oos && utcDays < 30
              ? `${text('First test window', '首个测试时段')}: ${utcDays} ${text('complete UTC days. Sharpe, Sortino and annualized volatility require 30; use a longer test window when needed.', '个完整 UTC 日。夏普、索提诺和年化波动需要 30 日；如需这些指标，请延长测试时段。')}`
              : text(
                  'Date boundaries and candidate validity are verified by the server. Results do not imply an untouched final holdout.',
                  '服务器将检查时段边界与候选有效性；结果不代表未经使用的最终保留样本。',
                )}
      </p>
    </div>
  );
}
