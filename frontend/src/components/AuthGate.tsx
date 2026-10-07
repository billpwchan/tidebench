import { createContext, useContext, useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Loader2, LockKeyhole, RefreshCw } from 'lucide-react';
import { proApi } from '../proApi';
import type { AuthStatus, AuthUser } from '../proApi';
import { useI18n } from '../lib/i18n';
import { ActionNote, ErrorBox, Field, Logo, Loading } from './workspace';
const AuthContext = createContext<AuthStatus | null>(null);
export const useSession = () => useContext(AuthContext);
export function LanguageSelect() {
  const { language, setLanguage, t } = useI18n();
  return (
    <select
      className="language-select"
      aria-label={t('Interface language')}
      value={language}
      onChange={(e) => setLanguage(e.target.value as 'en' | 'zh-CN')}
    >
      <option value="en">English</option>
      <option value="zh-CN">中文</option>
    </select>
  );
}
export default function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ['auth-status'],
    queryFn: proApi.authStatus,
    retry: 1,
    staleTime: 30000,
    refetchInterval: 60000,
    refetchOnWindowFocus: true,
  });
  useEffect(() => {
    const refresh = () => void qc.invalidateQueries({ queryKey: ['auth-status'] });
    window.addEventListener('tidebench:auth-required', refresh);
    return () => window.removeEventListener('tidebench:auth-required', refresh);
  }, [qc]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [bootstrap, setBootstrap] = useState('');
  const auth = useMutation({
    mutationFn: () =>
      status.data?.setup_required
        ? proApi.setup({ username, password, display_name: displayName }, bootstrap)
        : proApi.login({ username, password }),
    onSuccess: async () => {
      setPassword('');
      await qc.invalidateQueries({ queryKey: ['auth-status'] });
      await qc.invalidateQueries();
    },
  });
  if (
    status.data &&
    !status.data.setup_required &&
    (!status.data.auth_required || status.data.authenticated)
  )
    return <AuthContext.Provider value={status.data}>{children}</AuthContext.Provider>;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    auth.mutate();
  };
  return (
    <div className="auth-shell">
      <header>
        <a className="brand" href="#overview">
          <Logo />
          <span>tidebench.</span>
        </a>
        <LanguageSelect />
      </header>
      <main className="auth-main">
        <div className="auth-mark">
          <LockKeyhole size={24} />
        </div>
        <div className="eyebrow">TIDEBENCH WORKSPACE</div>
        <h1>
          {t(
            status.isError
              ? 'Authentication service unavailable'
              : status.data?.setup_required
                ? 'Initial setup'
                : 'Sign in',
          )}
        </h1>
        <p>
          {t(
            status.data?.setup_required
              ? 'Create the first administrator before opening the workspace.'
              : 'Sign in to access research, execution, and operations.',
          )}
        </p>
        {status.isPending ? (
          <div className="auth-loading">
            <Loader2 className="spin" size={18} />
            {t('Loading workspace data…')}
          </div>
        ) : status.isError ? (
          <>
            <ErrorBox error={status.error} />
            <button className="button button-dark full-width" onClick={() => void status.refetch()}>
              <RefreshCw size={14} />
              {t('Retry')}
            </button>
          </>
        ) : (
          <form onSubmit={submit}>
            {status.data.setup_required && (
              <Field label="Display name">
                <input
                  autoComplete="name"
                  required
                  maxLength={80}
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
              </Field>
            )}
            <Field label="Username">
              <input
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                minLength={3}
                maxLength={40}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </Field>
            <Field
              label="Password"
              hint={
                status.data.setup_required
                  ? 'Password must contain at least 12 characters.'
                  : undefined
              }
            >
              <input
                type="password"
                autoComplete={status.data.setup_required ? 'new-password' : 'current-password'}
                maxLength={128}
                minLength={status.data.setup_required ? 12 : 1}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            {status.data.setup_required && (
              <Field
                label="Bootstrap token"
                hint="Required for initial setup from a remote client. Optional on localhost."
              >
                <input
                  type="password"
                  autoComplete="off"
                  value={bootstrap}
                  onChange={(e) => setBootstrap(e.target.value)}
                />
              </Field>
            )}
            {auth.isError && <ErrorBox error={auth.error} />}
            <button
              type="submit"
              className="button button-citrus full-width"
              disabled={auth.isPending}
            >
              {auth.isPending ? <Loader2 size={15} className="spin" /> : <ArrowRight size={15} />}{' '}
              {t(status.data.setup_required ? 'Create administrator' : 'Sign in')}
            </button>
          </form>
        )}
      </main>
      <footer>Self-hosted crypto research & execution</footer>
    </div>
  );
}
export function SessionPanel() {
  const { t } = useI18n();
  const session = useSession();
  const qc = useQueryClient();
  const logout = useMutation({
    mutationFn: proApi.logout,
    onSuccess: () => {
      qc.clear();
      window.location.reload();
    },
  });
  return (
    <section className="pro-panel">
      <div className="section-heading">
        <h2>{t('Session')}</h2>
        <LanguageSelect />
      </div>
      <div className="session-summary">
        <span>
          {t(
            session?.authenticated ? 'Session authenticated' : 'Server authentication is disabled',
          )}
        </span>
        {session?.user && (
          <strong>
            {session.user.display_name ?? session.user.username} <small>{session.user.role}</small>
          </strong>
        )}
      </div>
      {session?.authenticated && (
        <button
          className="button button-secondary"
          disabled={logout.isPending}
          onClick={() => logout.mutate()}
        >
          {t('Sign out')}
        </button>
      )}
      {logout.isError && <ErrorBox error={logout.error} />}
      {session?.auth_required && session.user?.id !== 'service' && <PasswordChange />}
    </section>
  );
}
export function UserManagement() {
  const session = useSession();
  const { t } = useI18n();
  const qc = useQueryClient();
  const admin = session?.user?.role === 'admin';
  const users = useQuery({ queryKey: ['auth-users'], queryFn: proApi.users, enabled: admin });
  const [form, setForm] = useState({
    username: '',
    password: '',
    display_name: '',
    role: 'viewer',
  });
  const create = useMutation({
    mutationFn: proApi.createUser,
    onSuccess: () => {
      setForm({ username: '', password: '', display_name: '', role: 'viewer' });
      void qc.invalidateQueries({ queryKey: ['auth-users'] });
    },
  });
  if (!admin) return null;
  return (
    <section className="pro-panel">
      <div className="section-heading">
        <h2>{t('Users')}</h2>
      </div>
      {users.isPending ? (
        <Loading />
      ) : users.isError ? (
        <ErrorBox error={users.error} />
      ) : (
        <div className="user-list">
          {users.data?.items.map((u) => (
            <UserAccessRow key={u.id ?? u.username} user={u} />
          ))}
        </div>
      )}
      <form
        className="compact-form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(form);
        }}
      >
        <div className="form-grid">
          <Field label="Username">
            <input
              required
              autoComplete="off"
              minLength={3}
              maxLength={40}
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          </Field>
          <Field label="Display name">
            <input
              required
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            />
          </Field>
          <Field label="Password">
            <input
              required
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </Field>
          <Field label="Role">
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="viewer">{t('Viewer')}</option>
              <option value="trader">{t('Trader')}</option>
              <option value="researcher">{t('Researcher')}</option>
              <option value="risk_operator">{t('Risk operator')}</option>
              <option value="admin">{t('Administrator')}</option>
            </select>
          </Field>
        </div>
        {create.isError && <ErrorBox error={create.error} />}
        <button className="button button-dark" disabled={create.isPending}>
          {t('Create user')}
        </button>
      </form>
    </section>
  );
}

