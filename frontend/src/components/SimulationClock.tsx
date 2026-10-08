import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pause, Play, StepForward } from 'lucide-react';
import { proApi } from '../proApi';
import { date } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';
import { useSession } from './AuthGate';
import { ErrorBox } from './workspace';

export default function SimulationClock() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canTrade(useSession()?.user?.role);
  const clock = useQuery({
    queryKey: ['simulation-clock'],
    queryFn: proApi.clock,
    refetchInterval: 5000,
  });
  const change = useMutation({
    mutationFn: (body: { speed?: number; step_ms?: number }) =>
      proApi.changeClock({ ...body, expected_revision: clock.data!.revision }),
    onSuccess: (data) => qc.setQueryData(['simulation-clock'], data),
    onSettled: async () => {
      await qc.invalidateQueries({ queryKey: ['simulation-clock'] });
    },
  });
  const disabled = !canOperate || !clock.data || change.isPending || clock.isFetching;
  return (
    <div className="simulation-clock">
      <div>
        <strong>{t('Synthetic market clock')}</strong>
        <span>{clock.data ? date(clock.data.market_ts) : '—'} UTC</span>
      </div>
      <div className="toolbar">
        <span className="quiet-copy">
          {clock.data?.paused ? t('Paused') : `${clock.data?.speed}×`}
        </span>
        <button
          className="text-button"
          disabled={disabled}
          onClick={() => change.mutate({ speed: clock.data!.paused ? 60 : 0 })}
        >
          {clock.data?.paused ? <Play size={13} /> : <Pause size={13} />}{' '}
          {t(clock.data?.paused ? 'Play 60×' : 'Pause')}
        </button>
        <button
          className="text-button"
          disabled={disabled || !clock.data?.paused}
          onClick={() => change.mutate({ step_ms: 3_600_000 })}
        >
          <StepForward size={13} />
          {t('Step 1 hour')}
        </button>
      </div>
      {clock.isError && <ErrorBox error={clock.error} />}
      {change.isError && <ErrorBox error={change.error} />}
    </div>
  );
}
