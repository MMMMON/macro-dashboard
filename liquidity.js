'use strict';

const $ = (id) => document.getElementById(id);
const format = (value, digits = 2) => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value) : '—';
const MODULES = [
  { key: 'q', title: 'Q · 资金数量', max: 50, color: '#315f8a', subtitle: 'ON RRP、准备金与 Fed 资产负债表', metrics: [['on_rrp', 'ON RRP', 'T', 3], ['reserves', '准备金', 'T', 3], ['reserve_change', '准备金周变动', 'T', 3]] },
  { key: 'p', title: 'P · 资金价格', max: 35, color: '#9d2933', subtitle: '曲线、远期利率与实际利率', metrics: [['curve_2s10s', '2Y–10Y', 'bp', 0], ['ois_1y1y', '1Y1Y 代理', '%', 2], ['tips_10y', '10Y TIPS', '%', 2]] },
  { key: 'g', title: 'g · 传导结构', max: 15, color: '#2b7a78', subtitle: '隔夜资金、Repo 与 T-bill 供给', metrics: [['sofr_iorb', 'SOFR–IORB', 'bp', 1], ['repo', 'Repo 结构', '', 0], ['tbill', 'T-bill 虹吸', '', 0]] },
];
let snapshot;
const charts = [];

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scoreClass(status) {
  if (!status || status === '待核验') return 'pending';
  if (status.includes('宽松')) return 'loose';
  if (status.includes('中性')) return 'neutral';
  if (status.includes('脆弱')) return 'fragile';
  return 'tight';
}

function metricValue(key, value, unit, digits) {
  if (!Number.isFinite(value)) return '待核验';
  if (key === 'repo') return { 1: '隔夜偏多', 2: '定期增多', 3: '结构分化' }[value] || '待核验';
  if (key === 'tbill') return { 1: '轻虹吸', 2: '强虹吸' }[value] || '待核验';
  return `${format(value, digits)}${unit ? ` ${unit}` : ''}`;
}

function createModule(module) {
  const article = createElement('article', 'pqg-module');
  const heading = createElement('div', 'pqg-module-heading');
  const titleGroup = createElement('div');
  titleGroup.append(createElement('h2', '', module.title), createElement('p', '', module.subtitle));
  const score = createElement('div', 'module-score', '—');
  score.id = `score-${module.key}`;
  heading.append(titleGroup, score);
  const cards = createElement('div', 'metric-grid');
  for (const [key, label, unit, digits] of module.metrics) {
    const card = createElement('div', 'metric-card');
    const value = createElement('strong', '', '—');
    value.id = `metric-${key}`;
    const tag = createElement('span', 'metric-tag', '待核验');
    tag.id = `tag-${key}`;
    card.append(createElement('span', 'metric-label', label), value, createElement('small', '', unit), tag);
    cards.append(card);
  }
  const plotWrap = createElement('div', 'pqg-plot-wrap');
  const plot = createElement('div', 'pqg-plot');
  plot.id = `plot-${module.key}`;
  const empty = createElement('div', 'module-empty', '正在加载周度得分…');
  plotWrap.append(plot, empty);
  const note = createElement('div', 'module-note');
  note.id = `note-${module.key}`;
  article.append(heading, cards, plotWrap, note);
  $('pqg-modules').append(article);
  const chart = LightweightCharts.createChart(plot, {
    autoSize: true,
    layout: { background: { type: 'solid', color: '#fffefb' }, textColor: '#6f6a63', fontSize: 10, attributionLogo: true },
    grid: { vertLines: { color: '#eee9df' }, horzLines: { color: '#e7e0d5', style: 2 } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: .16, bottom: .12 } },
    leftPriceScale: { visible: false }, timeScale: { borderVisible: false, timeVisible: false, rightOffset: 1 },
    crosshair: { vertLine: { color: '#aaa096' }, horzLine: { color: '#aaa096' } },
    handleScroll: { vertTouchDrag: false }, localization: { locale: 'zh-CN' },
  });
  const line = chart.addSeries(LightweightCharts.LineSeries, { color: module.color, lineWidth: 3, priceLineVisible: false, lastValueVisible: true, crosshairMarkerRadius: 4, priceFormat: { type: 'price', precision: 0, minMove: 1 } });
  charts.push({ module, chart, line, empty });
}

function renderSummary(latest) {
  const total = latest.scores.total;
  $('pqg-summary').replaceChildren();
  const totalCard = createElement('article', `pqg-total ${scoreClass(latest.scores.status)}`);
  totalCard.append(createElement('span', 'summary-label', '美元流动性状态'), createElement('strong', '', Number.isFinite(total) ? `${format(total, 0)} / 100` : '待核验'), createElement('span', 'status-pill', latest.scores.status), createElement('small', '', `截至 ${latest.as_of}`));
  $('pqg-summary').append(totalCard);
  for (const module of MODULES) {
    const value = latest.scores[module.key];
    const card = createElement('article', 'pqg-score-card');
    card.append(createElement('span', 'summary-label', module.title), createElement('strong', '', Number.isFinite(value) ? `${format(value, 0)} / ${module.max}` : '待核验'), createElement('small', '', Number.isFinite(value) ? '最近周度得分' : '等待所需输入'));
    $('pqg-summary').append(card);
  }
}

