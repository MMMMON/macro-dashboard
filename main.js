'use strict';

const COLORS = ['#9d2933', '#315f8a', '#c28a1b', '#2b7a78', '#617a3f', '#765797', '#c45d48'];
const PANELS = [
  { id: 'crypto', title: 'BTC / ETH', subtitle: '数字资产 · 比特币与以太坊', keys: ['btc', 'eth'], axes: ['left', 'right'], labels: ['BTC · USD', 'ETH · USD'] },
  { id: 'bonds-oil', title: '中美国债与原油', subtitle: '十年期国债收益率 × WTI 原油期货', keys: ['us10y', 'cn10y', 'oil'], axes: ['left', 'left', 'right'], labels: ['国债收益率 · %', '原油 · USD/桶'] },
  { id: 'sp500', title: '美股标普 500', subtitle: 'S&P 500 · 美国大盘股', keys: ['sp500'], axes: ['right'], labels: ['', '指数 · 点'] },
  { id: 'gold-dollar', title: '黄金与美元', subtitle: 'COMEX 黄金期货 × 美元指数', keys: ['gold', 'dxy'], axes: ['left', 'right'], labels: ['黄金 · USD/盎司', 'DXY · 点'] },
  { id: 'm7', title: '美股七巨头 M7', subtitle: 'AAPL / MSFT / NVDA / AMZN / GOOGL / META / TSLA', keys: ['aapl', 'msft', 'nvda', 'amzn', 'googl', 'meta', 'tsla'], normalized: true, wide: true, labels: ['', '区间首个共同交易日 = 100'] },
  { id: 'global', title: '全球核心股指', subtitle: '美国 · 德国 · 日本 · 中国香港 · 中国内地', keys: ['sp500', 'nasdaq', 'dax', 'nikkei', 'hsi', 'shanghai'], normalized: true, wide: true, labels: ['', '本币指数 · 共同基准日 = 100'] },
  { id: 'real-inflation', title: '实际利率与通胀预期', subtitle: '美国 10Y TIPS 实际收益率 × 10Y 盈亏平衡通胀率', keys: ['real10y', 'breakeven10y'], axes: ['right', 'right'], labels: ['', '收益率 · %'] },
  { id: 'dollar', title: '美元指数', subtitle: 'DXY · 美元相对一篮子货币', keys: ['dxy'], axes: ['right'], labels: ['', '指数 · 点'] },
];
let snapshot;
let selectedRange = '6M';
let busy = false;
const cards = [];
const $ = (id) => document.getElementById(id);
const number = (value, digits = 2) => new Intl.NumberFormat('en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function alphaColor(hex, opacity) {
  const value = hex.replace('#', '');
  const red = parseInt(value.slice(0, 2), 16);
  const green = parseInt(value.slice(2, 4), 16);
  const blue = parseInt(value.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${opacity})`;
}

function restoreSeriesStyle(card) {
  for (const item of card.lines) {
    const selected = card.focusKey === item.key;
    item.line.applyOptions({
      visible: !card.focusKey || selected,
      color: item.color,
      lineWidth: selected ? 3 : 2,
      lastValueVisible: selected,
    });
    item.button.setAttribute('aria-pressed', String(selected));
  }
  card.article.classList.toggle('has-focus', !!card.focusKey);
  card.reset.disabled = !card.focusKey;
}

function setFocus(card, key) {
  card.focusKey = card.focusKey === key ? null : key;
  restoreSeriesStyle(card);
}

function previewSeries(card, key) {
  if (card.focusKey) return;
  for (const item of card.lines) {
    const selected = item.key === key;
    item.line.applyOptions({
      visible: true,
      color: selected ? item.color : alphaColor(item.color, 0.16),
      lineWidth: selected ? 3 : 1,
      lastValueVisible: selected,
    });
  }
}

function initializeCharts() {
  for (const [index, config] of PANELS.entries()) {
    const article = element('article', 'chart-card');
    if (config.wide) article.classList.add('chart-card--wide');
    if (config.keys.length >= 5) article.classList.add('chart-card--dense');
    const heading = element('div', 'card-heading');
    const titles = element('div');
    const title = element('h3', '', config.title);
    title.id = `title-${config.id}`;
    titles.append(title, element('p', 'card-subtitle', config.subtitle));
    heading.append(titles, element('span', 'card-number', String(index + 1).padStart(2, '0')));
    const legendBar = element('div', 'legend-bar');
    const legend = element('div', 'legend');
    const reset = element('button', 'reset-focus', '全部曲线');
    reset.type = 'button';
    reset.disabled = true;
    reset.title = '退出单线聚焦';
    legendBar.append(legend, reset);
    const axes = element('div', 'axis-labels');
    axes.append(...config.labels.map(text => element('span', '', text)));
    const wrap = element('div', 'plot-wrap');
    const plot = element('div', 'plot');
    plot.id = `chart-${config.id}`;
    plot.setAttribute('aria-labelledby', title.id);
    const empty = element('div', 'plot-empty', '正在加载数据…');
    wrap.append(plot, empty);
    const foot = element('div', 'card-foot');
    article.append(heading, legendBar, axes, wrap, foot);
    $('dashboard').append(article);
    const chart = LightweightCharts.createChart(plot, {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#fffefb' }, textColor: '#6f6a63', fontSize: 10, attributionLogo: true },
      grid: { vertLines: { color: '#eee9df' }, horzLines: { color: '#e7e0d5', style: 2 } },
      leftPriceScale: { visible: !!config.axes?.includes('left'), borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.1 } },
      rightPriceScale: { visible: true, borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.1 } },
      timeScale: { borderVisible: false, timeVisible: false, rightOffset: 3 },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: '#aaa096' }, horzLine: { color: '#aaa096' } },
      handleScroll: { vertTouchDrag: false },
      localization: { locale: 'zh-CN' },
    });
    const card = { config, chart, lines: [], empty, foot, article, reset, focusKey: null };
    const lines = config.keys.map((key, i) => {
      const line = chart.addSeries(LightweightCharts.LineSeries, {
        color: COLORS[i], lineWidth: 2, priceScaleId: config.axes?.[i] || 'right',
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerRadius: 3,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      });
      const button = element('button');
      button.type = 'button';
      button.setAttribute('aria-pressed', 'false');
      const swatch = element('span', 'swatch');
      swatch.style.background = COLORS[i];
      const label = element('span', '', key.toUpperCase());
      const value = element('span', 'legend-value', '—');
      button.append(swatch, label, value);
      button.addEventListener('click', () => setFocus(card, key));
      button.addEventListener('pointerenter', () => previewSeries(card, key));
      button.addEventListener('pointerleave', () => restoreSeriesStyle(card));
      button.addEventListener('focus', () => previewSeries(card, key));
      button.addEventListener('blur', () => restoreSeriesStyle(card));
      legend.append(button);
      return { key, line, label, value, button, color: COLORS[i], latest: null, data: [] };
    });
    card.lines = lines;
    reset.addEventListener('click', () => { card.focusKey = null; restoreSeriesStyle(card); });
    chart.subscribeCrosshairMove(param => {
      for (const item of lines) {
        const point = param.seriesData?.get(item.line);
        const value = param.time ? point?.value : item.latest;
        item.value.textContent = Number.isFinite(value) ? number(value) : '—';
      }
    });
    cards.push(card);
  }
}

function rangeStart(last) {
  if (selectedRange === 'ALL') return '0000-01-01';
  const day = new Date(`${last}T00:00:00Z`);
  const days = { '1M': 30, '3M': 90, '6M': 180, '1Y': 365 }[selectedRange];
  day.setUTCDate(day.getUTCDate() - days);
  return day.toISOString().slice(0, 10);
}

function isDelayed(item) {
  return item?.last_date && (Date.now() - Date.parse(`${item.last_date}T00:00:00Z`)) / 86400000 > (item.stale_days || 7);
}

function renderCharts() {
  for (const card of cards) {
    const { config, chart, lines, empty, foot } = card;
    const dates = lines.flatMap(item => snapshot.series[item.key]?.data.map(p => p.time) || []);
    const last = dates.length ? dates.reduce((a, b) => a > b ? a : b) : new Date().toISOString().slice(0, 10);
    const start = rangeStart(last);
    const filtered = lines.map(item => (snapshot.series[item.key]?.data || []).filter(p => p.time >= start));
    let baseDay;
    if (config.normalized) {
      const available = filtered.filter(points => points.length);
      if (available.length) {
        const sets = available.slice(1).map(points => new Set(points.map(p => p.time)));
        baseDay = available[0].find(p => sets.every(set => set.has(p.time)))?.time;
      }
    }
    let shown = 0;
    lines.forEach((item, i) => {
      const series = snapshot.series[item.key];
      let points = filtered[i];
      if (config.normalized) {
        const base = points.find(p => p.time === baseDay)?.value;
        points = base ? points.filter(p => p.time >= baseDay).map(p => ({ time: p.time, value: p.value / base * 100 })) : [];
      }
      item.data = points;
      item.line.setData(points);
      item.latest = points.at(-1)?.value;
      item.value.textContent = Number.isFinite(item.latest) ? number(item.latest) : '—';
      const axis = config.axes?.[i];
      item.label.textContent = `${series?.name || item.key}${config.axes?.includes('left') ? ` · ${axis === 'left' ? '左' : '右'}` : ''}`;
      item.button.title = `${series?.name || item.key} | ${series?.unit || ''} | 最新观测 ${series?.last_date || '暂无'} | 点击单线聚焦`;
      shown += points.length > 0;
    });
    empty.hidden = shown > 0;
    empty.textContent = config.normalized && dates.length ? '所选区间暂无共同交易日' : '暂无有效数据 · 等待数据源恢复';
    const issues = lines.filter(item => {
      const s = snapshot.series[item.key];
      return !s?.data.length || s.status !== 'ok' || isDelayed(s);
    });
    foot.classList.toggle('warning', issues.length > 0);
    restoreSeriesStyle(card);
    foot.replaceChildren(
      element('span', '', issues.length ? `${issues.length} 项缺失 / 缓存 / 延迟 · 见数据口径` : (config.normalized ? `基准日 ${baseDay || '—'} · 点击图例聚焦` : '日线 · 点击图例聚焦')),
      element('span', '', `最新 ${dates.length ? last : '—'}`),
    );
    chart.timeScale().fitContent();
  }
}

function renderOverview() {
  $('overview').replaceChildren();
  for (const key of ['sp500', 'btc', 'us10y', 'dxy']) {
    const s = snapshot.series[key];
    const latest = s?.data.at(-1);
    const previous = s?.data.at(-2);
    const diff = previous ? latest.value - previous.value : null;
    const change = diff === null ? '暂无变化数据' : key === 'us10y'
      ? `${diff >= 0 ? '+' : ''}${number(diff * 100)} bp`
      : previous.value === 0 ? '—' : `${diff >= 0 ? '+' : ''}${number(diff / previous.value * 100)}%`;
    const card = element('div', 'stat-card');
    const top = element('div', 'stat-top');
    top.append(element('span', '', s?.name || key), element('span', 'stat-symbol', s?.symbol || ''));
    const value = element('p', 'stat-value', latest ? `${key === 'btc' ? '$' : ''}${number(latest.value)}${key === 'us10y' ? '%' : ''}` : '—');
    const bottom = element('div', 'stat-bottom');
    bottom.append(element('span', diff === null ? 'neutral' : diff >= 0 ? 'positive' : 'negative', change), element('span', '', `${latest?.time || '暂无数据'} · ${s?.status !== 'ok' || isDelayed(s) ? '缓存/延迟' : '较前值'}`));
    card.append(top, value, bottom);
    $('overview').append(card);
  }
}

function renderSources() {
  $('source-list').replaceChildren();
  for (const s of Object.values(snapshot.series)) {
    const row = element('div', 'source-item');
    const a = element('a', '', `${s.name} ↗`);
    if (/^https:\/\//.test(s.source_url)) a.href = s.source_url;
    a.target = '_blank'; a.rel = 'noopener noreferrer';
    row.append(a, element('div', '', `${s.source} · ${s.unit} · ${s.frequency === 'daily' ? '日度' : s.frequency}`),
      element('div', '', `最新 ${s.last_date || '无数据'} · ${{ ok: '获取成功', cached: '使用缓存', unavailable: '暂不可用' }[s.status]}${isDelayed(s) ? ' · 数据延迟' : ''}`),
      element('div', '', s.note || ''), element('div', '', s.message || ''));
    $('source-list').append(row);
  }
}

function validateSnapshot(data) {
  if (data?.schema_version !== 1 || !data.series || !Number.isFinite(Date.parse(data.generated_at))) throw new Error('数据文件格式不正确');
  for (const key of new Set(PANELS.flatMap(p => p.keys))) {
    const s = data.series[key];
    if (!s || !Array.isArray(s.data)) throw new Error(`缺少指标 ${key}`);
    let previous = '';
    for (const p of s.data) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(p.time) || !Number.isFinite(p.value) || p.time <= previous) throw new Error(`指标 ${key} 的日期或数值无效`);
      previous = p.time;
    }
  }
  return data;
}

async function loadData() {
  if (busy) return;
  busy = true;
  $('refresh').disabled = true;
  try {
    const response = await fetch('./data.json', { cache: 'no-store', signal: AbortSignal.timeout(20000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const next = validateSnapshot(await response.json());
    snapshot = next;
    renderCharts(); renderOverview(); renderSources();
    const updated = new Date(snapshot.generated_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
    $('update-status').textContent = `快照生成 ${updated} 北京时间`;
    const old = Date.now() - Date.parse(snapshot.generated_at) > 3 * 86400000;
    $('error-banner').hidden = !old;
    if (old) $('error-banner').textContent = '快照已超过 3 天未变化，请检查自动更新任务；各指标实际日期见图例及数据口径。';
  } catch (error) {
    $('error-banner').hidden = false;
    $('error-banner').textContent = `读取快照失败：${error.message}。${snapshot ? '继续显示上次读取的数据。' : '请确认 data.json 已生成，并通过网站地址或本地 HTTP 服务打开页面。'}`;
    if (!snapshot) {
      $('update-status').textContent = '数据读取失败';
      for (const card of cards) card.empty.textContent = '数据暂不可用 · 请稍后刷新';
    }
  } finally {
    busy = false;
    $('refresh').disabled = false;
  }
}

try {
  if (!window.LightweightCharts) throw new Error('图表组件未加载，请检查网站构建结果');
  initializeCharts();
  $('range-control').addEventListener('click', event => {
    const button = event.target.closest('button[data-range]');
    if (!button) return;
    selectedRange = button.dataset.range;
    for (const child of $('range-control').children) child.setAttribute('aria-pressed', String(child === button));
    if (snapshot) renderCharts();
  });
  $('refresh').addEventListener('click', loadData);
  loadData();
  setInterval(() => { if (!document.hidden) loadData(); }, 15 * 60 * 1000);
} catch (error) {
  $('error-banner').hidden = false;
  $('error-banner').textContent = error.message;
  $('update-status').textContent = '页面初始化失败';
}
