'use strict';

const $ = id => document.getElementById(id);
const nf = (value, digits = 2) => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value) : '—';
const COLORS = { q: '#315f8a', g: '#24756f', p: '#9d2933', gold: '#a97513', gray: '#776f67', ink: '#25211d' };
const RANGE_DAYS = { '1M': 31, '3M': 93, '6M': 186, '1Y': 366, ALL: Infinity };
let liquidity; let cmeWebSnapshot = null; let currentRange = '6M'; const chartViews = [];

function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function validSeries(item) { return Array.isArray(item?.data) ? item.data.filter(p => /^\d{4}-\d{2}-\d{2}$/.test(p.time) && Number.isFinite(p.value)) : []; }
function series(key) { return validSeries(liquidity.series[key]); }
function latest(points) { return points.length ? points.at(-1) : null; }
function sameDay(left, right, operation) { const map = new Map(right.map(p => [p.time, p.value])); return left.filter(p => map.has(p.time)).map(p => ({ time: p.time, value: operation(p.value, map.get(p.time)) })); }
function changeByRows(points, rows, multiplier = 1) { return points.slice(rows).map((p, i) => ({ time: p.time, value: (p.value - points[i].value) * multiplier })); }
function sumCommon(items) { if (!items.length || items.some(a => !a.length)) return []; const maps = items.slice(1).map(a => new Map(a.map(p => [p.time, p.value]))); return items[0].filter(p => maps.every(m => m.has(p.time))).map(p => ({ time: p.time, value: p.value + maps.reduce((sum, m) => sum + m.get(p.time), 0) })); }
function meanCommon(items) { const summed = sumCommon(items); return summed.map(p => ({ time: p.time, value: p.value / items.length })); }
function pairedWindowChange(left, right, rows = 20) {
  const rightMap = new Map(right.map(p => [p.time, p.value]));
  const paired = left.filter(p => rightMap.has(p.time)).map(p => ({ time: p.time, left: p.value, right: rightMap.get(p.time) }));
  if (paired.length <= rows) return null;
  const end = paired.at(-1); const start = paired.at(-1 - rows);
  return { start: start.time, end: end.time, left: end.left - start.left, right: end.right - start.right, observations: rows + 1 };
}
function freshness(item) { return !item || item.status === 'unavailable' ? '数据不足' : item.stale ? '数据延迟' : item.status === 'cached' ? '使用缓存' : '已更新'; }
function isFresh(key) { const item = liquidity.series[key]; return Boolean(item && item.status !== 'unavailable' && !item.stale); }
function consecutive(points, predicate) { let count = 0; for (let i = points.length - 1; i >= 0 && predicate(points[i].value); i--) count += 1; return count; }

function prepareDerived() {
  const qTotal = sameDay(series('reserves'), series('on_rrp'), (a, b) => a + b);
  const q4w = changeByRows(qTotal, 4);
  const qAccel = changeByRows(q4w, 1);
  const tgaChange = changeByRows(series('tga'), 1);
  const spread = sameDay(series('sofr'), series('iorb'), (a, b) => (a - b) * 100);
  const tips20 = changeByRows(series('tips_10y'), 20);
  const directOisKeys = ['ois_3m', 'ois_6m', 'ois_1y', 'ois_2y'];
  const termSofrKeys = ['term_sofr_1m', 'term_sofr_3m', 'term_sofr_6m', 'term_sofr_1y'];
  const oisShortCenter = meanCommon(directOisKeys.map(series));
  const termSofrCenter = meanCommon(termSofrKeys.map(series));
  const directLayer = directOisKeys.every(isFresh) ? pairedWindowChange(oisShortCenter, series('tips_10y'), 20) : null;
  const termLayer = termSofrKeys.every(isFresh) ? pairedWindowChange(termSofrCenter, series('tips_10y'), 20) : null;
  const firstLayer = directLayer || termLayer;
  const firstLayerSource = directLayer ? 'direct-ois' : termLayer ? 'term-sofr' : null;
  const curve2s10s = sameDay(series('dgs10'), series('dgs2'), (a, b) => (a - b) * 100);
  const curve5s30s = sameDay(series('dgs30'), series('dgs5'), (a, b) => (a - b) * 100);
  const assets = series('fed_assets');
  const qtActual = assets.slice(4).map((p, i) => ({ time: p.time, value: Math.max(0, assets[i].value - p.value) }));
  const rrpBuffer = sameDay(series('on_rrp'), qtActual, (rrp, qt) => rrp - qt);
  const repo = liquidity.repo_series || {};
  const repoTotal = sumCommon(['repo_dvp_total', 'repo_gcf_total', 'repo_tri_total'].map(k => validSeries(repo[k])));
  return { qTotal, q4w, qAccel, tgaChange, spread, tips20, oisShortCenter, termSofrCenter, firstLayer, firstLayerSource, curve2s10s, curve5s30s, qtActual, rrpBuffer, repoTotal };
}

