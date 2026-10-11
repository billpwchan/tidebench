// Canvas charts read the same semantic colors as the surrounding controls.
export function chartPalette(node: HTMLElement) {
  const tokens = getComputedStyle(node);
  const color = (name: string, fallback: string) =>
    tokens.getPropertyValue(name).trim() || fallback;
  return {
    surface: color('--canvas', '#ffffff'),
    strategy: color('--accent', '#176e7a'),
    benchmark: color('--research-benchmark', '#627183'),
    drawdown: color('--negative', '#b24b43'),
    positive: color('--positive', '#217653'),
    text: color('--muted', '#556572'),
    grid: color('--line', '#dfe6eb'),
    ink: color('--ink', '#1e2c36'),
  };
}
