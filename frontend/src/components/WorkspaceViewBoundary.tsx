import { Component, type ReactNode } from 'react';
import { useI18n } from '../lib/i18n';

function ViewFailure() {
  const { t } = useI18n();
  return (
    <section className="action-note bad workspace-view-failure" role="alert">
      <h2>{t('This view could not be loaded')}</h2>
      <p>{t('Reload the workspace to try again, or choose another section.')}</p>
      <button className="button button-secondary" onClick={() => window.location.reload()}>
        {t('Reload workspace')}
      </button>
    </section>
  );
}

export default class WorkspaceViewBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? <ViewFailure /> : this.props.children;
  }
}