function PasswordChange() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState<string | null>(null);
  const change = useMutation({
    mutationFn: proApi.changePassword,
    onSuccess: () => {
      setCurrent('');
      setPassword('');
      setConfirmation('');
      qc.clear();
      window.location.reload();
    },
  });
  return (
    <details className="password-change">
      <summary>{t('Change password')}</summary>
      <p>
        {t('Changing your password signs out all sessions. Sign in again with the new password.')}
      </p>
      <form
        className="compact-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (password !== confirmation) {
            setError(t('Passwords do not match.'));
            return;
          }
          setError(null);
          change.mutate({ current_password: current, new_password: password });
        }}
      >
        <Field label="Current password">
          <input
            required
            type="password"
            autoComplete="current-password"
            maxLength={128}
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </Field>
        <div className="form-grid">
          <Field label="New password">
            <input
              required
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Field label="Confirm new password">
            <input
              required
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </Field>
        </div>
        {(error || change.isError) && <ErrorBox error={error ? new Error(error) : change.error} />}
        <button className="button button-secondary" disabled={change.isPending}>
          {change.isPending && <Loader2 size={13} className="spin" />}
          {t('Change password')}
        </button>
      </form>
    </details>
  );
}
const userEnabled = (user: AuthUser) => user.enabled === undefined || Boolean(user.enabled);
function UserAccessRow({ user }: { user: AuthUser }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [role, setRole] = useState(user.role);
  const [enabled, setEnabled] = useState(userEnabled(user));
  const [dirty, setDirty] = useState(false);
  const [password, setPassword] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['auth-users'] });
    void qc.invalidateQueries({ queryKey: ['auth-status'] });
  };
  const update = useMutation({
    mutationFn: proApi.updateUser,
    onSuccess: (updated) => {
      qc.setQueryData<{ items: AuthUser[] }>(['auth-users'], (data) =>
        data
          ? { items: data.items.map((item) => (item.id === updated.id ? updated : item)) }
          : data,
      );
      setRole(updated.role);
      setEnabled(userEnabled(updated));
      setDirty(false);
      setNotice(t('User access updated. Existing sessions were revoked.'));
      refresh();
    },
  });
  const reset = useMutation({
    mutationFn: proApi.resetPassword,
    onSuccess: () => {
      setPassword('');
      setNotice(t('Password reset. Existing sessions were revoked.'));
      refresh();
    },
  });
  useEffect(() => {
    if (!dirty) {
      setRole(user.role);
      setEnabled(userEnabled(user));
    }
  }, [user.role, user.enabled, dirty]);
  return (
    <div className="user-access-row">
      <div className="user-identity">
        <strong>{user.display_name ?? user.username}</strong>
        <span>{user.username}</span>
      </div>
      <form
        className="user-access-controls"
        onSubmit={(e) => {
          e.preventDefault();
          if (user.id) update.mutate({ id: user.id, role, enabled });
        }}
      >
        <select
          aria-label={`${t('Role')} ${user.username}`}
          value={role}
          disabled={update.isPending}
          onChange={(e) => {
            setDirty(true);
            setRole(e.target.value);
          }}
        >
          <option value="admin">{t('Administrator')}</option>
          <option value="trader">{t('Trader')}</option>
          <option value="researcher">{t('Researcher')}</option>
          <option value="risk_operator">{t('Risk operator')}</option>
          <option value="viewer">{t('Viewer')}</option>
        </select>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={enabled}
            disabled={update.isPending}
            onChange={(e) => {
              setDirty(true);
              setEnabled(e.target.checked);
            }}
          />
          <span>{t('Enabled')}</span>
        </label>
        <button
          className="button button-small button-secondary"
          aria-label={`${t('Save user')} ${user.username}`}
          disabled={
            update.isPending || !user.id || (role === user.role && enabled === userEnabled(user))
          }
        >
          {t('Save')}
        </button>
      </form>
      <details className="admin-password-reset">
        <summary>{t('Reset password')}</summary>
        <p>{t('Resetting a password signs this user out of all sessions.')}</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (user.id) reset.mutate({ id: user.id, new_password: password });
          }}
        >
          <Field label="New password">
            <input
              aria-label={`${t('New password')} ${user.username}`}
              required
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <button
            className="button button-small button-secondary"
            disabled={reset.isPending || !user.id}
          >
            {t('Reset password')}
          </button>
        </form>
      </details>
      <ActionNote text={notice} />
      {update.isError && <ErrorBox error={update.error} />}
      {reset.isError && <ErrorBox error={reset.error} />}
    </div>
  );
}
