import { useQueryClient } from '@tanstack/react-query';
import { Check, CircleAlert, Database, FlaskConical, ShieldCheck } from 'lucide-react';
import type { FormEvent } from 'react';
import { useState } from 'react';
import type { Source, System } from '../api';
import { readToken, saveToken } from '../api';
import { ActionNote, Field, Logo, PageHeading, Status } from '../components/workspace';
import { date } from '../lib/format';

export default function Settings({
  system,
  source,
  setSource,
}: {
  system?: System;
  source: Source;
  setSource: (s: Source) => void;
}) {
  const qc = useQueryClient();
  const [token, setToken] = useState(readToken);
  const [notice, setNotice] = useState<string | null>(null);
  const [showToken, setShowToken] = useState(false);
  const save = (e: FormEvent) => {
    e.preventDefault();
    saveToken(token);
    setNotice(
      token.trim()
        ? 'Session token saved. Protected workspace data is being refreshed.'
        : 'Session token removed.',
    );
    void qc.resetQueries();
  };
  return (
    <>
      <PageHeading
        eyebrow="YOUR INFRASTRUCTURE, YOUR RULES"
        title="Workspace settings"
        description="Connections and preferences for your self-hosted research desk."
      />
      <ActionNote text={notice} />
      <div className="settings-layout">
        <section className="settings-section">
          <div className="section-heading">
            <div>
              <h2>Market data</h2>
              <p className="section-description">
                Choose which data powers this browser's workspace.
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
                <strong>OKX public markets</strong>
                <p>Real public spot data over REST. No exchange credentials required.</p>
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
                <strong>Synthetic example</strong>
                <p>A deterministic dataset for exploring the product. Not real OKX prices.</p>
              </div>
              <span className="radio-indicator">{source === 'example' && <span />}</span>
            </button>
          </div>
          <p className="form-footnote">
            Each source has an independent paper account. Source selection is stored locally in your
            browser. Errors never switch your source automatically.
          </p>
        </section>
        <section className="settings-section">
          <div className="section-heading">
            <div>
              <h2>Session authentication</h2>
              <p className="section-description">
                Access your protected API when server authentication is enabled.
              </p>
            </div>
            <ShieldCheck size={20} className="muted-icon" />
          </div>
          <Status type={system?.auth_required ? 'warning' : 'neutral'}>
            {system
              ? system.auth_required
                ? 'Server requires a bearer token'
                : 'Server authentication is not configured'
              : 'Server status unavailable'}
          </Status>
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
                  {showToken ? 'Hide' : 'Show'}
                </button>
              </div>
            </Field>
            <div className="button-row">
              <button type="submit" className="button button-dark">
                <Check size={14} />
                Save session token
              </button>
              <button
                type="button"
                className="button button-secondary"
                onClick={() => {
                  setToken('');
                  saveToken('');
                  setNotice('Session token removed.');
                  void qc.resetQueries();
                }}
              >
                Clear token
              </button>
            </div>
          </form>
          <p className="form-footnote">
            Use the Tidebench server token here. Exchange API keys and passphrases are not accepted
            or required.
          </p>
        </section>
        <section className="settings-section server-settings">
          <div className="section-heading">
            <div>
              <h2>Server configuration</h2>
              <p className="section-description">Read-only values from your running backend.</p>
            </div>
            <span className="subtle-tag">READ ONLY</span>
          </div>
          <dl>
            <div>
              <dt>Application</dt>
              <dd>Tidebench {system ? `v${system.version}` : '—'}</dd>
            </div>
            <div>
              <dt>Market region</dt>
              <dd>{system?.market_region ?? '—'}</dd>
            </div>
            <div>
              <dt>Execution engine</dt>
              <dd>{system?.execution ?? '—'}</dd>
            </div>
            <div>
              <dt>Server time</dt>
              <dd>{date(system?.time, true)}</dd>
            </div>
          </dl>
          <div className="server-capabilities">
            <span className="eyebrow">REPORTED CAPABILITIES</span>
            <div>
              {system?.capabilities.length ? (
                system.capabilities.map((c) => (
                  <span className="subtle-tag" key={c}>
                    {c.replaceAll('_', ' ')}
                  </span>
                ))
              ) : (
                <span className="quiet-copy">No capabilities reported.</span>
              )}
            </div>
          </div>
        </section>
        <section className="settings-section about-settings">
          <Logo />
          <h2>Evidence before execution.</h2>
          <p>
            Tidebench is an open-source crypto research and local paper-trading workspace. Inspect
            your assumptions, replay a saved dataset, and understand every simulated fill.
          </p>
          <div className="about-note">
            <CircleAlert size={17} />
            <span>
              This developer preview does not place real exchange orders. Backtests and synthetic
              data do not establish a profitable trading strategy.
            </span>
          </div>
        </section>
      </div>
    </>
  );
}
