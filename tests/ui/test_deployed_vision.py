"""Opt-in read-only production UI check. Never creates or confirms inventory."""
import hashlib
import json
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.getenv('KINI_LIVE_DEMO_FILE'), reason='protected synthetic login required')
def test_deployed_vision_screen_reload():
    from playwright.sync_api import sync_playwright
    root = Path(__file__).resolve().parents[2]
    credentials = json.loads(Path(os.environ['KINI_LIVE_DEMO_FILE']).read_text(encoding='utf-8'))
    base = os.environ['KINI_LIVE_URL']
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width':390, 'height':844})
        page.set_default_timeout(35000)
        errors = []
        page.on('pageerror', lambda error: errors.append(type(error).__name__))
        writes = []
        page.on('request', lambda req: writes.append(req.url) if req.method not in ('GET','HEAD') and not req.url.endswith('/api/auth/login') else None)
        def sign_in():
            page.get_by_label('로그인 이름', exact=True).fill(credentials['login_name'])
            page.get_by_label('비밀번호', exact=True).fill(credentials['password'])
            page.get_by_label('가구 ID', exact=True).fill(str(credentials['household_id']))
            with page.expect_response(lambda res: res.url.endswith('/api/auth/login')) as response:
                page.get_by_role('button', name='로그인', exact=True).click()
            assert response.value.status == 200
            page.locator('body[aria-busy=false]').wait_for()
        page.goto(base + '/mobile')
        sign_in()
        page.locator('[data-view-link=inventory]:visible').first.click()
        page.get_by_role('heading', name='인식 확인 대기', exact=True).wait_for()
        with page.expect_response(lambda res: res.url.endswith('/api/data/inventory')) as response:
            page.get_by_role('button', name='재고 새로고침', exact=True).click()
        assert response.value.status == 200
        body = response.value.json()
        assert isinstance(body['pending_events'], list)
        assert isinstance(body['pending_vision_count'], int)
        assert all(event['source'] == 'yolo' and event['status'] == 'pending_confirmation' for event in body['pending_events'])
        for filename in ('app.js', 'api_client.js'):
            source = page.request.get(base + '/static/' + filename)
            assert source.status == 200
            assert hashlib.sha256(source.body()).digest() == hashlib.sha256((root / 'app/static' / filename).read_bytes()).digest()
        page.reload()
        login_field = page.get_by_label('로그인 이름', exact=True)
        pending = page.get_by_role('heading', name='인식 확인 대기', exact=True)
        pending.or_(login_field).wait_for()
        if login_field.is_visible():
            sign_in()
        pending.wait_for()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors and not writes
        browser.close()
