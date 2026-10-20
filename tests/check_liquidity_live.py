"""Verify the real deployment, including script headers and browser execution."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

url = os.getenv('DASHBOARD_URL', 'https://macro-dashboard-57g.pages.dev/liquidity')
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, failures, scripts = [], [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.on('requestfailed', lambda r: failures.append({"url": r.url, "error": r.failure}))
    page.on('response', lambda r: scripts.append({"url": r.url, "status": r.status,
            "type": r.headers.get('content-type')}) if r.request.resource_type == 'script' else None)
    page.goto(url, wait_until='networkidle', timeout=60000)
    page.wait_for_timeout(1000)
    state = page.evaluate('''() => ({library: typeof window.LightweightCharts,
        panels: document.querySelectorAll('.explain-panel').length,
        matrixRows: document.querySelectorAll('#scenario-table-body tr').length,
        error: document.querySelector('#pqg-error')?.textContent,
        errorVisible: !document.querySelector('#pqg-error')?.hidden})''')
    print(json.dumps({"url": page.url, "state": state, "scripts": scripts,
                      "errors": errors, "failed_requests": failures}, ensure_ascii=False, indent=2))
    assert state['library'] == 'object' and state['panels'] == 13 and not state['errorVisible'], state
    assert state['matrixRows'] == 8, state
    assert page.evaluate('typeof window.manualDashboardRefresh') == 'function'
    assert not errors, errors
    panel = page.locator('.explain-panel').filter(has_text='美国 OIS 历史期限曲线 · MacroMicro')
    assert '历史覆盖 2024-01-02' in panel.locator('.ois-history-status').inner_text()
    artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    panel.screenshot(path=str(artifacts / 'ois-history-live.png'))
    for label in ['1M', '3M', '6M', '1Y', 'ALL']:
        page.locator(f'#range-control button[data-range="{label}"]').click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors, errors
    browser.close()
