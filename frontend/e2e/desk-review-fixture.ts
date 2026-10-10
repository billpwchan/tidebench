import type { Page } from '@playwright/test';
type Fixture = {
  stale: boolean;
  future: number;
  priceTick: string;
  micro: boolean;
  mutations: string[];
  session: string;
};
export async function desk(
  page: Page,
  entry = 'overview',
  busy = true,
  source = 'example',
): Promise<Fixture> {
  const state: Fixture = {
    stale: false,
    future: 0,
    priceTick: '0.1',
    micro: false,
    mutations: [],
    session: 'desk-usability',
  };
  const now = Date.now();
  const positions = busy
    ? Array.from({ length: 12 }, (_, i) => ({
        inst_id: `${i ? 'ASSET' + i : 'BTC'}-USDT`,
        inst_type: 'SPOT',
        side: 'long',
        quantity: '1',
        mark: String(100 + i),
        entry_price: '104',
        margin: '0',
        maintenance_margin: '0',
        liquidation_price: null,
        market_value: String(100 + i),
        unrealized_pnl: String(i - 4),
        as_of: now,
      }))
    : [];
  const account = {
    source,
    equity: busy ? '7266' : '6000',
    available_cash: busy ? '5969.97' : '6000',
    cash: '6000',
    gross_notional: busy ? '1266' : '0',
    used_margin: '0',
    maintenance_margin: '0',
    unrealized_pnl: busy ? '18' : '0',
    positions,
    as_of: now,
    economic_status: 'complete',
    valuation_status: 'example',
    execution_mode: 'local-paper',
  };
  await page.addInitScript(() => {
    localStorage.setItem('tidebench:language', 'en');
    localStorage.setItem('tidebench:source', 'example');
  });
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/v1', '');
    if (route.request().method() !== 'GET') {
      state.mutations.push(path);
      return route.fulfill({
        status: 405,
        json: { error: { message: 'Read-only desk fixture.' } },
      });
    }
    let json: unknown = { items: [] };
    if (path === '/auth/status')
      json = {
        authenticated: true,
        auth_required: true,
        setup_required: false,
        user: { id: state.session, username: state.session, role: 'admin' },
      };
    else if (path === '/system') json = { version: '0.14.0', execution: 'local-paper' };
    else if (path === '/pro/execution/account') json = account;
    else if (path === '/pro/execution/risk') json = { source, halted: false, max_leverage: 10 };
    else if (path === '/pro/execution/orders')
      json = {
        items: busy
          ? Array.from({ length: 300 }, (_, i) => ({
              id: `order-${i}`,
              source: 'example',
              inst_id: `${i % 2 ? 'ETH' : 'BTC'}-USDT`,
              side: 'buy',
              quantity: '0.01',
              status: 'pending',
              order_type: 'limit',
              limit_price: '10',
              reduce_only: false,
              created_at: now - i,
            }))
          : [],
      };
    else if (path === '/pro/execution/analytics')
      json = {
        source,
        status: 'available',
        summary: { gross_notional: busy ? '1266' : '0', net_notional: busy ? '1266' : '0' },
        assets: [],
        input_snapshot: { account },
      };
    else if (path === '/pro/ops') json = { health: { status: 'ok' }, incidents: [], jobs: [] };
    else if (path === '/pro/research/runs')
      json = {
        items: busy
          ? Array.from({ length: 8 }, (_, i) => ({
              id: `study-${i}`,
              source: 'example',
              status: 'completed',
              mode: 'train_test',
              created_at: now - i,
              config: { strategy: { kind: 'sma_cross' }, fee_bps: '10', slippage_bps: '5' },
            }))
          : [],
      };
    else if (path === '/pro/catalog/datasets')
      json = {
        items: [
          {
            id: 'a'.repeat(32),
            source,
            inst_id: 'BTC-USDT',
            kind: 'trade',
            bar: '1H',
            start: Date.UTC(2025, 0, 1),
            end: Date.UTC(2025, 1, 1),
            content_hash: 'b'.repeat(64),
          },
        ],
      };
    else if (path === '/pro/catalog/instruments')
      json = {
        items: ['BTC', 'ETH'].map((asset) => ({
          inst_id: `${asset}-USDT${url.searchParams.get('inst_type') === 'SWAP' ? '-SWAP' : ''}`,
          state: 'live',
        })),
      };
    else if (path === '/pro/market') {
      const symbol = url.searchParams.get('inst_id') ?? 'BTC-USDT';
      const swap = symbol.endsWith('-SWAP');
      json = {
        inst_id: symbol,
        source,
        ts: state.stale ? now - 60000 : Date.now() + state.future,
        last: state.micro ? '0.00000341' : '100',
        bid: state.micro ? '0.00000341' : '100',
        ask: state.micro ? '0.00000342' : '100.1',
        mark: state.micro ? '0.00000341' : '100',
        instrument: {
          inst_id: symbol,
          inst_type: swap ? 'SWAP' : 'SPOT',
          base: symbol.split('-')[0],
          quote: 'USDT',
          settle_ccy: 'USDT',
          ct_val_ccy: symbol.split('-')[0],
          ct_val: '0.01',
          ct_mult: '1',
          tick_size: state.priceTick,
          lot_size: swap ? '1' : '0.0001',
          min_size: swap ? '1' : '0.0001',
        },
      };
    }
    await route.fulfill({ json });
  });
  await page.goto(`/#${entry}?source=${source}&view=${entry === 'execution' ? 'positions' : ''}`);
  return state;
}
