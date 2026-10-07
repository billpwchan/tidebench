import { useQueryClient } from '@tanstack/react-query';
import { Check, CircleAlert, Database, FlaskConical, ShieldCheck } from 'lucide-react';
import type { FormEvent } from 'react';
import { useState } from 'react';
import type { Source, System } from '../api';
import { readToken, saveToken } from '../api';
import { ActionNote, Field, PageHeading, Status } from '../components/workspace';
import { date } from '../lib/format';
import { SessionPanel, UserManagement } from '../components/AuthGate';
import { useI18n } from '../lib/i18n';

export default function Settings({
  system,
  source,
  setSource,
}: {
  system?: System;
  source: Source;
  setSource: (s: Source) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [token, setToken] = useState(readToken);
  const [notice, setNotice] = useState<string | null>(null);
  const [showToken, setShowToken] = useState(false);
  const save = (e: FormEvent) => {
    e.preventDefault();
    saveToken(token);
    setNotice(
      token.trim()
        ? t('API token saved. Protected workspace data is being refreshed.')
        : t('API token removed.'),
    );
    void qc.resetQueries();
  };
  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE"
        title="Workspace settings"
        description="Connections and preferences for your self-hosted research desk."
      />
      <ActionNote text={notice} />
      <div className="settings-layout">
        <SessionPanel />
        <UserManagement />
        <section className="settings-section">
          <div className="section-heading">
            <div>
              <h2>{t('Market data')}</h2>
              <p className="section-description">
                {t('Choose which data powers this browser’s workspace.')}
              </p>
            </div>
            <Database size={20} className="muted-icon" />
          </div>
          <div className="source-options">
            <button
              className={source === 'okx' ? 'active' : ''}
              onClick={() => setSource('okx')}
              aria-pressed={source === 'okx'}
            >
              <span className="source-option-icon">OKX</span>
              <div>
                <strong>{t('OKX public markets')}</strong>
                <p>
                  {t(
                    'Public spot and USDT perpetual data over REST. No exchange credentials required.',
                  )}
                </p>
              </div>
              <span className="radio-indicator">{source === 'okx' && <span />}</span>
            </button>
            <button
              className={source === 'example' ? 'active' : ''}
              onClick={() => setSource('example')}
              aria-pressed={source === 'example'}
            >
              <span className="source-option-icon">
                <FlaskConical size={22} />
              </span>
              <div>
                <strong>{t('Synthetic example')}</strong>
                <p>{t('Deterministic synthetic market data, separate from OKX.')}</p>
              </div>
              <span className="radio-indicator">{source === 'example' && <span />}</span>
            </button>
          </div>
          <p className="form-footnote">
            {t(
              'Each source has an independent execution account. Source preference is stored in this browser.',
            )}
          </p>
        </section>
        <section className="settings-section">
          <div className="section-heading">
            <div>
              <h2>{t('API integration')}</h2>
              <p className="section-description">
                {t('Optional service token for API clients and managed deployments.')}
              </p>
            </div>
            <ShieldCheck size={20} className="muted-icon" />
          </div>
          <Status type="neutral">{t('Browser access uses your signed-in session.')}</Status>
          <details className="service-token-details">
            <summary>{t('API service token (advanced)')}</summary>
            <form className="auth-form" onSubmit={save}>
              <Field
                label="API bearer token"
                hint="Stored in sessionStorage for this tab session. Never sent in a URL."
              >
                <div className="token-input">
                  <input
                    type={showToken ? 'text' : 'password'}
                    autoComplete="off"
                    spellCheck={false}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="Paste your server token"
                  />
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => setShowToken((v) => !v)}
                  >
                    {t(showToken ? 'Hide' : 'Show')}
                  </button>
                </div>
              </Field>
              <div className="button-row">
                <button type="submit" className="button button-dark">
                  <Check size={14} />
                  {t('Save API token')}
                </button>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => {
                    setToken('');
                    saveToken('');
                    setNotice(t('API token removed.'));
                    void qc.resetQueries();
                  }}
                >
                  {t('Clear token')}
                </button>
              </div>
            </form>
            <p className="form-footnote">
              {t(
                'Use a Tidebench service token. Exchange API keys and passphrases are not used here.',
              )}
            </p>
          </details>
        </section>
        <section className="settings-section server-settings">
          <div className="section-heading">
            <div>
              <h2>{t('Server configuration')}</h2>
              <p className="section-description">
                {t('Read-only values from your running backend.')}
              </p>
            </div>
            <span className="subtle-tag">{t('Read only')}</span>
          </div>
          <dl>
            <div>
              <dt>{t('Application')}</dt>
              <dd>Tidebench {system ? `v${system.version}` : '—'}</dd>
            </div>
            <div>
              <dt>{t('Market region')}</dt>
              <dd>{system?.market_region ?? '—'}</dd>
            </div>
            <div>
              <dt>{t('Execution engine')}</dt>
              <dd>{system?.execution ?? '—'}</dd>
            </div>
            <div>
              <dt>{t('Server time')}</dt>
              <dd>{date(system?.time, true)}</dd>
            </div>
          </dl>
          <div className="server-capabilities">
            <span className="eyebrow">{t('Reported capabilities')}</span>
            <div>
              {system?.capabilities.length ? (
                system.capabilities.map((c) => (
                  <span className="subtle-tag" key={c}>
                    {c.replaceAll('_', ' ')}
                  </span>
                ))
              ) : (
                <span className="quiet-copy">{t('No capabilities reported.')}</span>
              )}
            </div>
          </div>
        </section>
        <section className="settings-section about-settings">
          <h2>{t('Research and execution model')}</h2>
          <p>
            {t(
              'Research runs capture their data, costs and instrument rules. Exports include the snapshot required for replay.',
            )}
          </p>
          <div className="about-note">
            <CircleAlert size={17} />
            <span>
              {t(
                'Execution currently uses the local paper engine. Real orders are not sent to an exchange.',
              )}
            </span>
          </div>
        </section>
      </div>
    </>
  );
}
