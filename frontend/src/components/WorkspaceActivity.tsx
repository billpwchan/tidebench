import { useQuery } from '@tanstack/react-query';
import { ArrowRight, Loader2 } from 'lucide-react';
import type { Source } from '../api';
import type { Page } from '../lib/config';
import { useI18n } from '../lib/i18n';
import { proApi } from '../proApi';

export default function WorkspaceActivity({
  source,
  navigate,
}: {
  source: Source;
  navigate: (page: Page, view?: string) => void;
}) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const ops = useQuery({ queryKey: ['pro-ops'], queryFn: proApi.ops, refetchInterval: 5000 });
  const active =
    ops.data?.jobs?.filter(
      (job) =>
        job.source === source &&
        ['queued', 'running', 'preparing', 'cancelling'].includes(String(job.status)),
    ) ?? [];
  if (!active.length) return null;
  return (
    <div className="workspace-activity" role="status">
      <span>
        <Loader2 size={13} className="spin" />
        {active.length} {text('background tasks in this source', '项当前来源的后台任务')}
        {ops.isError
          ? text(' · last known state; refresh unavailable', ' · 最近状态；当前刷新失败')
          : ''}
      </span>
      <button className="text-button" onClick={() => navigate('operations', 'jobs')}>
        {text('Inspect tasks', '查看任务')}
        <ArrowRight size={13} />
      </button>
    </div>
  );
}
