import { useState } from 'react';
import { ArrowUpRight, ArrowRight } from 'lucide-react';
import { useI18n } from '../lib/i18n';
import dossiers from '../../../examples/strategy-dossiers.json';
import evidence from '../../../examples/strategy-evidence.json';

type Copy = { en: string; zh: string };
export default function StrategyResearchLibrary({
  canCreate,
  onUse,
}: {
  canCreate: boolean;
  onUse: (id: string) => void;
}) {
  const { language } = useI18n();
  const [selected, setSelected] = useState(dossiers[0].id);
  const [cost, setCost] = useState(20);
  const d = dossiers.find((item) => item.id === selected)!;
  const copy = (value: Copy) => (language === 'zh-CN' ? value.zh : value.en);
  const label = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  return (
    <section
      className="strategy-research-library"
      aria-label={label('Strategy research library', '策略研究库')}
    >
      <header className="strategy-research-intro">
        <span className="eyebrow">{label('RESEARCH NOTES / 01–05', '研究笔记 / 01–05')}</span>
        <h2>{label('A mechanism before a signal.', '先理解收益机制，再构建信号。')}</h2>
        <p>
          {label(
            'Executable hypotheses, source evidence and reasons to reject them. Literature motivates the test; it does not validate an OKX strategy.',
            '这里提供可运行的假设、原始研究依据和淘汰条件。文献支持研究方向，并不能验证 OKX 策略已经有效。',
          )}
        </p>
      </header>
      <div className="strategy-research-layout">
        <nav aria-label={label('Research families', '策略研究方向')}>
          {dossiers.map((item, i) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={item.id === selected}
              onClick={() => setSelected(item.id)}
            >
              <span>0{i + 1}</span>
              <div>
                <strong>{copy(item.title)}</strong>
                <small>{copy(item.subtitle)}</small>
              </div>
              <ArrowUpRight size={15} />
            </button>
          ))}
        </nav>
        <article className="strategy-dossier" aria-live="polite">
          <div className="strategy-dossier-meta">
            <span>{copy(d.scope)}</span>
            <span>{label('Research hypothesis · unvalidated', '研究假设 · 尚未验证优势')}</span>
          </div>
          <h3>{copy(d.title)}</h3>
          <p className="strategy-dossier-thesis">{copy(d.mechanism)}</p>
          <div className="strategy-dossier-formula">
            <span>{label('IMPLEMENTED RULE', '已实现规则')}</span>
            <code>{d.formula}</code>
            <p>{copy(d.rule)}</p>
          </div>
          <div className="strategy-dossier-grid">
            {[
              [label('What the evidence says', '证据能说明什么'), d.evidence],
              [label('Where it breaks', '主要失效情形'), d.failure],
              [label('Cost and capital', '成本与资本'), d.cost],
              [label('Reject the hypothesis if…', '出现这些结果就淘汰'), d.reject],
            ].map(([title, text]) => (
              <section key={title as string}>
                <h4>{title as string}</h4>
                <p>{copy(text as Copy)}</p>
              </section>
            ))}
          </div>
          <section className="strategy-dossier-test">
            <h4>{label('Discriminating experiment', '有区分能力的实验')}</h4>
            <p>{copy(d.experiment)}</p>
          </section>
          {['trend', 'reversion'].includes(selected) && (
            <section className="strategy-observed-evidence">
              <div className="section-heading">
                <h4>
                  {label('Observed OKX checks · no validated edge', 'OKX 实际检验 · 未验证优势')}
                </h4>
                <select
                  aria-label={label('Evidence cost scenario', '证据成本情景')}
                  value={cost}
                  onChange={(e) => setCost(Number(e.target.value))}
                >
                  <option value={10}>{label('10 fee + 5 slip bps', '10 费用＋5 滑点 bps')}</option>
                  <option value={20}>
                    {label('20 fee + 10 slip bps', '20 费用＋10 滑点 bps')}
                  </option>
                </select>
              </div>
              <p>
                {label(
                  'This published evidence uses OKX public spot data independently of the selected workspace source. Fixed 108-case battery, three selected current markets, two flat-start windows. Shared windows and cost scenarios are correlated. The default momentum was positive in 4/12 scenarios; reversion in 6/12. The filter did not consistently improve its ablation.',
                  '此处发布的证据来自 OKX 公开现货数据，与当前工作空间选择的数据源独立。固定的 108 案例检验，三个当前选定品种、两个独立空仓启动时段。共享时段与成本情景具有相关性。默认动量 4/12 情景为正，回归为 6/12；过滤器未一致改善消融版本。',
                )}
              </p>
              <div
                className="table-scroll"
                tabIndex={0}
                role="region"
                aria-label={label('Observed strategy results', '实际策略结果')}
              >
                <table>
                  <thead>
                    <tr>
                      {[
                        'Market',
                        'Window',
                        'Net %',
                        'Passive %',
                        ...(selected === 'reversion' ? ['No filter %'] : []),
                        'Round trips',
                      ].map((title, i) => (
                        <th key={title}>
                          {label(
                            title,
                            [
                              '品种',
                              '时段',
                              '净收益 %',
                              '被动 %',
                              ...(selected === 'reversion' ? ['无过滤 %'] : []),
                              '完整往返',
                            ][i],
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {evidence.rows
                      .filter((row) => row.family === selected && row.fee_bps === cost)
                      .map((row) => (
                        <tr key={row.inst_id + row.window}>
                          <td>{row.inst_id}</td>
                          <td>
                            <small>
                              {new Date(row.start).toISOString().slice(0, 10)} →{' '}
                              {new Date(row.end).toISOString().slice(0, 10)}
                            </small>
                          </td>
                          <td>{row.return_pct.toFixed(3)}</td>
                          <td>{row.passive_return_pct.toFixed(3)}</td>
                          {selected === 'reversion' && (
                            <td>{row.ablation_return_pct.toFixed(3)}</td>
                          )}
                          <td>{row.completed_round_trips}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <p>
                {label(
                  'Both use a 20% allocation cap; strategy stops and loss budgets reduce actual exposure, so the passive comparison is not risk matched. Current rules and a selected universe are scenario inputs. These are exploratory checks, not one-use blinded tests or significance claims.',
                  '均使用 20% 配置上限；策略止损与亏损预算会降低实际仓位，因此被动对照并未匹配风险。当前规则与选定交易池均为情景输入。此处为探索性检验，不是一次性盲测或显著性声明。',
                )}
              </p>
              <a
                href="https://github.com/billpwchan/tidebench/blob/main/docs/strategy-research.md"
                target="_blank"
                rel="noreferrer"
              >
                {label(
                  'Read all cases, methods and negative results',
                  '查看全部案例、方法与负面结果',
                )}
                <ArrowUpRight size={12} />
              </a>
            </section>
          )}
          <div className="strategy-dossier-sources">
            <span>{label('Original sources', '原始来源')}</span>
            {d.sources.map((source) => (
              <a key={source.url} href={source.url} target="_blank" rel="noreferrer">
                {source.title}
                <ArrowUpRight size={12} />
              </a>
            ))}
          </div>
          {d.recipe ? (
            <button
              type="button"
              className="button button-citrus"
              disabled={!canCreate}
              onClick={() => onUse(d.recipe)}
            >
              {label('Use this hypothesis', '使用此假设')}
              <ArrowRight size={14} />
            </button>
          ) : (
            <p className="quiet-copy">
              {label(
                'Open Research → Portfolio research and load the matching portfolio starting point. Both legs use one book and one capital balance.',
                '在策略研究 → 组合研究中加载对应组合配方。两条腿使用同一账本与资金余额。',
              )}
            </p>
          )}
        </article>
      </div>
    </section>
  );
}