function inferState(d) {
  const q = latest(d.q4w);
  const qState = !q ? 'unknown' : q.value > .02 ? 'loose' : q.value < -.02 ? 'tight' : 'flat';
  const spreadNow = latest(d.spread); const spreadDays = consecutive(d.spread, value => value > 0);
  const srfNow = latest(series('srf'));
  const repoWindow = d.repoTotal.slice(-21); const repoBase = repoWindow.length > 1 ? repoWindow.slice(0, -1).reduce((sum, p) => sum + p.value, 0) / (repoWindow.length - 1) : null;
  const repoSignal = Number.isFinite(repoBase) && d.repoTotal.at(-1).value > repoBase * 1.1;
  const gState = !spreadNow ? 'unknown' : spreadDays >= 3 && ((srfNow?.value || 0) > 0 || repoSignal) ? 'tight' : spreadNow.value <= 0 && !(srfNow?.value > 0) ? 'clear' : 'watch';
  const ois = series('ois_1y1y'); const oisNow = latest(ois); const tips = latest(d.tips20);
  let pState = 'unknown';
  if (ois.length > 20 && tips) { const ois20 = ois.at(-1).value - ois.at(-21).value; pState = ois20 > .05 && tips.value > .05 ? 'tight' : ois20 < -.05 && tips.value < -.05 ? 'loose' : 'split'; }
  return { q: qState, g: gState, p: pState, qPoint: q, spread: spreadNow, spreadDays, srf: srfNow, repoSignal, tips, oisNow, oisCount: ois.length };
}

function statusLabel(state) { return ({ loose: '已观察到扩张', tight: '已观察到收紧', flat: '尚未观察到方向', clear: '尚未观察到管道压力', watch: '证据待确认', split: '短长端分歧', unknown: '数据不足' })[state] || '待确认'; }
function statusClass(state) { return ['loose', 'clear'].includes(state) ? 'positive' : state === 'tight' ? 'negative' : state === 'unknown' ? 'pending' : 'watch'; }

function renderEvidence(d, state) {
  const specs = [
    { key: 'p', icon: '♨', title: 'P · 钱的价格', status: state.oisNow && state.p === 'unknown' ? '已有读数，方向待确认' : null, note: state.oisNow ? `1Y1Y SOFR 远期代理 ${nf(state.oisNow.value, 3)}%（${state.oisNow.time}）；目前累计 ${state.oisCount} 个观测。` : '短端预期路径缺失，长端不能替代整条曲线。', next: state.oisNow && state.p === 'unknown' ? '下一步：累计至少 20 个交易日后，再与 10Y 实际利率判断方向。' : '下一步：OIS 路径与 10Y 实际利率同向确认。' },
    { key: 'q', icon: '◆', title: 'Q · 钱的数量', note: state.qPoint ? `准备金＋ON RRP 四周净变化 ${nf(state.qPoint.value, 3)} T。` : '共同日期不足，暂不计算合计变化。', next: '下一步：看二阶变化和 ON RRP 缓冲。' },
    { key: 'g', icon: '⌁', title: 'g · 资金管道', note: state.spread ? `SOFR−IORB ${nf(state.spread.value, 1)} bp；连续正值 ${state.spreadDays} 个观测。` : '同日 SOFR 与 IORB 数据不足。', next: '下一步：必须由工具响应或 Repo 量价扩散确认。' },
  ];
  const root = $('evidence-summary'); root.replaceChildren();
  specs.forEach(spec => { const card = el('article', `evidence-card ${statusClass(state[spec.key])}`); card.append(el('span', 'factor-icon', spec.icon), el('h3', '', spec.title), el('strong', 'evidence-status', spec.status || statusLabel(state[spec.key])), el('p', '', spec.note), el('small', '', spec.next)); root.append(card); });
}

