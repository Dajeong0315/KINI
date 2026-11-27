"""Opt-in, read-only browser check using the existing synthetic demo account."""
import json
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.getenv('KINI_LIVE_DEMO_FILE'), reason='protected synthetic demo credential required')
def test_deployed_reports_reload():
    from playwright.sync_api import sync_playwright
    credential = json.loads(Path(os.environ['KINI_LIVE_DEMO_FILE']).read_text())
    url = os.environ['KINI_LIVE_URL']
    expected = os.getenv('KINI_LIVE_EXPECTED_KCAL', '198.5')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.set_default_timeout(35000)
        errors = []
        page.on('pageerror', lambda error: errors.append(type(error).__name__))

        def login():
            page.get_by_label('로그인 이름', exact=True).fill(credential['login_name'])
            page.get_by_label('비밀번호', exact=True).fill(credential['password'])
            page.get_by_label('가구 ID', exact=True).fill(str(credential['household_id']))
            page.get_by_role('button', name='로그인', exact=True).click()
            page.locator('body[aria-busy=false]').wait_for()

        page.goto(url + '/mobile')
        login()
        page.locator('[data-view-link=report]:visible').first.click()
        page.get_by_role('heading', name='저장된 식사 이력', exact=True).wait_for()
        report = page.locator('[data-view=report]')
        assert '0.5인분' in report.inner_text()
        assert expected in report.inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('button', name='기록 상세', exact=True).first.click()
        page.get_by_role('dialog').wait_for()
        assert '0.5인분' in page.get_by_role('dialog').inner_text()
        assert expected in page.get_by_role('dialog').inner_text()
        output = Path(os.environ['KINI_LIVE_DEMO_FILE']).parent / 'step3-mobile-detail.png'
        page.screenshot(path=str(output), full_page=True)
        page.reload()
        login()
        page.get_by_role('heading', name='저장된 식사 이력', exact=True).wait_for()
        assert '0.5인분' in report.inner_text()
        assert not errors
        browser.close()
