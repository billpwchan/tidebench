import type { ProResult, ProRun, RecordData } from '../proApi';
export function researchReturn(run: ProRun) {
  return (
    run.summary?.total_return_pct ??
    (run.summary?.metrics as RecordData | undefined)?.total_return_pct ??
    run.summary?.median_return_pct ??
    (run.summary?.oos_summary as RecordData | undefined)?.median_return_pct ??
    run.result?.metrics?.total_return_pct ??
    (run.result?.result as ProResult | undefined)?.metrics?.total_return_pct ??
    (run.result?.oos_summary as RecordData | undefined)?.median_return_pct
  );
}
export function resultScope(run: ProRun) {
  return ['train_test', 'walk_forward'].includes(run.config.mode)
    ? 'Median test return'
    : run.config.mode === 'grid'
      ? 'In-sample scenarios'
      : run.config.mode === 'cost_stress'
        ? 'Cost sensitivity'
        : 'Single run';
}