const PANELS = [
  { section: 'q', icon: '◆', title: 'Q · 钱的数量', intro: '先看水位，再看边际；Q 的变化二阶比静态水平更重要。', panels: [
    { id: 'q-level', title: '准备金、ON RRP 与有效流动性代理', unit: '万亿美元', lines: d => [{ name: '准备金', data: series('reserves'), color: '#315f8a' }, { name: 'ON RRP', data: series('on_rrp'), color: '#85a9c7' }, { name: '合计', data: d.qTotal, color: '#19252e', width: 3 }], what: '准备金是银行体系水位，ON RRP 是非银蓄水池；两者合计是本页的有效市场流动性代理。', how: '合计持续下降表示水量收缩；ON RRP 接近耗尽后，同样的抽水更直接消耗准备金。', mistake: '合计不是 M2，也不是社融；TGA 已通过负债端影响准备金，不能从合计里再扣一次。' },
    { id: 'q-momentum', title: '四周净变化与变化的变化', unit: '万亿美元', lines: d => [{ name: '四周净变化', data: d.q4w, color: '#315f8a', width: 3 }, { name: '二阶变化', data: d.qAccel, color: '#a97513' }], what: '四周净变化看抽水或灌水；二阶变化看抽水是否正在加速。', how: '净变化为负且二阶继续为负，收缩在加速；二阶转正只代表压力缓和。', mistake: '水平仍高但下降速度放缓，不等于已经重新宽松。' },
    { id: 'q-tga', title: 'TGA 财政吞吐', unit: '万亿美元', lines: d => [{ name: 'TGA 余额', data: series('tga'), color: '#315f8a', width: 3 }, { name: '周变化', data: d.tgaChange, color: '#a97513', scale: 'left' }], what: 'TGA 是美国财政部在联储的账户。本页使用 H.4.1 周三余额。', how: 'TGA 上升通常从市场吸走资金，下降通常向市场释放资金；应和发债结构一起看。', mistake: 'TGA 单周变化有强烈日历性，不能单独定义趋势。' },
    { id: 'q-buffer', title: 'ON RRP 缓冲与实际缩表代理', unit: '万亿美元', zero: true, lines: d => [{ name: 'ON RRP', data: series('on_rrp'), color: '#315f8a', width: 3 }, { name: 'Fed 总资产四周实际下降', data: d.qtActual, color: '#9d2933' }, { name: '海绵差值：ON RRP − 四周实际下降', data: d.rrpBuffer, color: '#24756f', width: 3 }], what: '用 Fed 总资产四周实际下降观察真实抽水节奏；再用 ON RRP 减去该抽水量，直接衡量海绵还剩多少。', how: '海绵差值为正，表示 ON RRP 仍覆盖近期四周实际抽水；等于或低于零，表示按这个代理口径缓冲已不足，新增缩表更可能直接落在准备金。', mistake: '这是实际缩表代理，不是计划 QT 上限；QT 停止或总资产受其他科目扰动时，差值不能机械解释。' },
  ]},
  { section: 'g', icon: '⌁', title: 'g · 资金管道', intro: '水量够不代表不缺氧。g 是阈值变量，要看价格、工具和非银渠道是否扩散。', panels: [
    { id: 'g-spread', title: 'SOFR − IORB', unit: '基点', zero: true, lines: d => [{ name: 'SOFR−IORB', data: d.spread, color: '#24756f', width: 3 }], what: '担保隔夜融资利率与银行准备金利率之差，反映回购融资相对准备金价格的压力。', how: '持续高于零值得关注，还要结合 Fed 回购投放和 Repo 量价确认。', mistake: '季末、缴税或发债缴款附近的一次跳升，不足以认定结构性恶化。' },
    { id: 'g-srf', title: 'Fed 回购投放（SRF 工具响应代理）', unit: '万亿美元', lines: () => [{ name: '回购投放余额', data: series('srf'), color: '#24756f', width: 3 }], what: 'FRED 的临时回购余额用于观察 Fed 是否向市场提供担保融资，作为 SRF 工具响应代理。', how: '从零星转为连续动用，才构成管道压力升级的证据。', mistake: '工具没有动用不能单独证明没有压力；本序列也不把 SRF 设定利率误当使用量。' },
    { id: 'g-repo', title: 'Repo 成交量与资金价格', unit: '量：万亿美元 / 价：%', lines: d => [{ name: 'Repo 总成交量', data: d.repoTotal, color: '#24756f', width: 3 }, { name: 'SOFR', data: series('sofr'), color: '#9d2933', scale: 'left' }], what: 'OFR 三类 Repo 成交量与 SOFR 上下对齐，观察资金量和融资价格是否共同变化。', how: '价格上行同时伴随结构或成交异常，比单看价格更接近扩散证据。', mistake: '成交量上升可能只是正常融资需求，不等于缺钱。' },
  ]},
  { section: 'p', icon: '♨', title: 'P · 钱的价格', intro: '短端看水平与预期路径，长端看实际利率和期限溢价的变化。', panels: [
    { id: 'p-ois', title: '美国 OIS 期限曲线（MacroMicro）', unit: '%', lines: d => [
      { name: '1M', data: series('ois_1m'), color: '#8d281f' }, { name: '3M', data: series('ois_3m'), color: '#b45b31' },
      { name: '6M', data: series('ois_6m'), color: '#c48a24' }, { name: '1Y', data: series('ois_1y'), color: '#24756f', width: 3 },
      { name: '2Y', data: series('ois_2y'), color: '#315f8a', width: 3 }, { name: '10Y', data: series('ois_10y'), color: '#65508f' },
      { name: '30Y', data: series('ois_30y'), color: '#776f67' }, { name: '短端路径中枢', data: d.oisShortCenter, color: '#19252e', width: 4 }],
      what: '隔夜指数掉期的固定端利率，按 1M、3M、6M、1Y、2Y、10Y、30Y 分列，反映市场对未来隔夜利率及期限补偿的定价。',
      reference: { label: 'MacroMicro · 美国隔夜指数掉期原图', url: 'https://sc.macromicro.me/charts/115044/us-overnight-indexed-swaps' },
      how: '短端路径中枢取 3M、6M、1Y、2Y 在共同观测日的等权平均。第一层要求它与 10Y 实际利率在同一个 20 个交易日窗口内同时上移，形成 2 项独立确认。',
      mistake: '这些是即期起息期限，并不等于 1Y1Y 远期 OIS；曲线倒挂或变陡也不能单独证明流动性宽松或紧张。' },
    { id: 'p-term-sofr', title: 'CME Term SOFR · 短端路径代理', unit: '%', lines: d => [
      { name: '1M Term SOFR', data: series('term_sofr_1m'), color: '#8d281f' },
      { name: '3M Term SOFR', data: series('term_sofr_3m'), color: '#b45b31' },
      { name: '6M Term SOFR', data: series('term_sofr_6m'), color: '#c48a24' },
      { name: '12M Term SOFR', data: series('term_sofr_1y'), color: '#315f8a', width: 3 },
      { name: '代理路径中枢', data: d.termSofrCenter, color: '#19252e', width: 4 }],
      what: 'CME 官方前瞻性 Term SOFR，提供 1M、3M、6M、12M 日度历史。直接 OIS 历史不足时，本页用四个期限的共同日期等权平均作为短端路径代理。',
      reference: { label: 'CME · Term SOFR 官方网页', url: 'https://www.cmegroup.com/market-data/cme-group-benchmark-administration/term-sofr.html' },
      webSnapshot: true,
      how: '代理中枢与 10Y 实际利率在同一个 20 个交易日窗口内都上移，才形成第一层的代理 2/2 确认；直接 OIS 数据充分且新鲜时会自动优先使用直接 OIS。',
      mistake: 'Term SOFR 由 SOFR 衍生品隐含预期生成，不是场外 OIS 平价掉期报价，也不能替代 2Y 以上的 OIS 曲线。' },
    { id: 'p-short', title: '短端利率与预期路径', unit: '%', lines: () => [{ name: 'IORB', data: series('iorb'), color: '#776f67' }, { name: 'SOFR', data: series('sofr'), color: '#9d2933', width: 3 }, { name: '1Y1Y SR3 代理', data: series('ois_1y1y'), color: '#315f8a' }], what: 'IORB 和 SOFR 是已实现短端；1Y1Y SR3 代理用于读取未来第 13—24 个月的预期中枢。', how: '预期路径与长端实际利率同向，P 的方向才更强。缺失时图表明确留空。', mistake: '降息动作或一天的期货跳动，不等于 P 已经转松。' },
    { id: 'p-real', title: '10Y 实际利率与 20 日变化', unit: '% / pct', lines: d => [{ name: '10Y 实际利率', data: series('tips_10y'), color: '#9d2933', width: 3 }, { name: '20 日变化', data: d.tips20, color: '#a97513', scale: 'left' }], what: '10Y TIPS 是长端实际贴现率；20 个交易日变化显示边际方向。', how: '水平决定约束底线，变化率决定边际压力。', mistake: '名义 10Y、CPI 或短端降息次数都不能替代实际利率。' },
    { id: 'p-curve', title: '收益率曲线、斜率与 10Y ACM 参考', unit: '% / bp', lines: d => [{ name: '2Y', data: series('dgs2'), color: '#9d2933' }, { name: '5Y', data: series('dgs5'), color: '#c26b70' }, { name: '10Y', data: series('dgs10'), color: '#315f8a', width: 3 }, { name: '30Y', data: series('dgs30'), color: '#19252e' }, { name: '2s10s', data: d.curve2s10s, color: '#24756f', scale: 'left' }, { name: '5s30s', data: d.curve5s30s, color: '#72a9a4', scale: 'left' }, { name: '10Y ACM 期限溢价', data: series('acm_10y'), color: '#a97513' }], what: '四个期限描述整条曲线；斜率用基点表示。10Y ACM 是模型估算的期限溢价参考。', how: '熊陡且期限溢价同步上升，才提示长端可能从增长定价切向财政供给定价。', mistake: '10Y ACM 不能冒充 30Y 期限溢价；财政主导仍需人工结合供给与通胀确认。' },
  ]},
];

