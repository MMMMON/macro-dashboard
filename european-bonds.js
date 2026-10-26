'use strict';

const SPREADS = [
  ['fr_de_10y', '法国 − 德国', '#cf564f'],
  ['it_de_10y', '意大利 − 德国', '#c78a2e'],
  ['gr_de_10y', '希腊 − 德国', '#76549a'],
  ['es_de_10y', '西班牙 − 德国', '#3c8b86'],
  ['us_eu_aaa_10y', '美国 − 欧元区AAA', '#3e668d'],
];
const TENORS = [['3m', '3M', '#c78a2e'], ['1y', '1Y', '#3c8b86'], ['5y', '5Y', '#4d79a5'], ['10y', '10Y', '#cf564f'], ['30y', '30Y', '#76549a']];
const COUNTRIES = [['fr', '法国'], ['de', '德国'], ['it', '意大利'], ['es', '西班牙'], ['gr', '希腊']];
const STATUS = { ok: '获取成功', cached: '使用最近成功缓存', unavailable: '暂不可用' };
const cards = [];
let snapshot = null;
let selectedRange = '1Y';
let busy = false;

const $ = id => document.getElementById(id);
function node(tag, className = '', text = '') {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text) result.textContent = text;
  return result;
}
function format(value) {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(value);
}
function delayed(item) { return !!item?.stale; }
function rangeStart(last) {
  const days = { '3M': 92, '1Y': 365, '3Y': 1095 }[selectedRange];
  const day = new Date(`${last}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() - days);
  return day.toISOString().slice(0, 10);
}
function chartOptions() {
  return {
    autoSize: true,
    layout: { background: { type: 'solid', color: '#fffdfa' }, textColor: '#716b64', fontSize: 10, attributionLogo: true },
    grid: { vertLines: { color: '#eeeae3' }, horzLines: { color: '#e7e2da', style: 2 } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: .12, bottom: .12 } },
    timeScale: { borderVisible: false, rightOffset: 3 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: '#a8a198' }, horzLine: { color: '#a8a198' } },
    handleScroll: { vertTouchDrag: false }, localization: { locale: 'zh-CN' },
  };
}
function applyFocus(card) {
  for (const item of card.items) {
    const muted = card.focusKey && item.key !== card.focusKey;
    item.line.applyOptions({ color: item.markersOnly ? 'rgba(0,0,0,0)' : muted ? `${item.color}24` : item.color, lineWidth: item.key === card.focusKey ? 3 : 2 });
    item.button.classList.toggle('is-muted', !!muted);
    item.button.setAttribute('aria-pressed', String(item.key === card.focusKey));
  }
}
function setFocus(card, key) {
  card.focusKey = card.focusKey === key ? null : key;
  applyFocus(card);
}
function createCard(target, config, index) {
  const article = node('article', 'euro-card');
  const head = node('div', 'euro-card-head');
  const titles = node('div');
  const title = node('h3', '', config.title);
  const subtitle = node('p', '', config.subtitle);
  titles.append(title, subtitle);
  head.append(titles, node('span', 'euro-card-number', String(index).padStart(2, '0')));
  const legend = node('div', 'euro-legend');
  const wrap = node('div', 'euro-plot-wrap');
  const plot = node('div', 'euro-plot');
  const empty = node('div', 'euro-empty', '正在加载数据…');
  wrap.append(plot, empty);
  const foot = node('div', 'euro-card-foot');
  article.append(head, legend, wrap, foot);
  target.append(article);
  const chart = LightweightCharts.createChart(plot, chartOptions());
  const card = { config, article, chart, legend, empty, foot, focusKey: null, items: [] };
  for (const [key, label, color] of config.lines) {
    const markersOnly = key === 'gr_1y';
    const line = chart.addSeries(LightweightCharts.LineSeries, {
      color: markersOnly ? 'rgba(0,0,0,0)' : color, lineWidth: 2,
      lastValueVisible: false, priceLineVisible: false,
      crosshairMarkerVisible: !markersOnly, crosshairMarkerRadius: 3,
      priceFormat: { type: 'price', precision: 2, minMove: .01 },
      autoscaleInfoProvider: config.kind === 'spread' ? original => {
        const info = original();
        return info ? { ...info, priceRange: { minValue: Math.min(0, info.priceRange.minValue), maxValue: Math.max(0, info.priceRange.maxValue) } } : null;
      } : undefined,
    });
    const button = node('button');
    button.type = 'button';
    button.setAttribute('aria-pressed', 'false');
    const swatch = node('span', 'euro-swatch');
    swatch.style.setProperty('--series-color', color);
    const value = node('span', 'euro-value', '—');
    button.append(swatch, node('span', '', label), value);
    button.addEventListener('click', () => setFocus(card, key));
    legend.append(button);
    const markerApi = markersOnly && LightweightCharts.createSeriesMarkers
      ? LightweightCharts.createSeriesMarkers(line, []) : null;
    card.items.push({ key, label, color, line, button, value, markersOnly, markerApi, latest: null });
  }
  if (config.kind === 'spread' && card.items.length) {
    card.items[0].line.createPriceLine({ price: 0, color: '#8f8981', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '0' });
  }
  chart.subscribeCrosshairMove(param => {
    for (const item of card.items) {
      const point = param.seriesData?.get(item.line);
      const value = param.time ? point?.value : item.latest;
      item.value.textContent = Number.isFinite(value) ? `${format(value)}${config.unit}` : '—';
    }
  });
  cards.push(card);
}
function initialize() {
  createCard($('spread-chart'), {
    title: '10年期国债利差', subtitle: '欧洲四国相对德国；美国相对欧元区 AAA · 共同观测日', unit: 'bp', kind: 'spread', lines: SPREADS,
  }, 1);
  COUNTRIES.forEach(([country, name], i) => createCard($('country-grid'), {
    title: `${name}国债收益率曲线`, subtitle: country === 'gr' ? '3M / 1Y拍卖点 / 5Y / 10Y / 30Y月频' : '3M / 1Y / 5Y / 10Y / 30Y',
    unit: '%', kind: 'country', lines: TENORS.map(([tenor, label, color]) => [`${country}_${tenor}`, label, color]),
  }, i + 2));
}
function sectionFor(card) { return card.config.kind === 'spread' ? snapshot.european_bonds.spreads : snapshot.european_bonds.series; }
function renderCard(card) {
  const section = sectionFor(card);
  const allDates = card.items.flatMap(item => (section[item.key]?.data || []).map(point => point.time));
  const last = allDates.length ? allDates.reduce((a, b) => a > b ? a : b) : new Date().toISOString().slice(0, 10);
  const start = rangeStart(last);
  let visible = 0;
  for (const item of card.items) {
    const series = section[item.key];
    const points = (series?.data || []).filter(point => point.time >= start);
    item.line.setData(points);
    item.latest = series?.data?.at(-1)?.value;
    item.value.textContent = Number.isFinite(item.latest) ? `${format(item.latest)}${card.config.unit}` : '—';
    item.button.title = `${series?.name || item.label} | 最新观测 ${series?.last_date || '暂无'} | ${STATUS[series?.status] || '状态未知'} | 点击聚焦`;
    if (item.markerApi) item.markerApi.setMarkers(points.map(point => ({ time: point.time, position: 'inBar', color: item.color, shape: 'circle', size: 1.2, text: '' })));
    visible += points.length > 0;
  }
  card.empty.hidden = visible > 0;
  card.empty.textContent = allDates.length ? '所选区间暂无真实观测' : '暂无有效数据 · 已保留来源状态，未使用模拟值';
  const issues = card.items.filter(item => {
    const series = section[item.key];
    return !series?.data?.length || series.status !== 'ok' || delayed(series);
  });
  card.foot.classList.toggle('warning', issues.length > 0);
  card.foot.replaceChildren(node('span', '', issues.length ? `${issues.length} 条缺失 / 缓存 / 延迟` : '来源正常 · 点击图例聚焦'), node('span', '', `最新观测 ${allDates.length ? last : '—'}`));
  applyFocus(card);
  card.chart.timeScale().fitContent();
}
function renderSources() {
  const target = $('euro-sources');
  target.replaceChildren();
  const groups = [snapshot.european_bonds.spreads, snapshot.european_bonds.series];
  for (const series of groups.flatMap(Object.values)) {
    const row = node('div', 'source-item');
    const link = node('a', '', `${series.name} ↗`);
    if (/^https:\/\//.test(series.source_url || '')) { link.href = series.source_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    row.append(link,
      node('div', '', `${series.source || '—'} · ${series.frequency === 'daily' ? '日频' : series.frequency === 'monthly' ? '月频' : series.frequency === 'event' ? '事件频率' : series.frequency}`),
      node('div', '', `最新 ${series.last_date || '无数据'} · ${STATUS[series.status] || '状态未知'}${delayed(series) ? ' · 延迟' : ''}`),
      node('div', '', `${series.note || ''}${series.message ? ` 状态说明：${series.message}` : ''}`));
    target.append(row);
  }
}
function validate(data) {
  const europe = data?.european_bonds;
  if (!Number.isFinite(Date.parse(data?.generated_at)) || !europe?.series || !europe?.spreads) throw new Error('快照尚未包含欧洲国债数据');
  for (const [group, configs] of [[europe.spreads, SPREADS], [europe.series, COUNTRIES.flatMap(([country]) => TENORS.map(([tenor, label, color]) => [`${country}_${tenor}`, label, color]))]]) {
    for (const [key] of configs) {
      const series = group[key];
      if (!series || !Array.isArray(series.data)) throw new Error(`缺少指标 ${key}`);
      let previous = '';
      for (const point of series.data) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(point.time) || point.time <= previous || !Number.isFinite(point.value)) throw new Error(`指标 ${key} 的日期或数值无效`);
        previous = point.time;
      }
    }
  }
  return data;
}
async function load() {
  if (busy) return;
  busy = true;
  try {
    const response = await fetch('./data.json', { cache: 'no-store', signal: AbortSignal.timeout(20000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    snapshot = validate(await response.json());
    cards.forEach(renderCard); renderSources();
    const generated = new Date(snapshot.generated_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
    $('euro-update').textContent = `快照生成 ${generated} 北京时间 · ${snapshot.european_bonds.state === 'success' ? '全部来源正常' : snapshot.european_bonds.state === 'partial' ? '部分来源缓存或不可用' : '来源暂不可用'}`;
    $('euro-observation').textContent = `综合最新观测 ${snapshot.european_bonds.as_of || '—'} · 默认最近 1 年`;
    $('euro-error').hidden = snapshot.european_bonds.state === 'success';
    if (!$('euro-error').hidden) $('euro-error').textContent = `数据健康状态：${snapshot.european_bonds.state}。缺失、缓存或延迟：${snapshot.european_bonds.issues.join('、') || '无'}。`;
  } catch (error) {
    $('euro-error').hidden = false;
    $('euro-error').textContent = `读取欧洲国债快照失败：${error.message}。${snapshot ? '继续显示上次读取的数据。' : '页面不会用模拟值补齐，请等待日更任务接通真实来源。'}`;
    $('euro-update').textContent = '数据读取失败';
  } finally { busy = false; }
}

try {
  if (!window.LightweightCharts) throw new Error('图表组件未加载');
  initialize();
  $('euro-range').addEventListener('click', event => {
    const button = event.target.closest('button[data-range]');
    if (!button) return;
    selectedRange = button.dataset.range;
    for (const child of $('euro-range').children) child.setAttribute('aria-pressed', String(child === button));
    if (snapshot) cards.forEach(renderCard);
  });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 15 * 60 * 1000);
} catch (error) {
  $('euro-error').hidden = false; $('euro-error').textContent = error.message; $('euro-update').textContent = '页面初始化失败';
}
