/* Keep tables and navigation usable when the chart CDN is unavailable. */
window.Chart = window.Chart || class ChartFallback {
  static defaults = { font: {}, color: '' };
  static isFallback = true;
  constructor() {}
  destroy() {}
};