function renderModules(weeks) {
  const latest = weeks.at(-1);
  for (const view of charts) {
    const { module, chart, line, empty } = view;
    const points = weeks.filter(row => Number.isFinite(row.scores[module.key])).map(row => ({ time: row.as_of, value: row.scores[module.key] }));
    line.setData(points);
    empty.hidden = points.length > 0;
    empty.textContent = module.key === 'p' ? '缺少可验证的 CME SR3 远期代理 · P 分待核验' : '缺少完整输入 · 该模块得分待核验';
    chart.timeScale().fitContent();
    const current = latest.scores[module.key];
    $(`score-${module.key}`).textContent = Number.isFinite(current) ? `${format(current, 0)} / ${module.max}` : '待核验';
    $(`score-${module.key}`).style.color = module.color;
    for (const [key, , unit, digits] of module.metrics) {
      $(`metric-${key}`).textContent = metricValue(key, latest.metrics[key], unit, digits);
      $(`tag-${key}`).textContent = latest.labels[key] || '待核验';
    }
    const breakdown = latest.scores.breakdown[module.key];
    const note = breakdown ? Object.entries(breakdown).map(([key, value]) => `${key} ${value >= 0 ? '+' : ''}${value}`).join(' · ') : '输入缺失或延迟，分数暂不计算';
    $(`note-${module.key}`).textContent = note;
  }
}

function renderTable(weeks) {
  const body = $('weekly-table');
  body.replaceChildren();
  for (const row of weeks) {
    const m = row.metrics; const s = row.scores;
    const values = [row.week, metricValue('on_rrp', m.on_rrp, 'T', 3), metricValue('reserves', m.reserves, 'T', 3), metricValue('curve_2s10s', m.curve_2s10s, 'bp', 0), metricValue('ois_1y1y', m.ois_1y1y, '%', 2), metricValue('tips_10y', m.tips_10y, '%', 2), metricValue('sofr_iorb', m.sofr_iorb, 'bp', 1), metricValue('repo', m.repo), metricValue('tbill', m.tbill), s.q, s.p, s.g, s.total, s.status];
    const tr = createElement('tr');
    values.forEach((value, index) => {
      const cell = createElement('td', index === values.length - 1 ? `status-cell ${scoreClass(s.status)}` : '', Number.isFinite(value) ? format(value, 0) : String(value ?? '待核验'));
      if (index > 0 && index < 9 && row.labels[Object.keys({ on_rrp: 1, reserves: 1, curve_2s10s: 1, ois_1y1y: 1, tips_10y: 1, sofr_iorb: 1, repo: 1, tbill: 1 })[index - 1]]) cell.title = row.labels[Object.keys({ on_rrp: 1, reserves: 1, curve_2s10s: 1, ois_1y1y: 1, tips_10y: 1, sofr_iorb: 1, repo: 1, tbill: 1 })[index - 1]];
      tr.append(cell);
    });
    body.append(tr);
  }
}

function renderSources(liquidity) {
  const list = $('pqg-sources');
  list.replaceChildren();
  const sources = { ...liquidity.series, ...liquidity.repo_series, tbill_events: liquidity.tbill_events };
  for (const source of Object.values(sources)) {
    const row = createElement('div', 'source-item');
    const link = createElement('a', '', `${source.name} ↗`);
    link.href = source.source_url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    row.append(link, createElement('div', '', `${source.source} · ${source.unit} · ${source.frequency}`), createElement('div', '', `最新 ${source.last_date || '无数据'} · ${{ ok: '获取成功', cached: '使用缓存', unavailable: '暂不可用' }[source.status]}${source.stale ? ' · 数据延迟' : ''}`), createElement('div', '', source.note || ''), createElement('div', '', source.message || ''));
    list.append(row);
  }
}

function validate(data) {
  const liquidity = data?.liquidity_pqg;
  if (!Number.isInteger(data?.schema_version) || data.schema_version < 2 || !liquidity || !Array.isArray(liquidity.weeks)) throw new Error('流动性快照格式不正确');
  for (const row of liquidity.weeks) {
    if (!/^\d{4}-\d{2}$/.test(row.week) || !/^\d{4}-\d{2}-\d{2}$/.test(row.as_of) || !row.metrics || !row.scores) throw new Error('周度记录不完整');
  }
  return liquidity;
}

async function load() {
  try {
    const response = await fetch('./data.json', { cache: 'no-store', signal: AbortSignal.timeout(20000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    snapshot = await response.json();
    const liquidity = validate(snapshot);
    if (!liquidity.weeks.length) throw new Error('暂无可用周度数据');
    const latest = liquidity.weeks.at(-1);
    renderSummary(latest); renderModules(liquidity.weeks); renderTable(liquidity.weeks); renderSources(liquidity);
    const generated = new Date(snapshot.generated_at).toLocaleString('zh-CN', { timeZone: 'Europe/Berlin', hour12: false });
    $('pqg-update-status').textContent = `快照生成 ${generated} 柏林时间 · 最新周度 ${latest.week}`;
    $('pqg-error').hidden = true;
  } catch (error) {
    $('pqg-error').hidden = false;
    $('pqg-error').textContent = `读取流动性快照失败：${error.message}`;
    $('pqg-update-status').textContent = '数据读取失败';
  }
}

try {
  if (!window.LightweightCharts) throw new Error('图表组件未加载');
  MODULES.forEach(createModule);
  load();
  setInterval(() => { if (!document.hidden) load(); }, 15 * 60 * 1000);
} catch (error) {
  $('pqg-error').hidden = false;
  $('pqg-error').textContent = error.message;
}
