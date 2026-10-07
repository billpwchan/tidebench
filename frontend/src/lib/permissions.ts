export const canResearch = (role: string | undefined) =>
  ['admin', 'trader', 'researcher'].includes(role ?? '');
export const canTrade = (role: string | undefined) => ['admin', 'trader'].includes(role ?? '');
export const canManageRisk = (role: string | undefined) =>
  ['admin', 'trader', 'risk_operator'].includes(role ?? '');
