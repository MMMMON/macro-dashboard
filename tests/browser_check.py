"""Optional browser smoke test of built assets with local route fulfillment."""
import json
import mimetypes
from urllib.parse import urlparse
from pathlib import Path
from playwright.sync_api import sync_playwright

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
    page.route('http://macro-dashboard.test/**', serve)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://macro-dashboard.test/', wait_until='networkidle')
    page.wait_for_function("document.querySelector('#update-status').textContent.includes('快照生成')")
    assert page.locator('.chart-card').count() == 8
    assert page.locator('.plot canvas').count() >= 8
    assert page.locator('.plot-empty:visible').count() == 0
    assert not page.locator('#error-banner').is_visible()
    for label in ['1M', '3M', '1Y', 'ALL', '6M']:
        page.locator(f'button[data-range="{label}"]').click()
    page.locator('.legend button').first.click()
    assert page.locator('.legend button').first.get_attribute('aria-pressed') == 'false'
    page.locator('.legend button').first.click()
    page.screenshot(path=str(artifacts / 'dashboard-desktop.png'), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(artifacts / 'dashboard-mobile.png'), full_page=True)
    # A failed refresh must keep the previously rendered chart snapshot.
    page.route('**/data.json', lambda route: route.fulfill(status=503, body='offline'))
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelector('#error-banner').textContent.includes('继续显示')")
    assert page.locator('.plot-empty:visible').count() == 0
    page.unroute('**/data.json')
    # Initial failure must give an honest empty state and recover on retry.
    page.route('**/data.json', lambda route: route.fulfill(status=404, body='missing'))
    page.reload(wait_until='networkidle')
    assert page.locator('#error-banner').is_visible()
    assert page.locator('.plot-empty:visible').count() == 8
    page.unroute('**/data.json')
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelector('#update-status').textContent.includes('快照生成')")
    assert not errors, errors
    print(json.dumps({"panels": 8, "desktop": "pass", "mobile": "pass", "refresh_failure": "pass", "initial_failure_recovery": "pass", "console_errors": errors}))
    browser.close()
