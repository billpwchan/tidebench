import { useEffect, useRef, useState } from 'react';
import type { Strategy } from '../api';
import { useI18n } from '../lib/i18n';
import { Field } from './workspace';

const example = [
  {
    tag: 'trend-long',
    conditions: [
      { left: { feature: 'fast_sma' }, op: 'gt', right: { feature: 'slow_sma' } },
      { left: { feature: 'volume' }, op: 'gt', right: { constant: '0' } },
    ],
    signal: 1,
  },
  {
    tag: 'trend-exit',
    conditions: [{ left: { feature: 'fast_sma' }, op: 'le', right: { feature: 'slow_sma' } }],
    signal: 0,
  },
];
export default function ProgramEditor({
  value,
  onChange,
}: {
  value: Strategy;
  onChange: (s: Strategy) => void;
}) {
  const { t } = useI18n();
  const sent = useRef(JSON.stringify(value.rules ?? []));
  const [draft, setDraft] = useState(JSON.stringify(value.rules ?? [], null, 2));
  const [error, setError] = useState(false);
  useEffect(() => {
    const serialized = JSON.stringify(value.rules ?? []);
    if (serialized !== sent.current) {
      sent.current = serialized;
      setDraft(JSON.stringify(value.rules, null, 2));
      setError(false);
    }
  }, [value.rules]);
  return (
    <>
      <Field
        label="Ordered signal rules"
        hint="First matching rule wins. Unavailable indicators never match. No matching rule retains the position."
      >
        <textarea
          className="program-editor"
          rows={12}
          value={draft}
          spellCheck={false}
          aria-invalid={error}
          onChange={(e) => {
            setDraft(e.target.value);
            try {
              const rules: unknown = JSON.parse(e.target.value);
              if (!Array.isArray(rules) || !rules.length) throw new Error();
              sent.current = JSON.stringify(rules);
              onChange({ ...value, rules });
              setError(false);
            } catch {
              sent.current = '[]';
              onChange({ ...value, rules: [] });
              setError(true);
            }
          }}
        />
      </Field>
      {error && (
        <p role="alert" className="inline-warning">
          {t('Enter a nonempty JSON rule array. Saving or running an invalid program is blocked.')}
        </p>
      )}
      <button
        type="button"
        className="text-button"
        onClick={() => {
          onChange({ ...value, rules: example });
        }}
      >
        {t('Load trend filter example')}
      </button>
      <p className="quiet-copy">
        {t(
          'Features: close, volume, fast_sma, slow_sma, rsi, atr, zscore, channel_upper, channel_lower. Operators: gt, ge, lt, le. Signals: 1 long, −1 short, 0 flat. Maximum four rules and four conditions per rule.',
        )}
      </p>
    </>
  );
}
