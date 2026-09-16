# Macro Atlas · 全球宏观看板

网站：https://macro-dashboard-57g.pages.dev/ · 仓库：https://github.com/MMMMON/macro-dashboard

纯 HTML5、Vanilla JavaScript、Tailwind CSS、TradingView Lightweight Charts。
Python 抓取真实数据，GitHub Actions 每天更新 `data.json`，Cloudflare Pages 托管静态页面。浏览器不持有密钥，也不直接请求上游数据服务。

## 文件

```text
index.html                    页面、8 个主题入口与口径说明
main.js                       图表初始化、双轴、时间范围、图例和异常状态
styles.css                    Tailwind 输入与自定义样式
assets/styles.css             已编译样式
data.json                     首次真实抓取的完整数据快照
fetch_data.py                 Yahoo / FRED / 中债抓取、清洗与容错
requirements.txt              Python 依赖
package.json / package-lock.json  样式与图表依赖、可重复安装
build.mjs                     仅将公开网页资源复制到 public/
_headers                      Cloudflare 缓存和浏览器安全响应头
.github/workflows/update.yml   每日自动更新
tests/test_fetch_data.py       数据清洗、容错与不变文件测试
tests/browser_check.py         桌面、移动端与错误恢复检查
```

## 本地运行

需要 Python 3.12、Node.js 22 或更新的兼容版本。

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python fetch_data.py
npm ci
npm run build
python -m http.server 8080 --directory public
```

浏览器打开 http://localhost:8080。不要直接双击 HTML：浏览器会限制 `file://` 下读取 JSON。
Windows 若虚拟环境路径包含中文，curl 可能无法读取 CA 文件；可把虚拟环境建在英文路径，或设置 `YFINANCE_CA_BUNDLE` 为可信 CA bundle 的英文绝对路径。不要关闭 TLS 证书验证。

## GitHub 与 Cloudflare Pages

本文件夹应成为独立 GitHub 仓库的根目录，建议仓库名 `MMMMON/macro-dashboard`。不要上传 `.venv`、`node_modules` 或其他个人目录。

1. 将项目文件推送到仓库默认分支 `main`。Actions 只从默认分支运行定时任务。
2. 在 Cloudflare 控制台选择 **Workers & Pages → Create application → Pages → Connect to Git**，授权 GitHub 应用访问该仓库。
3. 创建 Pages 项目，使用以下设置：

| 设置 | 值 |
| --- | --- |
| 项目名 | macro-dashboard（若重名，使用 mmmon-macro-dashboard） |
| GitHub 仓库 | MMMMON/macro-dashboard |
| Production branch | main |
| Framework preset | None |
| Root directory | 留空，即仓库根目录 |
| Build command | npm ci && npm run build |
| Build output directory | public |
| Environment variable | NODE_VERSION=22 |
| Automatic production deployments | 开启 |

构建时只编译样式和复制静态文件，不在 Cloudflare 构建过程中请求金融数据。

4. GitHub 仓库 **Settings → Actions → General → Workflow permissions** 允许读写内容；工作流也显式声明 `contents: write`。若组织规则或主分支保护禁止机器人直接推送，需允许该工作流的提交，否则更新任务会失败。
5. 在 **Actions → Update macro data → Run workflow** 手动运行一次，检查抓取摘要、提交记录及 Cloudflare 部署是否完成。

### 每日更新时间

`cron: '17 0 * * *'` = UTC 00:17 = 北京时间 08:17，每天包含周末执行。首次上传和修改抓取脚本、依赖或工作流时也会触发一次更新（push 规则默认使用 main；如改名请同步修改）。GitHub 定时任务可能延迟；这不是实时行情系统。公开仓库连续 60 天无活动时，GitHub 可能停用定时任务，需在 Actions 中重新启用。

`data.json` 有变化才提交；仅运行时间变化不会制造提交。Cloudflare Git 集成监听仓库提交并自动部署。`GITHUB_TOKEN` 推送不会触发另一个 GitHub Actions push 工作流，因此本项目不依赖第二个 push 部署工作流，而由 Cloudflare Git 集成执行构建。

## 数据源和可视化

| 图表 | 数据和坐标 |
| --- | --- |
| BTC / ETH | BTC-USD 左轴、ETH-USD 右轴，USD |
| 中美国债与原油 | DGS10 与中债 10Y 左轴（%）、CL=F 右轴（USD/桶） |
| 美股标普 | ^GSPC，指数点 |
| 黄金与美元 | GC=F 左轴（USD/盎司）、DX-Y.NYB 右轴（点） |
| M7 | AAPL/MSFT/NVDA/AMZN/GOOGL/META/TSLA，区间共同首日=100 |
| 全球股指 | ^GSPC/^IXIC/^GDAXI/^N225/^HSI/000001.SS，本币指数共同首日=100 |
| 实际利率与通胀预期 | DFII10、T10YIE，共用百分比轴 |
| 美元指数 | DX-Y.NYB，指数点；与黄金图共享同一份数据 |