function createChart(container, panel, d) {
  const chart = LightweightCharts.createChart(container, { autoSize: true, layout: { background: { type: 'solid', color: '#fffefb' }, textColor: '#6f6a63', fontSize: 11, attributionLogo: true }, grid: { vertLines: { color: '#eee9df' }, horzLines: { color: '#e7e0d5', style: 2 } }, rightPriceScale: { borderVisible: false }, leftPriceScale: { visible: true, borderVisible: false }, timeScale: { borderVisible: false, rightOffset: 1 }, crosshair: { vertLine: { color: '#aaa096' }, horzLine: { color: '#aaa096' } }, handleScroll: { vertTouchDrag: false }, localization: { locale: 'zh-CN' } });
  const lines = panel.lines(d); const created = [];
  lines.forEach((spec, index) => { const line = chart.addSeries(LightweightCharts.LineSeries, { color: spec.color, lineWidth: spec.width || 2, priceScaleId: spec.scale || 'right', priceLineVisible: false, lastValueVisible: true, crosshairMarkerRadius: 3, title: spec.name }); line.setData(spec.data); if (panel.zero && index === 0) line.createPriceLine({ price: 0, color: '#9d2933', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '零线' }); created.push({ line, spec }); });
  return { chart, created, panel, container };
}

function lastReading(panel, d) { const available = panel.lines(d).filter(line => line.data.length).map(line => `${line.name} ${nf(line.data.at(-1).value, line.name.includes('bp') || panel.unit === '基点' ? 1 : 3)}（${line.data.at(-1).time}）`); if (!available.length) return '数据不足，等待下一次有效更新。'; if (panel.id === 'q-buffer' && d.rrpBuffer.length) { const point = d.rrpBuffer.at(-1); return `${available.slice(0, 3).join(' · ')}。海绵判定：${point.value > 0 ? '正值，缓冲仍在' : '零或负值，缓冲不足'}。`; } return available.slice(0, 3).join(' · '); }
function firstLayerReading(d) {
  if (!d.firstLayer) return { state: 'pending', text: '数据不足：直接 OIS 或 CME Term SOFR 代理与 10Y 实际利率尚无同一窗口的 21 个新鲜共同观测，暂不能计算 20 个交易日变化。' };
  const oisUp = d.firstLayer.left > 0; const realUp = d.firstLayer.right > 0; const confirmations = Number(oisUp) + Number(realUp);
  const state = confirmations === 2 ? 'achieved' : 'not-achieved';
  const verdict = confirmations === 2 ? '已达成' : '未达成';
  const source = d.firstLayerSource === 'direct-ois' ? '直接 OIS' : 'CME Term SOFR 代理';
  return { state, text: `${verdict}（${confirmations}/2 项确认，来源：${source}）：${d.firstLayer.start} 至 ${d.firstLayer.end}，短端路径中枢 ${d.firstLayer.left >= 0 ? '+' : ''}${nf(d.firstLayer.left, 3)} pct；10Y 实际利率 ${d.firstLayer.right >= 0 ? '+' : ''}${nf(d.firstLayer.right, 3)} pct。` };
}
function renderDashboard(d) {
  const root = $('dashboard-sections'); root.replaceChildren(); chartViews.length = 0;
  PANELS.forEach(group => { const section = el('section', `factor-section factor-${group.section}`); const head = el('div', 'factor-heading'); head.append(el('span', 'factor-icon large', group.icon), el('div')); head.lastChild.append(el('p', 'eyebrow', `LAYER / ${group.section.toUpperCase()}`), el('h2', '', group.title), el('p', '', group.intro)); section.append(head);
    group.panels.forEach(panel => { const article = el('article', 'explain-panel'); const visual = el('div', 'panel-visual'); const heading = el('div', 'panel-title'); heading.append(el('div', '', panel.title), el('span', '', panel.unit)); const legend = el('div', 'panel-legend'); panel.lines(d).forEach(line => { const point = latest(line.data); const item = el('span', '', point ? `${line.name} ${nf(point.value, panel.unit === '基点' ? 1 : 3)} · ${point.time}` : `${line.name} · 暂无数据`); item.style.setProperty('--series-color', line.color); legend.append(item); }); const plot = el('div', 'explain-plot'); visual.append(heading, legend, plot); const explainer = el('aside', 'chart-explainer'); explainer.append(el('h3', '', '读图说明'), explanation('是什么', panel.what)); if (panel.webSnapshot) explainer.append(webSnapshotCard()); if (panel.reference) explainer.append(referenceLink(panel.reference)); explainer.append(explanation('怎么看', panel.how)); if (panel.id === 'p-ois') { const verdict = firstLayerReading(d); const row = explanation('第一层判定', verdict.text); row.className = `layer-verdict ${verdict.state}`; explainer.append(row); } explainer.append(explanation('本次变化', lastReading(panel, d)), explanation('不能据此认定', panel.mistake), formula(panel)); article.append(visual, explainer); section.append(article); chartViews.push(createChart(plot, panel, d)); }); root.append(section); });
  applyRange();
}
function explanation(label, value) { const p = el('p'); p.append(el('strong', '', `${label}：`), document.createTextNode(value)); return p; }
function referenceLink(reference) { const p = el('p'); const a = el('a', 'method-link', `${reference.label} ↗`); a.href = reference.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; p.append(el('strong', '', '参考链接：'), a); return p; }
function webSnapshotCard() { const card = el('div', 'layer-verdict pending'); if (!cmeWebSnapshot?.values) { card.append(el('strong', '', 'CME 网页快照：'), document.createTextNode('暂不可用，请通过下方官方链接查看。')); return card; } const values = cmeWebSnapshot.values; card.append(el('strong', '', `CME 网页快照 · ${cmeWebSnapshot.observed_date}`), el('p', '', `1M ${nf(values['1m'], 5)}% · 3M ${nf(values['3m'], 5)}% · 6M ${nf(values['6m'], 5)}% · 12M ${nf(values['12m'], 5)}%`), el('p', '', '仅作人工核对，不写入历史序列，也不参与中枢和第一层判定。')); return card; }
function formula(panel) { const details = el('details', 'formula'); details.append(el('summary', '', '展开计算口径'), el('p', '', panel.id === 'q-level' ? '只在准备金与 ON RRP 具有相同观测日期时相加。' : panel.id === 'q-buffer' ? '海绵差值 = 同一观测日 ON RRP − max（四周前 Fed 总资产 − 当前 Fed 总资产，0）。只使用共同日期。' : panel.id === 'g-spread' ? '(SOFR − IORB) × 100，单位为基点。' : panel.id === 'p-ois' ? '直接 OIS 中枢 =（3M + 6M + 1Y + 2Y）÷ 4；若直接数据不足，则使用 CME Term SOFR 代理中枢 =（1M + 3M + 6M + 12M）÷ 4。只使用各组四条曲线与 10Y 实际利率都有值的共同交易日；直接 OIS 新鲜且完整时优先。以第 21 个共同观测减第 1 个共同观测，得到 20 个交易日变化；两项变化都大于 0 才算 2/2 独立确认。' : panel.id === 'p-term-sofr' ? '代理路径中枢 =（1M + 3M + 6M + 12M CME Term SOFR）÷ 4，只在四个期限具有共同观测日时计算。' : panel.id === 'p-real' ? '当前值减 20 个有效交易日前的值。' : '原始频率保留；不对休市日或缺失日做前向填充。')); return details; }

