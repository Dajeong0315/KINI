"""Real local HTTP + Chrome E2E; AI/nutrition/chat are deterministic test doubles.
Run: python -m pip install playwright; python -m pytest tests/ui/test_live_flow.py -q
"""
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'flow.sqlite3'))
    monkeypatch.setenv('KINI_USE_OPENAI', '0')
    import app.main as main
    from app.services.ai import recommendation_chain
    from app.services.ai.openai_client import FakeOpenAIRecommendationClient, OpenAIClientError
    mode = {'error': False}
    class Fake:
        def explain(self, payload):
            if mode['error']:
                raise OpenAIClientError('test failure')
            return FakeOpenAIRecommendationClient().explain(payload)
    monkeypatch.setattr(recommendation_chain, 'configured_recommendation_client', lambda: Fake())
    monkeypatch.setattr(main, '_answer_question', lambda text: {'answer': '로컬 채팅 테스트 응답', 'question': text})
    monkeypatch.setattr(main, 'nutrition_for_food', lambda name: {'food_name': name, 'energy_kcal': 101, 'protein_g': 5, 'sodium_mg': 12, 'source': '테스트 영양 응답', 'serving_size': '100g'})
    server = make_server('127.0.0.1', 0, main.app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', mode
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def browser_page():
    pw = pytest.importorskip('playwright.sync_api')
    executable = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(executable) if executable.exists() else None, headless=True)
        page = browser.new_page(viewport={'width':390,'height':844}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        yield page
        assert not errors
        browser.close()


def open_recommend(page, base):
    page.goto(base)
    page.get_by_role('button', name='메뉴 추천 조건 선택', exact=True).wait_for()
    members = page.request.get(base + '/api/members?household_id=1').json()['members']
    for m in members:
        if m['id'] != 3:
            page.locator('[data-view=home] .member').filter(has_text=m['name']).click()
    page.get_by_role('button', name='메뉴 추천 조건 선택', exact=True).click()


def request_recommend(page):
    page.get_by_role('button', name='추천 요청', exact=True).click()
    page.locator('body[aria-busy=false]').wait_for()


def test_real_api_cooking_and_saved_nutrition(live, browser_page):
    base, _ = live
    page = browser_page
    open_recommend(page, base)
    request_recommend(page)
    assert page.locator('#recommendation-status').get_attribute('data-state') == 'recommendations_available'
    assert page.locator('.recipe-option').count() > 0
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path='output/ui/live-mobile-recommend.png',full_page=True)
    page.locator('.recipe-option').filter(has_text='두부').first.click()
    page.get_by_label('실제 재료·분량 및 데이터 품질을 확인했습니다').check()
    page.get_by_role('button', name='조리 시작', exact=True).click()
    page.get_by_label('변경할 정확한 재료명').fill('버섯')
    page.get_by_label('레시피 전체 사용량').fill('0')
    page.get_by_role('button', name='변경 및 영양 재검증').click()
    page.locator('body[aria-busy=false]').wait_for()
    assert '버섯:' not in page.locator('[data-view=cook]').inner_text()
    # A rejected change preserves the last server-validated ingredients.
    page.get_by_label('변경할 정확한 재료명').fill('새우')
    page.get_by_label('레시피 전체 사용량').fill('100')
    page.get_by_role('button', name='변경 및 영양 재검증').click()
    page.get_by_role('alert').wait_for()
    assert '새우: 100g' not in page.locator('[data-view=cook]').inner_text()
    page.get_by_role('button', name='조리 완료 · 섭취량 확인', exact=True).click()
    page.get_by_label('실제 섭취량 (인분)').fill('0.5')
    page.get_by_label('실제 사용량과 개인별 섭취량을 확인했습니다').check()
    with page.expect_response(lambda r: r.url.endswith('/complete')) as saved:
        page.get_by_role('button', name='서버에 식사 기록 저장', exact=True).click()
    saved_nutrition=saved.value.json()['nutrition_per_serving']
    page.get_by_role('heading', name='식사 기록 저장 확인', exact=True).wait_for()
    assert '기록 ID' in page.locator('[data-view=report]').inner_text()
    from app.services.recommendation_repository import open_db
    with open_db() as db:
        assert db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0] == 1
        assert db.execute('SELECT servings FROM meal_record_items').fetchone()[0] == .5
        protein = db.execute("SELECT amount FROM nutrition_intakes WHERE nutrient='protein_g'").fetchone()[0]
        assert protein == round(saved_nutrition['protein_g'] * .5, 2)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path='output/ui/live-mobile-record.png',full_page=True)
    page.reload()
    page.get_by_role('heading',name='식사 기록 저장 확인',exact=True).wait_for()
    assert '과거 기록이 없다는 의미는 아닙니다' in page.locator('[data-view=report]').inner_text()


