"""Optional browser smoke test of built assets with local route fulfillment."""
import json
import mimetypes
import os
from urllib.parse import urlparse
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
artifacts.mkdir(exist_ok=True)
public = artifacts.parent / 'public'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-proxy-server'])
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
    def serve(route):
        requested = urlparse(route.request.url).path.lstrip('/') or 'index.html'
        path = (public / requested).resolve()
        if not path.is_relative_to(public.resolve()) or not path.is_file():
            route.fulfill(status=404, body='missing')
            return
        route.fulfill(status=200, body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
    live_url = os.getenv('DASHBOARD_URL')
    if not live_url:
        page.route('http://macro-dashboard.test/**', serve)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_url or 'http://macro-dashboard.test/', wait_until='networkidle')
    expect(page.locator('#update-status')).to_contain_text('自动任务运行')
    assert page.locator('.chart-card').count() == 8
    assert page.locator('.plot canvas').count() >= 8
    assert page.locator('.plot-empty:visible').count() == 0
    assert not page.locator('#error-banner').is_visible()
    for label in ['1M', '3M', '1Y', 'ALL', '6M']:
        page.locator(f'button[data-range="{label}"]').click()
    page.locator('.legend button').first.click()
    assert page.locator('.legend button').first.get_attribute('aria-pressed') == 'true'
    assert page.locator('.chart-card').first.evaluate("node => node.classList.contains('has-focus')")
    page.locator('.legend button').first.click()
    assert page.locator('.legend button').first.get_attribute('aria-pressed') == 'false'
    page.screenshot(path=str(artifacts / 'dashboard-desktop.png'), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(artifacts / 'dashboard-mobile.png'), full_page=True)
    # A failed refresh must keep the previously rendered chart snapshot.
    page.route('**/data.json', lambda route: route.fulfill(status=503, body='offline'))
    page.locator('#refresh').click()
    expect(page.locator('#error-banner')).to_contain_text('继续显示')
    assert page.locator('.plot-empty:visible').count() == 0
    page.unroute('**/data.json')
    # Initial failure must give an honest empty state and recover on retry.
    page.route('**/data.json', lambda route: route.fulfill(status=404, body='missing'))
    page.reload(wait_until='networkidle')
    assert page.locator('#error-banner').is_visible()
    assert page.locator('.plot-empty:visible').count() == 8
    page.unroute('**/data.json')
    page.locator('#refresh').click()
    expect(page.locator('#update-status')).to_contain_text('自动任务运行')
    assert not errors, errors
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.goto((live_url.rstrip('/') + '/liquidity.html') if live_url else 'http://macro-dashboard.test/liquidity.html', wait_until='networkidle')
    expect(page.locator('#pqg-update-status')).to_contain_text('快照生成')
    assert page.locator('.factor-section').count() == 3
    liquidity_panel_count = page.locator('.explain-panel').count()
    assert liquidity_panel_count == 13, {"panels": liquidity_panel_count, "errors": errors}
    assert page.locator('.explain-plot canvas').count() >= 13
    assert page.locator('.chart-explainer').count() == 13
    ois_panel = page.locator('.explain-panel').filter(has_text='美国 OIS 历史期限曲线 · MacroMicro')
    expect(ois_panel.locator('.ois-history-status')).to_contain_text('历史覆盖')
    assert '1Y' in ois_panel.locator('.panel-legend').inner_text()
    assert '30Y' in ois_panel.locator('.panel-legend').inner_text()
    assert ois_panel.locator('.ois-term-card').count() == 1
    assert ois_panel.locator('.ois-term-line').count() == 3
    assert ois_panel.locator('.ois-term-legend span').count() == 3
    page.wait_for_timeout(400)
    ois_panel.screenshot(path=str(artifacts / 'ois-history-desktop.png'))
    assert page.locator('.combo-card').count() == 7
    assert page.locator('.check-item').count() == 8
    assert page.locator('.concept-gate').inner_text().find('不是一回事') >= 0
    assert page.locator('.evidence-card').count() == 3
    buffer_panel = page.locator('.explain-panel').filter(has_text='ON RRP 缓冲与实际缩表代理')
    assert '海绵判定' in buffer_panel.locator('.chart-explainer').inner_text()
    assert '海绵差值：ON RRP − 四周实际下降' in buffer_panel.locator('.panel-legend').inner_text()
    corridor_panel = page.locator('.explain-panel').filter(has_text='联储操作利率走廊')
    assert corridor_panel.locator('.corridor-row').count() == 4
    assert 'ON RRP 利率' in corridor_panel.locator('.panel-legend').inner_text()
    assert 'SOFR' in corridor_panel.locator('.panel-legend').inner_text()
    assert '贴现率（主要信贷）' in corridor_panel.locator('.panel-legend').inner_text()
    for label in ['1M', '3M', '6M', '1Y', 'ALL']:
        page.locator(f'#range-control button[data-range="{label}"]').click()
    page.screenshot(path=str(artifacts / 'liquidity-desktop.png'), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(artifacts / 'liquidity-mobile.png'), full_page=True)
    # The production-only licensed series are represented by an explicit browser fixture;
    # it validates rendering and interactions without writing synthetic values to data.json.
    fixture = json.loads((public / 'data.json').read_text(encoding='utf-8'))
    days = [f'2026-09-{day:02d}' for day in range(1, 29)]
    def series(name, offset=0, frequency='daily', display=None):
        item = {'name': name, 'symbol': name, 'unit': '%', 'frequency': frequency,
                'source': 'Browser test fixture', 'source_url': 'https://example.test/',
                'status': 'ok', 'stale': False, 'last_date': days[-1],
                'message': None, 'note': '仅供浏览器渲染测试',
                'data': [{'time': day, 'value': 2.0 + offset + index * .01} for index, day in enumerate(days)]}
        if display: item['display'] = display
        return item
    european_series = {}
    for country_index, country in enumerate(['fr', 'de', 'it', 'es', 'gr']):
        for tenor_index, tenor in enumerate(['3m', '1y', '5y', '10y', '30y']):
            frequency = 'event' if country == 'gr' and tenor == '1y' else 'monthly' if country == 'gr' and tenor == '30y' else 'daily'
            item = series(f'{country} {tenor}', country_index * .2 + tenor_index * .1, frequency,
                          'markers' if frequency == 'event' else None)
            if frequency == 'event': item['data'] = item['data'][::9]
            if frequency == 'monthly': item['data'] = item['data'][::20]
            european_series[f'{country}_{tenor}'] = item
    european_series['eu_aaa_10y'] = series('Euro AAA 10Y', .4)
    spreads = {key: {**series(label, index * .1), 'unit': 'bp'} for index, (key, label) in enumerate([
        ('fr_de_10y', 'FR-DE'), ('it_de_10y', 'IT-DE'), ('gr_de_10y', 'GR-DE'),
        ('es_de_10y', 'ES-DE'), ('us_eu_aaa_10y', 'US-EU')])}
    fixture['european_bonds'] = {'state': 'success', 'as_of': days[-1], 'issues': [],
                                  'series': european_series, 'spreads': spreads}
    page.route('http://macro-dashboard.test/data.json', lambda route: route.fulfill(
        status=200, body=json.dumps(fixture), content_type='application/json'))
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.goto('http://macro-dashboard.test/european-bonds.html', wait_until='networkidle')
    expect(page.locator('#euro-update')).to_contain_text('快照生成')
    assert page.locator('.euro-card').count() == 6
    assert page.locator('.euro-plot canvas').count() >= 6
    assert page.locator('.euro-empty:visible').count() == 0
    assert '1Y拍卖点' in page.locator('.euro-card').filter(has_text='希腊国债收益率曲线').inner_text()
    for label in ['3M', '1Y', '3Y']:
        page.locator(f'#euro-range button[data-range="{label}"]').click()
    page.locator('.euro-legend button').first.click()
    assert page.locator('.euro-legend button').first.get_attribute('aria-pressed') == 'true'
    page.screenshot(path=str(artifacts / 'european-bonds-desktop.png'), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(artifacts / 'european-bonds-mobile.png'), full_page=True)
    assert not errors, errors
    print(json.dumps({"panels": 8, "desktop": "pass", "mobile": "pass", "refresh_failure": "pass", "initial_failure_recovery": "pass", "console_errors": errors}))
    browser.close()
