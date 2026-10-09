import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Source } from '../api';
import { proApi } from '../proApi';
import { useI18n } from '../lib/i18n';
import { canManageRisk } from '../lib/permissions';
import { useSession } from './AuthGate';
import { ErrorBox, Field, Loading } from './workspace';

export default function AccountCapitalPolicy({
  source,
  onSaved,
}: {
  source: Source;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const allowed = canManageRisk(useSession()?.user?.role);
  const qc = useQueryClient();
  const policy = useQuery({
    queryKey: ['capital-policy', source],
    queryFn: () => proApi.capitalPolicy(source),
  });
  const [baseGross, setBaseGross] = useState('100');
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (policy.data && !dirty) setBaseGross(policy.data.max_base_asset_gross_pct);
  }, [policy.data, dirty]);
  const save = useMutation({
    mutationFn: proApi.saveCapitalPolicy,
    onSuccess: (updated) => {
      setDirty(false);
      qc.setQueryData(['capital-policy', source], updated);
      for (const key of ['pro-ops', 'managed-portfolios', 'pro-account', 'portfolio-analytics'])
        void qc.invalidateQueries({ queryKey: [key] });
      onSaved();
    },
  });
  return (
    <div className="account-capital-control">
      <h3>{t('Account capital policy')}</h3>
      <p className="quiet-copy">
        {t(
          'All managed groups share a 100% declared capital budget. Spot and perpetual exposure to the same underlying consume a common gross limit without direction netting.',
        )}
      </p>
      {policy.isPending ? (
        <Loading />
      ) : policy.isError ? (
        <ErrorBox error={policy.error} onRetry={() => void policy.refetch()} />
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate({ source, max_base_asset_gross_pct: baseGross });
          }}
        >
          <Field label="Underlying asset gross limit (%)">
            <input
              required
              type="number"
              min={1}
              max={1000}
              step="any"
              value={baseGross}
              onChange={(event) => {
                setDirty(true);
                setBaseGross(event.target.value);
              }}
            />
          </Field>
          <button className="button button-secondary" disabled={!allowed || save.isPending}>
            {t('Save capital policy')}
          </button>
          {save.isError && <ErrorBox error={save.error} />}
        </form>
      )}
    </div>
  );
}
