import type { Bar } from '../api';
export type Page =
  | 'overview'
  | 'strategies'
  | 'research'
  | 'execution'
  | 'paper'
  | 'risk'
  | 'data'
  | 'operations'
  | 'settings';
export const symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'OKB-USDT', 'DOGE-USDT'];
export const bars: Bar[] = ['15m', '1H', '4H', '1Dutc'];
