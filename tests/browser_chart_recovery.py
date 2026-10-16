"""Exercise chart-script outages with the deployed security policy enabled."""
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
public = root / 'public'
csp = next(line.strip().split(': ', 1)[1] for line in (root / '_headers').read_text().splitlines()
           if line.strip().startswith('Content-Security-Policy:'))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for scenario in ['first-failure', 'persistent-failure', 'empty-script']:
        context = browser.new_context()
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        attempts = [0]
        blocked = [True]

        def serve(route):
            path = urlparse(route.request.url).path
            if path == '/liquidity-runtime.js':
                attempts[0] += 1
                if blocked[0] and (scenario == 'persistent-failure' or attempts[0] == 1):
                    if scenario == 'empty-script':
                        route.fulfill(status=200, content_type='application/javascript', body='/* empty cached script */')
                    else:
                        route.abort('failed')
                    return
            target = public / ('liquidity.html' if path == '/liquidity' else path.lstrip('/'))
            if not target.is_file() or not target.resolve().is_relative_to(public.resolve()):
                route.fulfill(status=404, body='missing')
                return
            route.fulfill(status=200, body=target.read_bytes(),
                          content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream',
                          headers={'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff'})

        page.route('http://dashboard.test/**', serve)
        page.route('https://macro-dashboard-hub.pages.dev/manual-refresh.js',
                   lambda r: r.fulfill(content_type='application/javascript', body='window.manualDashboardRefresh = () => {};'))
        page.goto('http://dashboard.test/liquidity', wait_until='networkidle')
        if scenario == 'persistent-failure':
            expect(page.locator('#retry-dashboard')).to_be_visible()
            assert attempts[0] >= 3, attempts
            assert page.locator('.explain-plot canvas').count() == 0
            blocked[0] = False
            page.locator('#retry-dashboard').click()
        expect(page.locator('#scenario-table-body tr')).to_have_count(8)
        expect(page.locator('.evidence-card')).to_have_count(3)
        expect(page.locator('.explain-panel')).to_have_count(13)
        expect(page.locator('#pqg-error')).to_be_hidden()
        assert page.locator('.explain-plot canvas').count() >= 13
        assert page.evaluate('typeof window.manualDashboardRefresh') == 'function'
        assert not errors, errors
        print(json.dumps({'scenario': scenario, 'script_requests': attempts[0], 'result': 'pass'}))
        context.close()
    browser.close()
