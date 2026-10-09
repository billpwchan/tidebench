import type { Source } from '../api';
import type { ManagedPortfolio, OpsIncident, ProDeployment } from '../proApi';

export type DeskIssue = {
  id: string;
  group?: ManagedPortfolio;
  incident?: OpsIncident;
  name: string;
  phase: string;
  error: string;
  since?: number;
  asOf?: number;
  inventoryNotional?: string | null;
  valuationStatus?: string;
  inventory?: { inst_id: string; quantity: string; market_value: string | null }[];
  residualNotional?: string | null;
  scope: 'source' | 'workspace';
  severity: 'bad' | 'warning';
};

// Acknowledgement is ownership of a response, never recovery of the condition.
export function deskIssues(
  source: Source,
  groups: ManagedPortfolio[],
  incidents: OpsIncident[],
  deployments: ProDeployment[],
): DeskIssue[] {
  const byGroup = new Map(groups.map((group) => [group.id, group]));
  const byDeployment = new Map(deployments.map((deployment) => [deployment.id, deployment]));
  const active = incidents.filter((incident) => {
    if (incident.status === 'resolved') return false;
    const incidentSource =
      incident.details.source ??
      byGroup.get(incident.subject)?.source ??
      byDeployment.get(incident.subject)?.source ??
      (incident.subject.startsWith('okx:')
        ? 'okx'
        : incident.subject.startsWith('example:')
          ? 'example'
          : ['okx', 'example'].includes(incident.subject)
            ? incident.subject
            : undefined);
    return !incidentSource || incidentSource === source;
  });
  const issues: DeskIssue[] = [];
  const groupIncidentIds = new Set<string>();
  for (const group of groups) {
    const incident = active.find(
      (item) => item.kind === 'managed_portfolio' && item.subject === group.id,
    );
    const attention = group.attention;
    // Historical failures alone are not an active condition. A stopped group is
    // actionable only when the current server projection or durable monitor says so.
    const fallback =
      group.status === 'compensating' ||
      (group.status === 'running' && !!group.last_error) ||
      (group.status !== 'stopped' && !!group.integrity_error);
    if (!attention && !incident && !fallback) continue;
    if (incident) groupIncidentIds.add(incident.id);
    issues.push({
      id: `group:${group.id}`,
      group,
      incident,
      name: group.manifest?.name ?? group.id.slice(0, 8),
      phase: attention?.phase ?? (group.status === 'running' ? 'preparing' : group.status),
      error:
        attention?.error ??
        group.last_error ??
        group.integrity_error?.message ??
        String(incident?.details.error ?? ''),
      since: incident?.first_seen ?? attention?.since,
      asOf: attention?.as_of ?? incident?.last_seen,
      inventoryNotional: attention?.inventory_notional,
      valuationStatus: attention?.valuation_status,
      inventory: attention?.inventory,
      residualNotional: attention?.residual_notional,
      scope: 'source',
      severity:
        attention?.phase.includes('compensat') ||
        group.status === 'compensating' ||
        attention?.phase === 'integrity' ||
        group.integrity_error ||
        attention?.inventory.some((position) => Number(position.quantity) !== 0)
          ? 'bad'
          : 'warning',
    });
  }
  for (const incident of active) {
    if (groupIncidentIds.has(incident.id)) continue;
    issues.push({
      id: `incident:${incident.id}`,
      incident,
      name: byDeployment.get(incident.subject)?.inst_id ?? incident.subject,
      phase: incident.kind,
      error: String(incident.details.error ?? incident.details.message ?? ''),
      since: incident.first_seen,
      asOf: incident.last_seen,
      severity: ['contribution_quarantined', 'market_execution'].includes(incident.kind)
        ? 'bad'
        : 'warning',
      scope:
        incident.details.source ||
        byDeployment.has(incident.subject) ||
        /^(okx|example)(:|$)/.test(incident.subject)
          ? 'source'
          : 'workspace',
    });
  }
  // Current inventory takes priority; missing valuations remain visible and do not
  // turn into a zero. Deterministic ordering keeps the queue stable on each refresh.
  return issues.sort((a, b) => {
    const priority = (issue: DeskIssue) =>
      issue.group
        ? issue.phase.includes('compensat') ||
          (issue.inventory?.some((position) => Number(position.quantity) !== 0) ?? false)
          ? 0
          : 1
        : 2;
    return (
      priority(a) - priority(b) ||
      (a.since ?? Infinity) - (b.since ?? Infinity) ||
      a.id.localeCompare(b.id)
    );
  });
}
