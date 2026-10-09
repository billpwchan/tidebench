export const deskZh: Record<string, string> = {
  'Reduce account position': '减少账户持仓',
  'PROTECTIVE EXECUTION': '保护性处置',
  'This reduces the shared account position. Reductions are allocated across its current inventory owners; they do not close only the selected portfolio.':
    '此操作减少共同账户的净持仓，并按比例分配至该市场当前的所有持仓所有者；不会只平掉所选组合。',
  'Current account net quantity': '账户当前净数量',
  'Exit direction': '退出方向',
  'Buy to reduce a short': '买入减空仓',
  'Sell to reduce a long': '卖出减多仓',
  '1 · Prevent automatic re-entry': '1 · 阻止自动重新入场',
  'Controllers stopped · no working entries': '控制器已停止 · 无待执行入场单',
  'Protection preparation required': '需要先准备保护',
  'Stop every controller in this market and cancel its working entry orders before reducing. Stopping a managed leg stops its entire group and retains all other inventory.':
    '减仓前停止该市场的所有控制器，并取消待执行入场单。停止托管腿会停止整个组合，其余持仓继续保留。',
  'active market controllers': '个运行中的市场控制器',
  'working entry orders': '笔待执行入场单',
  'Stop controllers and cancel entries': '停止控制器并取消入场单',
  '2 · Preview a reduce-only exit': '2 · 预览只减仓退出',
  'Inventory changed. Refresh the position and preview again.':
    '持仓已变化。请刷新持仓并重新预览。',
  'Refresh the position and preview again.': '请刷新持仓并重新预览。',
  'Prepare protection again; a controller or entry order is still active.':
    '仍有控制器或入场单在运行，请重新准备保护。',
  'Exit quantity': '退出数量',
  'Refresh to full current quantity': '刷新为当前全部数量',
  'Market exit · reduce-only locked. The transaction rejects an oversize exit or a changed direction; it cannot open or reverse a position.':
    '市价退出 · 只减仓已锁定。事务会拒绝超过现有持仓的数量或错误方向，不能开仓或反向建仓。',
  'Preview protective exit': '预览保护性退出',
  'Estimated exit price': '预计退出价格',
  'Exit notional (USDT)': '退出名义金额（USDT）',
  'Estimated fee (USDT)': '预计费用（USDT）',
  'Quote observed at': '报价观察时间',
  'Submit reduce-only exit': '提交只减仓退出',
  'Protective exit filled': '保护性退出已成交',
  'Controllers remain stopped. Review remaining positions and working protective orders before starting a new release.':
    '控制器继续保持停止。启动新发布前，请核对剩余持仓和待执行保护订单。',
  'No position remains in this market. No exit order will be submitted.':
    '该市场已无持仓，不会提交退出订单。',
  'Affected inventory owners': '受影响的持仓所有者',
  'Ownership is not verified. Protective account reductions remain available; attribution requires separate recovery.':
    '归属尚未核验。账户保护性减仓仍可用；归因需要单独恢复。',
  'No current inventory': '当前无持仓',
  'This fold is selected after its test results became available. Its displayed test performance is not independent validation of this deployment choice.':
    '此测试折在测试结果可取得后才被选择，已显示的测试表现不能独立验证这次部署选择。',
  'The parameters were chosen on training data, but this deployment fold is selected after test results became available. A new independent final evaluation is needed to validate that choice.':
    '参数来自训练数据，但部署测试折在结果可取得后才被选择。需要新的独立最终评估来验证该选择。',
  training_selected_test_exposed_fold: '训练选参 · 测试结果可见后选折',
  evidence_unavailable: '证据缺失 · 尝试记录保留',
  'Recorded attempts': '已记录尝试',
  'Primary evaluations': '首次评估',
  'Replay attempts': '重放尝试',
  'Distinct configurations': '不同配置',
  Attempt: '尝试类型',
  'Counts retain admitted attempts across financial restore, including missing results and replays. Replay is not a new primary evaluation. Distinct configurations are workflow counts, not independent hypotheses. External trials are outside these counts.':
    '财务恢复后仍保留已获准的尝试，包括缺失结果和重放。重放不是新的首次评估；不同配置数是工作流计数，不代表独立假设。外部尝试不在计数内。',
};
Object.assign(deskZh, {
  'Account observation window': '账户观察窗口',
  'These are shared-account results, including manual activity and every strategy. A selected observation window is not a strategy return.':
    '这是共同账户结果，包含手工操作和所有策略；所选观察窗口不代表单一策略收益。',
  'Observed from (UTC)': '观察起点（UTC）',
  'Observed until (UTC, exclusive)': '观察终点（UTC，不含该时刻）',
  'Review fixed account window': '审阅固定账户窗口',
  'All account observations': '全部账户观察',
  'Observation acceptance': '观察验收',
  'Observed targets met': '观察目标已达到',
  'Observation targets not met': '观察目标尚未达到',
  'Actual observation time, not accelerated market time': '真实观察时间，不是加速行情时间',
  Criterion: '判据',
  Result: '结果',
  Met: '达到',
  'Not met': '未达到',
  public_okx_observations: '真实公开 OKX 观察',
  market_clock_progress: '行情时钟推进',
  bound_observation_clocks: '观察时钟绑定',
  real_wall_duration: '真实经过时长',
  observation_count: '观察数量',
  observation_coverage: '观察覆盖率',
  economic_coverage: '经济状态完整覆盖率',
  fresh_final_observation: '最终观察新鲜度',
  no_pending_funding: '无未结资金费',
  observed_recovery: '实际恢复观察',
  no_clock_regression: '无时钟回退',
  no_financial_discontinuity: '无财务断点',
  'Passing these observations does not establish profitable edge, venue capacity or HTTP availability.':
    '通过这些观察不能证明盈利优势、交易所容量或 HTTP 可用性。',
  'Frozen account windows': '已冻结账户窗口',
  'Saved content hashes are checked when listed. Full recomputation is a separate verification action; later observations do not change a frozen report.':
    '列表会检查保存内容的哈希；完整重新计算需要单独验证。后续观察不会改变已冻结报告。',
  'No frozen account windows': '尚无冻结账户窗口',
  'Frozen at': '冻结时间',
  'Stored content': '保存内容',
  'Hash valid · not recomputed': '哈希有效 · 尚未重新计算',
  'Evidence unavailable': '证据不可用',
  'Open frozen window': '打开冻结窗口',
  'Older frozen windows': '更早的冻结窗口',
});

Object.assign(deskZh, {
  'Observed value': '实际观察值',
  'Required value': '要求值',
  'Window observations': '窗口观察数',
  'Economic coverage': '经济状态完整覆盖率',
  'Selected frozen account window': '已选冻结账户窗口',
  'This saved report is separate from the current observation metrics above.':
    '此保存报告与上方当前观察指标分别展示。',
  'Funding schedule needs attention': '资金费率日程需要处理',
  'A current settlement schedule is unavailable. Equity is provisional and new perpetual risk is blocked; reduce-only protection remains available.':
    '无法获取有效的结算日程。权益为暂定值，新增永续风险已阻止；仍可执行只减仓保护。',
});

Object.assign(deskZh, {
  'Bound research evidence is incomplete. Refresh the review before approval.':
    '绑定研究证据不完整。批准前请刷新审阅。',
});