function applyRange() { const days = RANGE_DAYS[currentRange]; chartViews.forEach(view => { const all = view.created.flatMap(x => x.spec.data); if (!all.length) return; const end = new Date(`${all.map(p => p.time).sort().at(-1)}T00:00:00Z`); if (Number.isFinite(days)) { const start = new Date(end); start.setUTCDate(start.getUTCDate() - days); view.chart.timeScale().setVisibleRange({ from: start.toISOString().slice(0, 10), to: end.toISOString().slice(0, 10) }); } else view.chart.timeScale().fitContent(); }); }

const COMBOS = [
  ['A', '全面宽松', { p: 'loose', q: 'loose', g: 'clear' }, '估值扩张主导；成长与久期受益，但低波＋高杠杆仍要警惕。'],
  ['B', '保守主义复位', { p: 'loose', q: 'tight', g: 'tight' }, '降息只是缓冲；缩短久期，向确定性现金流与链主收缩。'],
  ['C', 'PQ 双紧＋g 恶化', { p: 'tight', q: 'tight', g: 'tight' }, '美元与确定性资产虹吸；外圈、高杠杆和纯估值资产承压。'],
  ['D', '央行紧、财政灌', { p: 'loose', q: 'tight', g: 'clear' }, '行政对冲主导节奏；高敏感资产只适合条件式观察。'],
  ['E', '财政主导的通胀型', { p: 'tight', q: 'flat', g: 'tight' }, '长端熊陡与期限溢价主导；久期承压，黄金与硬资产相对受益。'],
  ['F', '隐性紧缩', { p: 'split', q: 'flat', g: 'tight' }, '表面平静、底层缺氧；低波不能用作安全证明。'],
  ['H', 'K 型流动性', { p: 'split', q: 'flat', g: 'watch' }, '总量中性但结构极端分化；按标的现金流与融资渠道选，不按指数选。'],
];
function renderCombinations(state) { const root = $('combination-grid'); root.replaceChildren(); COMBOS.forEach(([code, name, target, impact]) => { const matches = Object.keys(target).filter(k => state[k] === target[k]); const conflicts = Object.keys(target).filter(k => state[k] !== 'unknown' && state[k] !== target[k]); const pending = Object.keys(target).filter(k => state[k] === 'unknown'); const card = el('article', `combo-card ${matches.length >= 2 && !conflicts.length ? 'candidate' : ''}`); card.append(el('span', 'combo-code', code), el('h3', '', name), el('p', 'combo-state', `P ${labelShort(target.p)} / Q ${labelShort(target.q)} / g ${labelShort(target.g)}`), comboLine('支持', matches), comboLine('冲突', conflicts), comboLine('待补', pending), el('p', 'combo-impact', impact)); root.append(card); });
  const impact = $('asset-impact'); impact.replaceChildren(); impact.append(el('h3', '', '资产传导怎么读'), el('p', '', '若 Q 收缩并由 g 的工具响应确认，先看美元融资、信用和高波动资产；若 P 的长端由期限溢价抬升主导，实际利率和久期具有否决权。当前任一关键维度为“数据不足”时，只展示条件，不给仓位或买卖结论。'));
}
function labelShort(value) { return ({ loose: '松', tight: '紧', clear: '通', flat: '平', split: '分歧', watch: '分化' })[value] || '待核验'; }
function comboLine(label, keys) { const p = el('p', `combo-evidence ${label === '支持' ? 'support' : label === '冲突' ? 'conflict' : 'pending'}`); p.append(el('b', '', `${label}：`), document.createTextNode(keys.length ? keys.map(k => k.toUpperCase()).join('、') : '无')); return p; }

