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
    assert page.locator('.explain-panel').count() == 10
    assert page.locator('.explain-plot canvas').count() >= 10
    assert page.locator('.chart-explainer').count() == 10
    assert page.locator('.combo-card').count() == 7
    assert page.locator('.check-item').count() == 8
    assert page.locator('.concept-gate').inner_text().find('不是一回事') >= 0
    assert '数据不足' in page.locator('.evidence-card').first.inner_text()
    buffer_panel = page.locator('.explain-panel').filter(has_text='ON RRP 缓冲与实际缩表代理')
    assert '海绵判定' in buffer_panel.locator('.chart-explainer').inner_text()
    assert '海绵差值：ON RRP − 四周实际下降' in buffer_panel.locator('.panel-legend').inner_text()
    for label in ['1M', '3M', '6M', '1Y', 'ALL']:
        page.locator(f'#range-control button[data-range="{label}"]').click()
    page.screenshot(path=str(artifacts / 'liquidity-desktop.png'), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(artifacts / 'liquidity-mobile.png'), full_page=True)
    print(json.dumps({"panels": 8, "desktop": "pass", "mobile": "pass", "refresh_failure": "pass", "initial_failure_recovery": "pass", "console_errors": errors}))
    browser.close()
