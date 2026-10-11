import { useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, Keyboard, LayoutDashboard, Save, Trash2, X } from 'lucide-react';
import { useWorkspacePreferences, type DisplayLayout } from './WorkspacePreferences';
import type { WorkspaceLocation } from '../lib/workspaceLocation';
import type { Page } from '../lib/config';
import { useI18n } from '../lib/i18n';
import { useDialogFocus } from '../lib/hooks';
import { useSession } from './AuthGate';
import { canTrade } from '../lib/permissions';

export function DisplayPreferenceFields() {
  const p = useWorkspacePreferences();
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  return (
    <div className="workspace-preference-fields">
      <label>
        {text('Information density', '信息密度')}
        <select
          aria-label={text('Information density', '信息密度')}
          value={p.density}
          onChange={(e) => p.update({ density: e.target.value as DisplayLayout['density'] })}
        >
          <option value="comfortable">{text('Comfortable', '舒适')}</option>
          <option value="compact">{text('Compact', '紧凑')}</option>
        </select>
      </label>
      <label>
        {text('Monitor layout', '监控布局')}
        <select
          aria-label={text('Monitor layout', '监控布局')}
          value={p.monitorLayout}
          onChange={(e) =>
            p.update({ monitorLayout: e.target.value as DisplayLayout['monitorLayout'] })
          }
        >
          <option value="balanced">{text('Book and activity', '账簿与工作动态')}</option>
          <option value="focus">{text('Focus on account', '账户优先')}</option>
        </select>
      </label>
      <label>
        {text('Order ticket width', '订单票据宽度')}
        <select
          aria-label={text('Order ticket width', '订单票据宽度')}
          value={p.ticketWidth}
          onChange={(e) =>
            p.update({ ticketWidth: e.target.value as DisplayLayout['ticketWidth'] })
          }
        >
          <option value="standard">{text('Standard', '标准')}</option>
          <option value="wide">{text('Wide · larger desktops', '宽 · 大屏桌面')}</option>
        </select>
      </label>
      <button
        className="text-button"
        onClick={() =>
          p.update({ density: 'comfortable', monitorLayout: 'balanced', ticketWidth: 'standard' })
        }
      >
        {text('Restore default layout', '恢复默认布局')}
      </button>
      {p.storageError && (
        <p className="inline-warning" role="status">
          {text(
            p.storageError,
            '无法恢复或保存显示偏好；当前会话仍可使用，刷新后可能恢复默认布局。',
          )}
        </p>
      )}
    </div>
  );
}