function renderChecklist(d, state) {
  const rows = [
    ['01', 'ON RRP 缓冲', latest(series('on_rrp')), '余额需与实际月度抽水量比较；QT 为零时比例不适用。'],
    ['02', '准备金＋工具响应', state.srf, '回购投放从零星转持续，才说明压力进入工具层。'],
    ['03', 'SOFR−IORB', state.spread, `连续正值 ${state.spreadDays} 个观测；单日跳升不能确认。`],
    ['04', '短端预期路径', latest(series('ois_1y1y')), '当前为 SR3 推导代理；累计至少 20 个交易日后再判断方向。'],
    ['05', '10Y 实际利率', latest(series('tips_10y')), state.tips ? `20 日变化 ${nf(state.tips.value)} pct。` : '20 日变化不足。'],
    ['06', '曲线＋期限溢价', latest(series('acm_10y')), '10Y ACM 仅参考；财政主导需人工确认。'],
    ['07', 'TGA＋发债结构', latest(series('tga')), 'TGA 有数据；Bill/Coupon 结构仍需结合财政看板人工判断。'],
    ['08', '波动与信用', null, 'MOVE、原油 IV、VIX 期限结构与高收益利差不在本页，保留人工核验。'],
  ]; const root = $('checklist'); root.replaceChildren(); rows.forEach(([num, name, point, note]) => { const item = el('article', 'check-item'); const stateText = point ? '已观察到' : '数据不足'; item.append(el('span', 'check-number', num), el('div', 'check-main'), el('span', `check-state ${point ? 'observed' : 'pending'}`, stateText)); item.querySelector('.check-main').append(el('h3', '', name), el('p', '', point ? `${nf(point.value, 3)} · ${point.time}。${note}` : note)); root.append(item); });
}