默认保留最近 1095 个自然日。`python fetch_data.py --days 1825` 可调整历史长度。

Yahoo 显式使用 `auto_adjust=True` 的复权收盘价。M7 展示七条独立曲线，不冒充官方 M7 指数；全球指数按本币口径归一，不代表统一币种的投资回报。黄金、原油是连续近月期货，换月可能跳变，非现货报价。T10YIE 为市场隐含盈亏平衡通胀率，非已公布 CPI。

全部日期为 `YYYY-MM-DD`，数值去除 NaN/Infinity，按日去重升序，保留合法负利率。排除当前 UTC 日期，避免把尚未结束的日线当作收盘价。节假日不前向填充。每个指标独立记录最新观测时间，不能用整个文件生成时间代表数据实时性。

### FRED API

`fetch_fred_series(series_id, start, end, api_key=None)` 已完整实现官方 API 接口：

```python
fetch_fred_series("DGS10", date(2025, 1, 1), date(2026, 1, 1), api_key="YOUR_KEY")
```

可将密钥存到 **GitHub Settings → Secrets and variables → Actions → New repository secret → FRED_API_KEY**。配置密钥后优先使用 FRED API，失败时使用美国财政部官方 XML 备用源。没有密钥时优先使用财政部 XML，失败时尝试 FRED 公开 CSV。`fetch_fred_series` 单独调用且无密钥时仍读取 FRED CSV。密钥不写进 `data.json` 或前端；错误日志不输出携带密钥的请求 URL。

财政部使用 `BC_10YEAR`（名义）和 `TC_10YEAR`（实际）日度收益率；通胀盈亏平衡率按共同日期的名义减实际收益率计算，不将它标为直接下载的 FRED T10YIE。切换来源会替换完整历史并更新来源标签，不拼接不同来源；缓存保留原始来源信息。该备用源解决实测 GitHub 托管运行器无法下载 FRED CSV 的问题，无需额外 API 密钥。

中国国债直接读取中国债券信息网国债收益率曲线的 10 年期限日数据，按不足一年的日期窗口请求，无需虚构 FRED 中国系列，也不以美债数据替代。

### 故障处理

HTTP 请求有超时和重试。空值、历史截断、日期倒退视为失败，保留上次有效历史并标记 `cached`；首次抓取失败使用空数组和 `unavailable`。超过 7 天未更新的普通日度指标、超过 3 天未更新的加密资产显示延迟。前端重新根据当前日期检查延迟，页面刷新失败保留当前图表。

全部数据源失败时脚本返回非零退出码且不覆盖原文件。部分失败会继续发布有效数据，在 Actions 摘要及网页提示。写入采用临时文件原子替换，并禁止非有限 JSON 数值。脚本保留快照生成时间仅在内容或状态变动时更新；页面每 15 分钟重新读取已生成的静态快照。

## JSON 结构

```json
{
  "schema_version": 1,
  "history_days": 1095,
  "price_basis": "Yahoo adjusted daily close; no forward fill; current UTC day excluded",
  "generated_at": "2026-09-16T00:17:00+00:00",
  "series": {
    "us10y": {
      "name": "美国 10Y 国债收益率",
      "symbol": "DGS10",
      "unit": "%",
      "frequency": "daily",
      "source": "FRED",
      "source_url": "https://fred.stlouisfed.org/series/DGS10",
      "note": "工作日发布；收益率单位为百分比",
      "status": "ok",
      "stale": false,
      "last_date": "2026-09-14",
      "message": null,
      "data": [{"time": "2026-09-14", "value": 4.0}]
    }
  }
}
```

上面仅为结构示例，示例值不是行情；项目中的 `data.json` 来自实际抓取，包含完整 22 个指标。

## 检查

```sh
python -m unittest discover -s tests -v
node --check main.js
npm run build
```

可选浏览器检查需要额外安装 Playwright，构建完成后执行 `python tests/browser_check.py`。测试通过 Playwright 路由加载本地构建产物，不依赖外部网络；正式上线后还应检查线上 HTTP 响应及自动部署链路。

## 官方参考

- [Cloudflare Pages 静态 HTML](https://developers.cloudflare.com/pages/framework-guides/deploy-anything/)
- [Cloudflare GitHub 集成](https://developers.cloudflare.com/pages/configuration/git-integration/github-integration/)
- [GitHub 定时工作流](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [FRED observations API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [美国财政部官方 XML 数据](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed)
- [Yahoo 数据参数](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
- [中国债券信息网](https://yield.chinabond.com.cn/)
- [Lightweight Charts](https://tradingview.github.io/lightweight-charts/)

页面保留 TradingView 的图表署名、链接以及随构建输出的开源许可证。公开使用上游金融数据时请遵守各数据提供方的使用条款。
