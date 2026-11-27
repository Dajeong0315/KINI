"""Opt-in positive UI test, only on agent-created synthetic meal record 1."""
import json
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.getenv('KINI_LIVE_DEMO_FILE'), reason='protected synthetic credential required')
def test_deployed_shared_feedback_edit_and_reconnect():
    from playwright.sync_api import sync_playwright
    credential = json.loads(Path(os.environ['KINI_LIVE_DEMO_FILE']).read_text())
    url = os.environ['KINI_LIVE_URL']
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        page = browser.new_page(viewport={'width':390,'height':844})
        page.set_default_timeout(35000)
        errors = []
        page.on('pageerror', lambda error: errors.append(type(error).__name__))

        def login():
            assert page.get_by_label('비밀번호', exact=True).count() == 1
            page.get_by_label('로그인 이름', exact=True).fill(credential['login_name'])
            page.get_by_label('비밀번호', exact=True).fill(credential['password'])
            page.get_by_label('가구 ID', exact=True).fill(str(credential['household_id']))
            page.get_by_role('button', name='로그인', exact=True).click()
            page.locator('body[aria-busy=false]').wait_for()

        records = page.get_by_role('heading', name='최근 가족 식사 기록', exact=True).or_(
            page.get_by_role('heading', name='저장된 식사 이력', exact=True))

        def open_feedback():
            card = page.locator('[data-view=report] .scenario-log').filter(has_text='기록 ID 1')
            row = page.locator('.family-meals button[data-record-id="1"]')
            action = row.or_(card.get_by_role('button', name='식후 평가', exact=True))
            action.click()
            if row.count():
                page.get_by_role('dialog').get_by_role('button', name='식후 평가', exact=True).click()
            page.get_by_role('heading', name='식후 평가 수정', exact=True).wait_for()
            page.locator('body[aria-busy=false]').wait_for()

        def save_taste(label):
            page.get_by_role('dialog').locator('section[aria-label="맛"]').get_by_role('button', name=label, exact=True).click()
            with page.expect_response(lambda response: response.request.method == 'PUT' and response.url.endswith('/feedback')) as saved:
                page.get_by_role('button', name='평가 수정 저장', exact=True).click()
            assert saved.value.status == 200
            assert saved.value.json()['scope'] == 'meal'
            page.get_by_role('heading', name='평가 저장 완료', exact=True).wait_for()
            page.locator('#dialog-close').click()
            return saved.value.json()

        page.goto(url + '/mobile')
        login()
        page.locator('[data-view-link=report]:visible').first.click()
        records.wait_for()
        open_feedback()
        negative = save_taste('별로예요')
        open_feedback()
        positive = save_taste('맛있어요')
        assert positive['revision'] == negative['revision'] + 1
        page.reload()
        login_field = page.get_by_label('로그인 이름', exact=True)
        records.or_(login_field).wait_for()
        if login_field.is_visible():
            login()
        records.wait_for()
        open_feedback()
        assert page.get_by_role('dialog').locator('section[aria-label="맛"]').get_by_role('button', name='맛있어요', exact=True).get_attribute('aria-pressed') == 'true'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
        page.screenshot(path=str(Path(os.environ['KINI_LIVE_DEMO_FILE']).parent / 'step6-feedback.png'), full_page=True)
        browser.close()