export default function WorkspaceControls({
  location,
  onRestore,
  navigate,
  searchMarkets,
  blocked = false,
}: {
  location: WorkspaceLocation;
  onRestore: (l: WorkspaceLocation) => void;
  navigate: (page: Page, view?: string) => void;
  searchMarkets: () => void;
  blocked?: boolean;
}) {
  const p = useWorkspacePreferences();
  const { language, t } = useI18n();
  const session = useSession();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const [open, setOpen] = useState<'layout' | 'commands' | null>(null);
  const [name, setName] = useState('');
  const [notice, setNotice] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const listId = useId();
  useEffect(() => {
    if (!open) return;
    const shell = document.querySelector<HTMLElement>('.app-shell');
    const alreadyInert = shell?.inert;
    if (shell) shell.inert = true;
    return () => {
      if (shell && !alreadyInert) shell.inert = false;
    };
  }, [open]);
  useDialogFocus(!!open, '.workspace-tools-dialog', () => setOpen(null));
  useEffect(() => {
    if (open === 'commands')
      document
        .querySelector<HTMLInputElement>('.workspace-tools-dialog input[role="combobox"]')
        ?.focus();
  }, [open]);
  useEffect(() => {
    setOpen(null);
    setNotice('');
    setName('');
  }, [p.scopeKey]);
  const commands = [
    {
      id: 'monitor',
      label: text('Monitor account and risk', '查看账户与风险'),
      run: () => navigate('overview'),
    },
    {
      id: 'research',
      label: text('Inspect research evidence', '查看研究依据'),
      run: () => navigate('research'),
    },
    {
      id: 'orders',
      label: text('Inspect working orders', '查看当前挂单'),
      run: () => navigate('execution', 'orders'),
    },
    ...(canTrade(session?.user?.role)
      ? [
          {
            id: 'draft',
            label: text('Prepare a paper order', '准备模拟订单'),
            run: () => navigate('execution', 'order'),
          },
        ]
      : []),
    { id: 'markets', label: text('Search markets', '搜索市场'), run: searchMarkets },
    {
      id: 'library',
      label: text('Strategy hypotheses and versions', '策略假设与版本'),
      run: () => navigate('strategies'),
    },
    {
      id: 'data',
      label: text('Prepare research data', '准备研究数据'),
      run: () => navigate('data', 'packages'),
    },
    {
      id: 'ops',
      label: text('Inspect incidents and service', '查看事件与服务'),
      run: () => navigate('operations', 'incidents'),
    },
    {
      id: 'settings',
      label: text('Workspace preferences', '工作台偏好'),
      run: () => navigate('settings'),
    },
  ];
  const filtered = commands.filter((c) =>
    `${c.id} ${c.label}`.toLowerCase().includes(query.toLowerCase().trim()),
  );
  const activeIndex = Math.min(selected, Math.max(0, filtered.length - 1));
  const runCommand = (index: number) => {
    const command = filtered[index];
    if (!command) return;
    setOpen(null);
    command.run();
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (
        blocked ||
        event.defaultPrevented ||
        event.isComposing ||
        event.repeat ||
        target?.closest(
          'input,textarea,select,[contenteditable="true"],[role="slider"],canvas,[role="dialog"]',
        ) ||
        document.querySelector('[role="dialog"]')
      )
        return;
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setQuery('');
        setSelected(0);
        setOpen('commands');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [blocked]);
  return (
    <>
      <button
        className="workspace-layout-trigger icon-button"
        aria-label={text('Workspace layout and commands', '工作台布局与命令')}
        title={text('Workspace layout and commands', '工作台布局与命令')}
        onClick={() => {
          setNotice('');
          setOpen('layout');
        }}
      >
        <LayoutDashboard size={17} />
      </button>
      {open &&
        createPortal(
          <div className="dialog-backdrop workspace-tools-backdrop">
            <section
              className="workspace-tools-dialog"
              role="dialog"
              aria-modal="true"
              aria-label={
                open === 'layout'
                  ? text('Workspace layout', '工作台布局')
                  : text('Workspace commands', '工作台命令')
              }
            >
              <div className="section-heading">
                <h2>
                  {open === 'layout'
                    ? text('Your workspace', '你的工作台')
                    : text('Workspace commands', '工作台命令')}
                </h2>
                <button
                  className="icon-button"
                  aria-label={t('Close')}
                  onClick={() => setOpen(null)}
                >
                  <X size={18} />
                </button>
              </div>
              {open === 'layout' ? (
                <>
                  <p className="section-description">
                    {text(
                      'Display preferences are scoped to your user and current data source.',
                      '显示偏好按用户和当前数据来源隔离。',
                    )}
                  </p>
                  <DisplayPreferenceFields />
                  <section className="saved-workspaces">
                    <h3>{text('Saved workspaces', '已保存工作台')}</h3>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        p.saveWorkspace(name, location);
                        setNotice(
                          text(
                            'View and layout saved. Restoring rechecks current data and permissions.',
                            '视图与布局已保存；恢复时会重新检查当前数据和权限。',
                          ),
                        );
                        setName('');
                      }}
                    >
                      <label className="sr-only" htmlFor={`${listId}-name`}>
                        {text('Workspace name', '工作台名称')}
                      </label>
                      <input
                        id={`${listId}-name`}
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        maxLength={40}
                        required
                        placeholder={text('Name this view', '为当前视图命名')}
                      />
                      <button className="button button-secondary" disabled={!name.trim()}>
                        <Save size={14} />
                        {t('Save')}
                      </button>
                    </form>
                    {notice && (
                      <p role="status" className="form-footnote">
                        {notice}
                      </p>
                    )}
                    {p.workspaces.length === 0 ? (
                      <p className="form-footnote">
                        {text(
                          'Save a research view or monitoring layout for your next session.',
                          '保存研究视图或监控布局，方便下一次工作。',
                        )}
                      </p>
                    ) : (
                      <ul>
                        {p.workspaces.map((w) => (
                          <li key={w.id}>
                            <button
                              className="saved-workspace-open"
                              onClick={() => {
                                p.restoreLayout(w.layout);
                                setOpen(null);
                                onRestore(w.location);
                              }}
                            >
                              <strong>{w.name}</strong>
                              <span>
                                {t(
                                  w.location.page === 'overview'
                                    ? 'Overview'
                                    : w.location.page === 'research'
                                      ? 'Research'
                                      : w.location.page === 'execution'
                                        ? 'Execution'
                                        : w.location.page,
                                )}{' '}
                                · {w.location.symbol ?? location.source}
                              </span>
                              <ArrowRight size={15} />
                            </button>
                            <button
                              className="icon-button"
                              aria-label={`${text('Remove saved workspace', '移除已保存工作台')} ${w.name}`}
                              onClick={() => p.removeWorkspace(w.id)}
                            >
                              <Trash2 size={14} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                  <button
                    className="button button-secondary full-width"
                    onClick={() => {
                      setQuery('');
                      setSelected(0);
                      setOpen('commands');
                    }}
                  >
                    <Keyboard size={16} />
                    {text('Open commands', '打开命令')}
                    <kbd>⌘ / Ctrl ⇧ K</kbd>
                  </button>
                </>
              ) : (
                <>
                  <input
                    autoComplete="off"
                    role="combobox"
                    aria-label={text('Find a workspace command', '搜索工作台命令')}
                    aria-controls={listId}
                    aria-expanded="true"
                    aria-activedescendant={filtered.length ? `${listId}-${activeIndex}` : undefined}
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setSelected(0);
                    }}
                    placeholder={text('Find a task…', '查找任务…')}
                    onKeyDown={(e) => {
                      if (e.nativeEvent.isComposing) return;
                      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                        e.preventDefault();
                        setSelected(
                          (activeIndex + (e.key === 'ArrowDown' ? 1 : -1) + filtered.length) %
                            Math.max(1, filtered.length),
                        );
                      }
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        runCommand(activeIndex);
                      }
                    }}
                  />
                  <div
                    id={listId}
                    role="listbox"
                    aria-label={text('Available commands', '可用命令')}
                    className="workspace-command-list"
                  >
                    {filtered.map((c, i) => (
                      <button
                        id={`${listId}-${i}`}
                        key={c.id}
                        role="option"
                        aria-selected={i === activeIndex}
                        onClick={() => runCommand(i)}
                      >
                        {c.label}
                        <ArrowRight size={15} />
                      </button>
                    ))}
                  </div>
                  {!filtered.length && (
                    <p role="status">{text('No matching command.', '没有匹配的命令。')}</p>
                  )}
                  <p className="form-footnote">
                    {text(
                      'Commands open views or drafts. Trading and release actions keep their existing review and confirmation.',
                      '命令用于打开视图或草稿；交易与发布操作仍需原有审查和确认。',
                    )}
                  </p>
                </>
              )}
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