function renderSources() { const root = $('pqg-sources'); root.replaceChildren(); const all = { ...liquidity.series, ...liquidity.repo_series, tbill_events: liquidity.tbill_events }; Object.values(all).forEach(source => { const row = el('div', 'source-item'); const link = el('a', '', `${source.name} ↗`); link.href = source.source_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.append(link, el('div', '', `${source.source} · ${source.unit} · ${source.frequency}${source.proxy ? ' · 代理指标' : ''}`), el('div', '', `最新 ${source.last_date || '无数据'} · ${freshness(source)}`), el('div', '', source.note || ''), el('div', '', source.message || '')); root.append(row); }); }

function validate(data) { const value = data?.liquidity_pqg; if (!Number.isInteger(data?.schema_version) || data.schema_version < 2 || !value?.series) throw new Error('流动性快照格式不正确'); return value; }
async function load() { try { const [response, cmeResponse] = await Promise.all([fetch('./data.json', { cache: 'no-store', signal: AbortSignal.timeout(20000) }), fetch('./cme_term_sofr_snapshot.json', { cache: 'no-store', signal: AbortSignal.timeout(10000) }).catch(() => null)]); if (!response.ok) throw new Error(`HTTP ${response.status}`); const snapshot = await response.json(); cmeWebSnapshot = cmeResponse?.ok ? await cmeResponse.json() : null; liquidity = validate(snapshot); const d = prepareDerived(); const state = inferState(d); renderEvidence(d, state); renderDashboard(d); renderCombinations(state); renderChecklist(d, state); renderSources(); const generated = new Date(snapshot.generated_at).toLocaleString('zh-CN', { timeZone: 'Europe/Berlin', hour12: false }); $('pqg-update-status').textContent = `快照生成 ${generated} 柏林时间 · 最新观测 ${liquidity.as_of || '待核验'}`; $('pqg-error').hidden = true; } catch (error) { $('pqg-error').hidden = false; $('pqg-error').textContent = `读取流动性快照失败：${error.message}`; $('pqg-update-status').textContent = '数据读取失败'; } }

document.querySelectorAll('#range-control button').forEach(button => button.addEventListener('click', () => { currentRange = button.dataset.range; document.querySelectorAll('#range-control button').forEach(item => item.setAttribute('aria-pressed', String(item === button))); applyRange(); }));
try { if (!window.LightweightCharts) throw new Error('图表组件未加载'); load(); setInterval(() => { if (!document.hidden) load(); }, 15 * 60 * 1000); } catch (error) { $('pqg-error').hidden = false; $('pqg-error').textContent = error.message; }
