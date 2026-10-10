import { useI18n } from '../lib/i18n';

export default function ResearchCancellationStatus({
  requested,
  recovery,
  onRetry,
  disabled,
}: {
  requested: boolean;
  recovery?: unknown;
  onRetry: () => void;
  disabled: boolean;
}) {
  const { t } = useI18n();
  if (!requested) return null;
  const record =
    recovery && typeof recovery === 'object' && !Array.isArray(recovery)
      ? (recovery as Record<string, unknown>)
      : undefined;
  const blocked = record?.state === 'blocked';
  return blocked ? (
    <div className="research-cancellation-recovery" role="status">
      <strong>{t('Cancellation needs exit evidence')}</strong>
      <p>
        {t(
          "Cancellation is recorded, but the previous worker's exit cannot yet be verified. This run will not restart or publish a result.",
        )}
      </p>
      <p className="quiet-copy">
        {t(
          'Retry checks the same cancellation; it does not start a new study. If evidence remains unavailable, keep this attempt and start a replacement study.',
        )}
      </p>
      <button className="button button-secondary" disabled={disabled} onClick={onRetry}>
        {t('Recheck cancellation')}
      </button>
    </div>
  ) : (
    <div className="action-note" role="status">
      {t(
        'Cancellation requested. The worker is still stopping; this run remains active until cleanup completes.',
      )}
    </div>
  );
}
