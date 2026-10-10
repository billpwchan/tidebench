import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';
import { useState } from 'react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { Page } from '../lib/config';
import { date } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { useSession } from './AuthGate';
import { ErrorBox, Loading, Status } from './workspace';

type JourneyItem = {
  key: string;
  id?: string;
  title: string;
  detail: string;
  action: string;
  page: Page;
  view?: string;
  status?: string;
  progress?: number;
  attention?: boolean;
  updated?: number;
};

const active = (status: string) =>
  ['queued', 'running', 'preparing', 'cancelling'].includes(status);
const measuredProgress = (progress: unknown) =>
  typeof progress === 'number' && Number.isFinite(progress) && progress >= 0 && progress <= 1
    ? progress
    : undefined;

export default function WorkspaceJourney({
  source,
  navigate,
}: {
  source: Source;
  navigate: (page: Page, view?: string) => void;
}) {
  const { language, t } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const session = useSession();
  const [showAll, setShowAll] = useState(false);
  const [showFailures, setShowFailures] = useState(false);
  const identity = session?.user?.id ?? session?.user?.username ?? 'anonymous';
  const snapshot = useQuery({
    queryKey: ['workspace-journey', window.location.origin, identity, session?.user?.role, source],
    queryFn: async () => {
      const [packages, runs, portfolioRuns, releases, portfolioReleases, deployments, groups] =
        await Promise.all([
          proApi.packages(source),
          proApi.runs(source),
          proApi.portfolioRuns(source),
          proApi.releases(source),
          proApi.portfolioReleases(source),
          proApi.deployments(source),
          proApi.managedPortfolios(source),
        ]);
      return { packages, runs, portfolioRuns, releases, portfolioReleases, deployments, groups };
    },
    refetchInterval: 5000,
    refetchOnMount: 'always',
    retry: 1,
  });
  const items: JourneyItem[] = [];
  const failures: JourneyItem[] = [];
  const data = snapshot.data;
  if (data && !snapshot.isError) {
    const packages = data.packages.items.filter((item) => item.source === source);
    const runs = data.runs.items.filter((item) => !item.source || item.source === source);
    const portfolioRuns = data.portfolioRuns.items.filter((item) => item.source === source);
    const groups = data.groups.items.filter((item) => item.source === source);
    const deployments = data.deployments.items.filter((item) => item.source === source);
    const members = new Set(
      groups.flatMap((group) => group.manifest?.legs.map((leg) => leg.deployment_id) ?? []),
    );
    const independent = deployments.filter((item) => !item.group_id && !members.has(item.id));
    const releasedRuns = new Set(data.releases.items.map((item) => item.run_id));
    const releasedPortfolioRuns = new Set(
      data.portfolioReleases.items
        .filter((item) => item.source === source)
        .map((item) => item.approval.preview.run_id),
    );
    for (const group of groups) {
      if (
        !group.attention &&
        !group.integrity_error &&
        (group.status === 'stopped' ||
          (!group.last_error && !['failed', 'compensating'].includes(group.status)))
      )
        continue;
      items.push({
        key: `group:${group.id}`,
        id: group.id,
        title: text('Portfolio group needs attention', '组合执行组需要处理'),
        detail:
          group.attention?.error ??
          group.last_error ??
          text('Inspect retained inventory and recovery evidence.', '请检查保留持仓与恢复证据。'),
        action: text('Inspect group', '查看执行组'),
        page: 'execution',
        view: `managed:${group.id}`,
        status: group.status,
        attention: true,
      });
    }
    for (const deployment of independent) {
      if (
        deployment.status === 'stopped' ||
        (!deployment.last_error && !['failed', 'blocked', 'error'].includes(deployment.status))
      )
        continue;
      items.push({
        key: `deployment:${deployment.id}`,
        id: deployment.id,
        title: `${deployment.inst_id} · ${text('Deployment needs attention', '部署需要处理')}`,
        detail:
          deployment.last_error ??
          text('Inspect the current execution condition.', '请检查当前执行状态。'),
        action: text('Inspect deployment', '查看部署'),
        page: 'execution',
        view: `strategies:${deployment.id}`,
        status: deployment.status,
        attention: true,
      });
    }
    for (const item of packages.filter(
      (item) => !item.ready && ['failed', 'blocked'].includes(item.status),
    )) {
      failures.push({
        key: `package:${item.id}`,
        id: item.id,
        title: `${item.inst_id} · ${text('Data preparation blocked', '数据准备受阻')}`,
        detail:
          item.blockers?.[0]?.message ??
          text(
            'Inspect coverage and component errors before retrying.',
            '重试前请检查覆盖范围与组件错误。',
          ),
        action: text('Inspect package', '查看数据包'),
        page: 'data',
        view: `package:${item.id}`,
        status: item.status,
        attention: true,
        updated: item.updated_at ?? item.created_at,
      });
    }
    for (const item of runs.filter((item) => item.status === 'failed')) {
      failures.push({
        key: `run:${item.id}`,
        id: item.id,
        title: text('Research failed', '研究任务失败'),
        detail:
          item.error ??
          text(
            'Inspect the saved failure before changing inputs.',
            '修改输入前，请检查保存的失败原因。',
          ),
        action: text('Inspect research', '查看研究'),
        page: 'research',
        view: `run:${item.id}`,
        status: item.status,
        attention: true,
        updated: item.updated_at ?? item.created_at,
      });
    }
    for (const item of portfolioRuns.filter((item) => item.status === 'failed')) {
      failures.push({
        key: `portfolio-run:${item.id}`,
        id: item.id,
        title: text('Portfolio research failed', '组合研究失败'),
        detail:
          item.error ??
          text(
            'Inspect the saved failure and execution evidence.',
            '请检查保存的失败原因与执行证据。',
          ),
        action: text('Inspect portfolio research', '查看组合研究'),
        page: 'research',
        view: `portfolio:${item.id}`,
        status: item.status,
        attention: true,
        updated: item.updated_at ?? item.created_at,
      });
    }
    for (const item of packages.filter((item) => !item.ready && active(item.status))) {
      items.push({
        key: `package:${item.id}`,
        id: item.id,
        title: `${item.inst_id} · ${text('Preparing research data', '正在准备研究数据')}`,
        detail: text(
          'The package remains unavailable for research until its input checks pass.',
          '输入检查通过前，数据包仍不能用于研究。',
        ),
        action: text('Inspect preparation', '查看准备进度'),
        page: 'data',
        view: `package:${item.id}`,
        status: item.status,
        progress: measuredProgress(item.progress),
      });
    }
    for (const item of runs.filter((item) => active(item.status))) {
      items.push({
        key: `run:${item.id}`,
        id: item.id,
        title: text('Research is running', '研究正在进行'),
        detail: text(
          'Open this saved task to inspect its current computation.',
          '打开此已保存任务，查看当前计算状态。',
        ),
        action: text('Open research task', '打开研究任务'),
        page: 'research',
        view: `run:${item.id}`,
        status: item.status,
        progress: measuredProgress(item.progress),
      });
    }
    for (const item of portfolioRuns.filter((item) => active(item.status))) {
      items.push({
        key: `portfolio-run:${item.id}`,
        id: item.id,
        title: text('Portfolio research is running', '组合研究正在进行'),
        detail: text(
          'Inspect this task; a finished calculation will still require an evidence review.',
          '请检查此任务；计算结束后仍需审查证据。',
        ),
        action: text('Open portfolio task', '打开组合任务'),
        page: 'research',
        view: `portfolio:${item.id}`,
        status: item.status,
        progress: measuredProgress(item.progress),
      });
    }
    for (const item of data.releases.items.filter((item) => item.status === 'approved')) {
      items.push({
        key: `release:${item.id}`,
        id: item.id,
        title: text('Saved paper approval awaits activation', '已保存的模拟批准等待激活'),
        detail: text(
          'Inspect the exact approval. Activation rechecks current costs and risk.',
          '请查看具体批准。激活时会重新检查当前成本与风险。',
        ),
        action: text('Inspect approval', '查看批准'),
        page: 'execution',
        view: `releases:${item.id}`,
        status: item.status,
      });
    }
    for (const item of data.portfolioReleases.items.filter(
      (item) => item.source === source && item.status === 'approved',
    )) {
      items.push({
        key: `portfolio-release:${item.id}`,
        id: item.id,
        title: text('Saved portfolio approval awaits activation', '已保存的组合批准等待激活'),
        detail: text(
          'Review the whole portfolio approval and current admission checks.',
          '请审查整组组合批准与当前准入检查。',
        ),
        action: text('Inspect portfolio approval', '查看组合批准'),
        page: 'execution',
        view: `portfolio-release:${item.id}`,
        status: item.status,
      });
    }
    for (const item of portfolioRuns.filter(
      (item) => item.status === 'completed' && !releasedPortfolioRuns.has(item.id),
    )) {
      items.push({
        key: `portfolio-run:${item.id}`,
        id: item.id,
        title: text('Review a portfolio research result', '审查组合研究结果'),
        detail: text(
          'Computation is complete. Inspect execution outcomes, test scope and costs before any paper release.',
          '计算已结束。模拟发布前仍需检查执行结果、检验范围与成本。',
        ),
        action: text('Review result', '审查结果'),
        page: 'research',
        view: `portfolio:${item.id}`,
        status: item.status,
      });
    }
    for (const item of runs.filter(
      (item) => item.status === 'completed' && !releasedRuns.has(item.id),
    )) {
      items.push({
        key: `run:${item.id}`,
        id: item.id,
        title: text('Review a research result', '审查研究结果'),
        detail: text(
          'Historical research is not observed account performance. Review test scope and assumptions.',
          '历史研究并非账户实测表现。请审查检验范围与假设。',
        ),
        action: text('Review result', '审查结果'),
        page: 'research',
        view: `run:${item.id}`,
        status: item.status,
      });
    }
    for (const group of groups.filter(
      (item) => item.status === 'running' && !items.some((row) => row.key === `group:${item.id}`),
    )) {
      items.push({
        key: `group:${group.id}`,
        id: group.id,
        title: text('Inspect a running paper portfolio', '查看运行中的模拟组合'),
        detail: text(
          'Review current inventory and group state; then inspect observed performance.',
          '请检查当前持仓与执行组状态，再检查实测表现。',
        ),
        action: text('Inspect portfolio', '查看组合'),
        page: 'execution',
        view: `managed:${group.id}`,
        status: group.status,
      });
    }
    for (const deployment of independent.filter(
      (item) =>
        item.status === 'running' && !items.some((row) => row.key === `deployment:${item.id}`),
    )) {
      items.push({
        key: `deployment:${deployment.id}`,
        id: deployment.id,
        title: `${deployment.inst_id} · ${text('Inspect a running paper strategy', '查看运行中的模拟策略')}`,
        detail: text(
          'Review the specific deployment and its observed performance. Research returns do not prove performance.',
          '请审查具体部署与实测表现。研究收益不能证明实测表现。',
        ),
        action: text('Inspect deployment', '查看部署'),
        page: 'execution',
        view: `strategies:${deployment.id}`,
        status: deployment.status,
      });
    }
    if (!items.length) {
      for (const item of packages
        .filter((item) => item.ready && item.status === 'ready')
        .slice(0, 2)) {
        items.push({
          key: `package:${item.id}`,
          id: item.id,
          title: `${item.inst_id} · ${text('Research inputs ready', '研究输入已就绪')}`,
          detail: text(
            'Open this package to use its exact dataset versions and UTC window.',
            '打开此数据包，使用其精确数据版本与 UTC 区间。',
          ),
          action: text('Open prepared inputs', '打开已准备输入'),
          page: 'data',
          view: `package:${item.id}`,
          status: item.status,
        });
      }
    }
    if (!items.length) {
      const empty =
        !packages.length &&
        !runs.length &&
        !portfolioRuns.length &&
        !data.releases.items.length &&
        !data.portfolioReleases.items.length &&
        !deployments.length &&
        !groups.length &&
        !data.runs.next_cursor;
      items.push({
        key: 'prepare',
        title: empty
          ? text('Prepare your first research package', '准备第一个研究数据包')
          : text('Start a new research study', '开始新的研究'),
        detail: text(
          'Choose a market and UTC window. A versioned package is the input to research, not a trading signal.',
          '选择市场与 UTC 区间。版本化数据包是研究输入，并非交易信号。',
        ),
        action: text('Prepare research data', '准备研究数据'),
        page: 'data',
        view: 'packages',
      });
    }
  }

  failures.sort((a, b) => (b.updated ?? 0) - (a.updated ?? 0));
  const renderItem = (item: JourneyItem) => (
    <li className="journey-item" key={item.key} data-task={item.key}>
      <div className="journey-detail">
        <strong>{item.title}</strong>
        {item.status && (
          <Status type={item.attention ? 'warning' : 'neutral'}>{t(item.status)}</Status>
        )}
        <p>{item.detail}</p>
        {item.id && <code>{item.id}</code>}
        {item.progress !== undefined && (
          <div className="journey-progress">
            <progress
              max={1}
              value={item.progress}
              aria-label={`${item.title} · ${text('Progress', '进度')}`}
            />
            <span>{Math.round(item.progress * 100)}%</span>
          </div>
        )}
      </div>
      <button className="text-button journey-action" onClick={() => navigate(item.page, item.view)}>
        {item.action}
        <ArrowRight size={14} />
      </button>
    </li>
  );

  return (
    <section className="workspace-journey" aria-label={text('Next actions', '下一步操作')}>
      <div className="journey-heading">
        <div>
          <span className="eyebrow">{text('CONTINUE YOUR WORK', '继续工作')}</span>
          <h2>{text('Next actions', '下一步操作')}</h2>
        </div>
        {snapshot.isSuccess && !snapshot.isError && (
          <span className="journey-status">
            {text('Last checked', '最近检查')} · {date(snapshot.dataUpdatedAt, true)}
          </span>
        )}
      </div>
      <p className="journey-note">
        {source === 'example'
          ? text('Synthetic example · separate paper account.', '合成示例 · 独立模拟账户。')
          : text(
              'OKX public data · local simulated execution.',
              'OKX 公开数据 · 本地模拟执行。',
            )}{' '}
        {text(
          'Data → research review → paper release → observed performance.',
          '数据 → 研究审查 → 模拟发布 → 实测表现。',
        )}
      </p>
      {snapshot.isPending ? (
        <Loading label={text('Loading current tasks…', '正在加载当前任务…')} />
      ) : snapshot.isError ? (
        <div className="journey-incomplete">
          <p className="inline-warning">
            {text(
              'Task state is incomplete. Next actions cannot be confirmed until all workspace snapshots load.',
              '任务状态不完整。工作区快照全部加载前，无法确认下一步操作。',
            )}
          </p>
          <ErrorBox error={snapshot.error} onRetry={() => void snapshot.refetch()} />
        </div>
      ) : (
        <>
          <ol className="journey-list">{(showAll ? items : items.slice(0, 6)).map(renderItem)}</ol>
          {items.length > 6 && (
            <button
              className="text-button journey-expand"
              onClick={() => setShowAll((value) => !value)}
            >
              {showAll
                ? text('Show fewer tasks', '收起任务')
                : text(`Show all ${items.length} tasks`, `查看全部 ${items.length} 项任务`)}
            </button>
          )}
          {!!failures.length && (
            <details className="journey-history">
              <summary>
                {text('Recent failed research & data preparation', '近期失败的研究与数据准备')} ·{' '}
                {failures.length}
              </summary>
              <ol className="journey-list">
                {(showFailures ? failures : failures.slice(0, 2)).map(renderItem)}
              </ol>
              {failures.length > 2 && (
                <button
                  className="text-button journey-expand"
                  onClick={() => setShowFailures((value) => !value)}
                >
                  {showFailures
                    ? text('Show fewer failed tasks', '收起失败任务')
                    : text(
                        `Show all ${failures.length} failed tasks`,
                        `查看全部 ${failures.length} 项失败任务`,
                      )}
                </button>
              )}
            </details>
          )}
          {data?.runs.next_cursor && (
            <p className="journey-note">
              {text(
                'This is a recent research list. Older runs may not be shown.',
                '此处为近期研究列表，较早的任务可能未列出。',
              )}{' '}
              <button className="text-button" onClick={() => navigate('research')}>
                {text('Open research history', '打开研究历史')}
              </button>
            </p>
          )}
        </>
      )}
      {snapshot.isSuccess && (
        <p className="journey-note">
          {text(
            'Suggested actions reflect stored task states. Completed research does not establish paper eligibility or clear trading risk.',
            '建议操作来自保存的任务状态。研究完成不代表具备模拟发布资格，也不代表交易风险已解除。',
          )}
        </p>
      )}
    </section>
  );
}
