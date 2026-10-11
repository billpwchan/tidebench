import { ArrowRight } from 'lucide-react';
import type { RecordData } from '../proApi';
import { date, number } from '../lib/format';
import { useI18n } from '../lib/i18n';

const numeric = (value: unknown) =>
  value === null || value === undefined || value === '' || !Number.isFinite(Number(value))
    ? null
    : Number(value);

const utcDay = (value: unknown) => {
  const timestamp = numeric(value);
  return timestamp === null ? '—' : new Date(timestamp).toISOString().slice(0, 10);
};

export default function ResearchDecisionSummary({
  scope,
  config,
  metrics,
  sample,
  verified,
  onAnalysis,
  onDraft,
}: {
  scope: string;
  config: RecordData;
  metrics: RecordData;
  sample: RecordData;
  verified: boolean;
  onAnalysis: (section: string) => void;
  onDraft?: () => void;
}) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const days = numeric(sample.complete_utc_days ?? metrics.complete_utc_days);
  const trades = numeric(sample.closed_trades ?? metrics.round_trips);
  const smallSample = (days !== null && days < 30) || (trades !== null && trades < 30);
  const test = scope === 'out_of_sample';
  const training = scope === 'training';
  const costs = scope === 'cost_sensitivity';
  const impaired =
    (numeric(metrics.liquidations) ?? 0) > 0 ||
    (numeric(metrics.insurance_shortfall) ?? 0) > 0 ||
    (numeric(metrics.final_equity) !== null && Number(metrics.final_equity) <= 0);
  const title = !verified
    ? text('Verifying captured evidence…', '正在校验已保存证据…')
    : training
      ? text('Training candidate.', '训练候选。')
      : test
        ? text('Out-of-sample window.', '样本外时段。')
        : costs
          ? text('Cost sensitivity.', '成本敏感性。')
          : text('Development evidence.', '开发阶段证据。');
  const scopeNote = test
    ? text(
        'Out-of-sample window. Review recorded holdout usage before claiming untouched independence.',
        '样本外时段。需复核保留样本使用记录，才能确认未受研究过程影响。',
      )
    : training
      ? text(
          'Training candidate. These results are development evidence.',
          '训练候选。这些结果仅是开发阶段证据。',
        )
      : costs
        ? text(
            'Cost scenarios reuse the evaluated path; they are sensitivity evidence, not independent tests.',
            '成本情景重复使用评估路径，只提供敏感性证据，不构成独立测试。',
          )
        : text(
            'Development evidence. A full-window result is not an independent validation.',
            '开发阶段证据。全时段结果不构成独立验证。',
          );
  const next = impaired
    ? text('Inspect risk', '检查风险')
    : training || test
      ? text('Review test windows', '复核测试时段')
      : costs
        ? text('Inspect costs', '检查成本')
        : text('Plan next test', '规划下一次测试');
  return (
    <section className="research-decision" aria-label={text('Evidence decision', '证据决策')}>
      <details className="research-decision-basis">
        <summary>
          <strong>{title}</strong>
          {verified && (
            <span className={smallSample ? 'research-sample-limited' : ''}>
              {number(days, 0)} {text('UTC days', '个 UTC 日')} · {number(trades, 0)}{' '}
              {text('trades', '笔交易')}
              {smallSample ? ` · ${text('limited sample', '样本有限')}` : ''}
            </span>
          )}
          <span className="research-decision-assumptions">
            {verified && (
              <span>
                {utcDay(sample.start_ts)} → {utcDay(sample.end_ts)} UTC
              </span>
            )}
            <span>
              {text('Fee', '费用')} {number(config.fee_bps)} + {text('slippage', '滑点')}{' '}
              {number(config.slippage_bps)} bps
            </span>
          </span>
        </summary>
        <div className="research-decision-detail">
          <p>{scopeNote}</p>
          <dl className="record-grid">
            <div>
              <dt>{text('Captured UTC window', '已保存 UTC 时段')}</dt>
              <dd>
                {date(numeric(sample.start_ts) ?? undefined, true)} →{' '}
                {date(numeric(sample.end_ts) ?? undefined, true)}
              </dd>
            </div>
            <div>
              <dt>{text('Sample limits', '样本限制')}</dt>
              <dd>
                {smallSample
                  ? text(
                      'Fewer than 30 complete days or completed trades. Estimates are descriptive; a few observations can dominate.',
                      '完整 UTC 日或完整交易少于 30。统计仅作描述，少量观测可能主导结果。',
                    )
                  : text(
                      'Returns and trades can be dependent. Sample size does not establish statistical reliability.',
                      '收益和交易可能存在依赖性；样本数量不代表统计可靠性。',
                    )}
              </dd>
            </div>
            <div>
              <dt>{text('Recorded cost assumptions', '记录的成本假设')}</dt>
              <dd>
                {text('Fee', '费用')} {number(config.fee_bps)} + {text('slippage', '滑点')}{' '}
                {number(config.slippage_bps)} bps · {text('Liquidation allowance', '清算附加费用')}{' '}
                {number(config.liquidation_fee_bps)} bps
              </dd>
            </div>
            <div>
              <dt>{text('Next decision', '下一步决策')}</dt>
              <dd>
                {impaired
                  ? text(
                      'Inspect the ledger and risk assumptions before another trial.',
                      '下一次试验前检查账本与风险假设。',
                    )
                  : training || test
                    ? text(
                        'Inspect selection history, separate test windows and recorded holdout usage.',
                        '检查选择历史、独立测试时段与保留样本使用记录。',
                      )
                    : costs
                      ? text(
                          'Compare recorded fees, funding and execution-price sensitivity.',
                          '比较记录费用、资金费与成交价格敏感性。',
                        )
                      : text(
                          'Define the next test and record selection choices in the separate draft.',
                          '在独立草稿中定义下一次测试并记录选择过程。',
                        )}
              </dd>
            </div>
          </dl>
          <p>
            {text(
              'Returns and Sharpe describe this captured sample. Paper admission is determined by the server review, not by these metrics.',
              '收益与夏普描述本次已保存样本。模拟准入由服务器审查决定，不由这些指标决定。',
            )}
          </p>
        </div>
      </details>
      <button
        className="text-button research-decision-next"
        disabled={!verified}
        onClick={() => {
          if (impaired) onAnalysis('risk');
          else if (training || test) onAnalysis('validation');
          else if (costs) onAnalysis('economics');
          else if (onDraft) onDraft();
          else onAnalysis('validation');
        }}
      >
        {next}
        <ArrowRight size={13} />
      </button>
    </section>
  );
}