def test_terminal_states_never_show_previous_candidates(live, browser_page):
    base, mode = live
    page=browser_page
    open_recommend(page,base)
    request_recommend(page)
    page.get_by_label('조리 제한 (분)').fill('1')
    page.get_by_label('조리 제한 (분)').press('Tab')
    request_recommend(page)
    assert page.locator('#recommendation-status').get_attribute('data-state') == 'no_safe_recommendation'
    assert page.locator('.recipe-option').count() == 0
    page.get_by_label('조리 제한 (분)').fill('30')
    page.get_by_label('조리 제한 (분)').press('Tab')
    page.get_by_label('제외 재료 (쉼표 구분)').fill('010-1234-5678')
    page.get_by_label('제외 재료 (쉼표 구분)').press('Tab')
    request_recommend(page)
    assert page.locator('#recommendation-status').get_attribute('data-state') == 'privacy_blocked'
    assert page.locator('.reason-card').count() == 0
    page.get_by_label('제외 재료 (쉼표 구분)').fill('')
    page.get_by_label('제외 재료 (쉼표 구분)').press('Tab')
    mode['error']=True
    request_recommend(page)
    assert page.locator('#recommendation-status').get_attribute('data-state') == 'api_error'
    assert page.locator('.recipe-option').count() == 0
    mode['error']=False
    request_recommend(page)
    assert page.locator('.recipe-option').count() > 0
    page.route('**/api/recommendations',lambda route:route.fulfill(status=500,content_type='text/html',body='server failure'))
    request_recommend(page)
    assert page.locator('#recommendation-status').get_attribute('data-state') == 'api_error'
    assert page.locator('.recipe-option').count() == 0
    assert '서버 응답을 읽을 수 없습니다' in page.get_by_role('alert').inner_text()


def test_legacy_queries_data_state_and_stale_start(live,browser_page):
    base,_=live;page=browser_page
    page.route('**/api/data-quality/summary',lambda route:route.fulfill(status=404,json={'error':'contract pending'}))
    open_recommend(page,base)
    request_recommend(page)
    page.locator('.recipe-option').first.click()
    page.get_by_label('실제 재료·분량 및 데이터 품질을 확인했습니다').check()
    page.route('**/api/recommendations/*',lambda route:route.fulfill(json={'status':'stale_unvalidated','recommendations':[{'name':'MUST NOT DISPLAY'}],'warnings':['release changed']}))
    page.get_by_role('button',name='조리 시작',exact=True).click()
    page.get_by_role('alert').wait_for()
    assert page.get_by_role('button',name='조리 시작',exact=True).count()==0
    page.locator('[data-view-link=inventory]:visible').first.click()
    page.get_by_role('button',name='영양 조회',exact=True).first.click()
    page.get_by_role('dialog').wait_for()
    assert '테스트 영양 응답' in page.get_by_role('dialog').inner_text()
    page.get_by_role('button',name='닫기',exact=True).click()
    page.locator('[data-view-link=home]:visible').first.click()
    page.get_by_label('질문',exact=True).fill('테스트 질문')
    page.get_by_role('button',name='질문 보내기',exact=True).click()
    page.get_by_text('로컬 채팅 테스트 응답',exact=True).wait_for()
    page.locator('[data-view-link=profile]:visible').first.click()
    assert '/api/data-quality/summary' in page.locator('[data-view=profile]').inner_text()
    assert 'ThinQ 동기화 상태' in page.locator('[data-view=profile]').inner_text()
    page.set_viewport_size({'width':1280,'height':900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path='output/ui/live-desktop-data.png',full_page=True)


def test_inventory_registration_uses_normalization_without_replacing_legacy_stock(live,browser_page):
    base,_=live;page=browser_page
    open_recommend(page,base)
    page.locator('[data-view-link=inventory]:visible').first.click()
    page.get_by_label('등록할 정확한 재료명').fill('당근')
    page.get_by_label('측정 중량 (g)').fill('123')
    page.get_by_label('재료명·실제 중량을 확인했습니다').check()
    page.get_by_role('button',name='SQLite 재고 등록',exact=True).click()
    page.locator('body[aria-busy=false]').wait_for()
    assert page.get_by_role('alert').count()==0
    from app.services.recommendation_repository import open_db
    with open_db() as db:
        row=db.execute('SELECT quantity_g FROM inventory_items ORDER BY id DESC LIMIT 1').fetchone()
        assert row[0]==123
    assert page.request.get(base+'/api/inventory').json()['items'][0]['quantity']!=123

@pytest.mark.parametrize('device', ['fridge','mobile'])
def test_explicit_surfaces_keep_mode_across_resize_refresh_and_switch(live,browser_page,device):
    base,_=live;page=browser_page
    page.set_viewport_size({'width':1280,'height':960})
    page.goto(base+'/'+device)
    page.get_by_role('button',name='메뉴 추천 조건 선택',exact=True).wait_for()
    assert page.locator('#app-shell').get_attribute('data-device')==device
    assert page.locator('[data-device-choice='+device+']').get_attribute('aria-pressed')=='true'
    assert page.locator('[data-view=home] .ai-answer:visible').count()==0
    if device=='mobile':
        assert page.locator('#app-shell').bounding_box()['width']<=480
    else:
        assert page.locator('.home-dashboard').evaluate('(n)=>getComputedStyle(n).gridTemplateColumns.split(" ").length')==2
        assert page.locator('.scenario-field').first.evaluate('(n)=>getComputedStyle(n).color')=='rgb(229, 236, 232)'
    for width,height in [(390,844),(870,943),(1280,960)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.locator('#app-shell').get_attribute('data-device')==device
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path=f'output/ui/separate-{device}-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=f'output/ui/separate-{device}-narrow.png',full_page=True)
    page.reload()
    page.get_by_role('button',name='메뉴 추천 조건 선택',exact=True).wait_for()
    assert page.locator('#app-shell').get_attribute('data-device')==device
    # Switching layouts retains the in-memory household selection and active workflow.
    first=page.locator('[data-view=home] .member').first
    first.click()
    selected=page.locator('[data-view=home] .member[aria-pressed=true]').count()
    other='mobile' if device=='fridge' else 'fridge'
    page.get_by_role('button',name='모바일 앱' if other=='mobile' else '냉장고',exact=True).click()
    assert page.locator('#app-shell').get_attribute('data-device')==other
    assert '/'+other in page.url
    assert page.locator('[data-view=home] .member[aria-pressed=true]').count()==selected
