"""Real local HTTP + Chrome E2E; AI/nutrition/chat are deterministic test doubles.
Run: python -m pip install playwright; python -m pytest tests/ui/test_live_flow.py -q
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
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
    from app.services.local_identity import create_account
    from app.services.recommendation_repository import open_db
    with closing(open_db()) as db:
        create_account(db, 'browser-owner', 'synthetic-browser-password', 1)
        viewer = create_account(db, 'browser-viewer', 'synthetic-browser-password', 1)
        with db:
            db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (viewer,))
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
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        yield page
        assert not errors
        browser.close()


def sign_in(page, name='browser-owner', password='synthetic-browser-password'):
    page.get_by_label('로그인 이름').fill(name)
    page.get_by_label('비밀번호').fill(password)
    with page.expect_response(lambda r: r.url.endswith('/api/auth/login')) as login:
        page.get_by_role('button', name='로그인', exact=True).click()
    page.locator('body[aria-busy=false]').wait_for()
    return login.value.json().get('access_token')


def open_recommend(page, base, name='browser-owner', *, from_home=False, token=None):
    page.goto(base + '/mobile')
    if token is None:
        token = sign_in(page, name)
    members = page.request.get(base + '/api/households/1', headers={'Authorization': f'Bearer {token}'}).json()['members']
    member = next(m for m in members if m['id'] == 3)
    page.get_by_role('button', name=member['name'], exact=True).click()
    if from_home:
        page.get_by_role('button', name='맞춤 메뉴 추천받기', exact=True).click()
        page.locator('body[aria-busy=false]').wait_for()
    else:
        page.locator('[data-view-link=recommend]').click()


def request_recommend(page):
    page.locator('.picker-actions .primary').click()
    page.locator('body[aria-busy=false]').wait_for()


def test_reports_failure_retry_and_expired_session_do_not_show_old_totals(live,browser_page):
    base,_=live;page=browser_page
    page.goto(base+'/mobile');sign_in(page)
    page.locator('[data-view-link=report]:visible').first.click()
    page.get_by_role('heading',name='최근 가족 식사 기록',exact=True).wait_for()
    assert page.locator('[data-view=report] [data-status=empty]').first.is_visible()
    page.route('**/api/reports/nutrition?**',lambda route:route.fulfill(status=503,json={'error':'합성 조회 실패'}))
    page.get_by_role('button',name='기록 다시 조회',exact=True).click()
    page.locator('[data-view=report] [data-status=error]').wait_for()
    assert page.locator('[data-view=report] .nutrient-grid').count()==0
    assert page.locator('[data-view=report] [data-status=empty]').count()==0
    page.unroute('**/api/reports/nutrition?**')
    page.get_by_role('button',name='조회 재시도',exact=True).click()
    page.get_by_role('heading',name='최근 가족 식사 기록',exact=True).wait_for()
    page.route('**/api/meal-records?**',lambda route:route.fulfill(status=401,json={'error':'합성 만료'}))
    page.get_by_role('button',name='기록 다시 조회',exact=True).click()
    page.get_by_role('heading',name='로컬 가구 로그인',exact=True).wait_for()
    assert page.locator('[data-view=report] .nutrient-grid').count()==0
    assert not page.evaluate('localStorage.length || sessionStorage.length')
    page.unroute('**/api/meal-records?**')
    sign_in(page)
    page.get_by_role('heading',name='최근 가족 식사 기록',exact=True).wait_for()
    assert page.locator('[data-view=report] [data-status=empty]').first.is_visible()


def test_published_recipes_are_visible_on_menu_screen(live, browser_page):
    from app.services.catalog_repository import publish, stage, validate
    from app.services.recommendation_repository import open_db

    base, _ = live
    photo = 'https://www.foodsafetykorea.go.kr/uploadimg/cook/preview.gif'
    recipes = [
        {'kind': 'recipe', 'external_id': f'R{i}', 'name': f'공개 레시피 {i}',
         'ingredients_text': '두부 100g', 'steps': ['두부를 찐다.'],
         'image_url': photo.replace('https:', 'http:') if i == 1 else None,
         'nutrition': {'basis_amount': 1, 'basis_unit': 'serving',
                       'nutrients': {'energy_kcal': 110, 'protein_g': 2.1}}}
        for i in range(1, 8)
    ]
    with closing(open_db(seed_demo=False)) as db:
        release = stage(db, 'mfds_recipe', recipes, source_revision='fixture')
        assert validate(db, release) == []
        db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_recipe'")
        publish(db, release)

    page = browser_page
    image = bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b')
    page.route(photo, lambda route: route.fulfill(body=image, content_type='image/gif'))
    page.goto(base + '/fridge#recommend')
    sign_in(page)
    catalog = page.get_by_role('heading', name='식약처 레시피', exact=True).locator('..')
    assert catalog.locator('img').get_attribute('src') == photo
    catalog.get_by_role('button', name='공개 레시피 1 조리법 보기', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_text('두부 100g', exact=True).wait_for()
    assert dialog.get_by_text('두부를 찐다.', exact=True).is_visible()
    assert dialog.get_by_text('식약처 제공 · 1인분 기준', exact=True).is_visible()
    assert dialog.get_by_text('110 kcal', exact=True).is_visible()
    assert dialog.get_by_text('2.1 g', exact=True).is_visible()
    assert dialog.get_by_role('img', name='공개 레시피 1 완성 사진', exact=True).get_attribute('src') == photo
    page.keyboard.press('Escape')
    catalog.get_by_role('button', name='다음 레시피', exact=True).click()
    catalog.get_by_role('heading', name='공개 레시피 7', exact=True).wait_for()
    assert catalog.get_by_role('button', name='다음 레시피', exact=True).is_disabled()
    assert page.locator('.recipe-option').count() == 0
    assert page.request.get(base + '/api/data/catalog/recipes?member_id=3').status == 400


@pytest.mark.parametrize('viewport_width', [390, 1280])
def test_new_family_api_registration_then_existing_mobile_login(live, browser_page, viewport_width):
    base, _ = live
    page = browser_page
    page.set_viewport_size({'width': viewport_width, 'height': 844})
    credentials = {'login_name': 'new-family-owner', 'password': 'synthetic-onboarding-password'}
    assert page.request.post(base + '/api/auth/register', data=credentials).status == 201
    account = page.request.post(base + '/api/auth/account-login', data=credentials)
    assert account.status == 200
    created = page.request.post(base + '/api/households', data={'name': '신규 테스트 가족'},
                               headers={'Authorization': f"Bearer {account.json()['access_token']}"})
    assert created.status == 201
    household_id = created.json()['id']
    login = page.request.post(base + '/api/auth/login', data={**credentials, 'household_id': household_id})
    assert login.status == 200
    headers = {'Authorization': f"Bearer {login.json()['access_token']}"}
    for name, age in [('새 구성원 하나', 40), ('새 구성원 둘', 10)]:
        registered = page.request.post(base + f'/api/households/{household_id}/members',
                                      headers=headers, data={'name': name, 'age': age})
        assert registered.status == 201
    assert page.request.get(base + f'/api/members?household_id={household_id}').status == 401
    page.context.clear_cookies()
    page.goto(base + '/mobile')
    page.get_by_label('가구 ID').fill(str(household_id))
    sign_in(page, credentials['login_name'], credentials['password'])
    page.get_by_role('button', name='맞춤 메뉴 추천받기', exact=True).wait_for()
    assert page.locator('[data-view=home] .member').count() == 2
    assert '새 구성원 하나' in page.locator('[data-view=home]').inner_text()
    assert '새 구성원 둘' in page.locator('[data-view=home]').inner_text()
    assert page.locator('[data-view=home] .home-summary [data-status="empty"]').is_visible()
    assert page.get_by_text('최근 7일간 저장된 식사 기록이 없어요.', exact=True).is_visible()
    page.locator('#household-settings').click()
    page.get_by_role('button', name='+ 구성원 추가', exact=True).click()
    page.get_by_label('가족 호칭', exact=True).fill('첫째')
    assert page.get_by_text('엄마, 아빠, 첫째처럼 가족 안에서 부르는 호칭을 입력해 주세요.', exact=True).is_visible()
    page.get_by_label('나이', exact=True).fill('12')
    page.get_by_label('키 (cm, 선택)', exact=True).fill('153.5')
    page.get_by_label('알레르기 (쉼표 구분)').fill('새우')
    dialog = page.get_by_role('dialog')
    fields = dialog.locator('form > .scenario-controls')
    columns = fields.evaluate('(grid) => getComputedStyle(grid).gridTemplateColumns')
    assert len(columns.split()) == 2
    assert dialog.get_by_role('radio', name='잘 모르겠어요', exact=True).is_checked()
    assert dialog.get_by_text('대부분 앉아서 지내고, 걷거나 운동하는 시간이 적어요.', exact=True).is_visible()
    assert dialog.get_by_text('주로 앉아서 지내지만, 출퇴근이나 산책으로 꾸준히 걸어요.', exact=True).is_visible()
    dialog.get_by_role('radio', name='저활동적', exact=True).check()
    with page.expect_response(lambda r: r.url.endswith(f'/api/households/{household_id}/members')) as member:
        page.get_by_role('button', name='구성원 추가', exact=True).click()
    assert member.value.status == 201
    page.get_by_role('heading', name='첫째', exact=True).wait_for()
    assert '새우' in page.locator('[data-view=profile]').inner_text()
    page.get_by_role('button', name='첫째 정보 수정', exact=True).click()
    dialog = page.get_by_role('dialog')
    assert fields.evaluate('(grid) => getComputedStyle(grid).gridTemplateColumns') == columns
    assert dialog.get_by_label('가족 호칭', exact=True).input_value() == '첫째'
    assert dialog.get_by_label('알레르기').input_value() == '새우'
    assert dialog.get_by_label('키 (cm, 선택)', exact=True).input_value() == '153.5'
    assert dialog.get_by_role('radio', name='저활동적', exact=True).is_checked()
    assert page.get_by_role('button', name='저장', exact=True).is_disabled()
    dialog.get_by_label('가족 호칭', exact=True).fill('둘째')
    dialog.get_by_label('나이', exact=True).fill('13')
    dialog.get_by_label('알레르기 (쉼표 구분)', exact=True).fill('게')
    dialog.get_by_label('비선호 음식 (쉼표 구분)', exact=True).fill('고수, 버섯')
    dialog.get_by_label('몸무게 (kg, 선택)', exact=True).fill('48.2')
    dialog.get_by_role('radio', name='활동적', exact=True).check()
    with page.expect_response(lambda r: r.request.method == 'PATCH') as updated:
        dialog.get_by_role('button', name='저장', exact=True).click()
    assert updated.value.status == 200
    page.get_by_text('48.2 kg', exact=True).wait_for()
    page.get_by_role('button', name='둘째 삭제', exact=True).click()
    assert page.get_by_role('button', name='삭제 확인', exact=True).is_disabled()
    page.get_by_role('button', name='닫기', exact=True).first.click()
    page.locator('[data-view-link=home]').last.click()
    assert page.get_by_role('button', name='둘째', exact=True).get_attribute('aria-pressed') == 'false'
    page.get_by_role('button', name='둘째', exact=True).click()
    page.locator('[data-view-link=recommend]').click()
    assert page.get_by_role('button', name='둘째', exact=True).get_attribute('aria-pressed') == 'true'
    page.get_by_role('button', name='전체 선택', exact=True).click()
    assert page.locator('[data-view=recommend] .member[aria-pressed=true]').count() == 4
    assert not page.evaluate('localStorage.length || sessionStorage.length')
    page.goto(base + '/fridge#profile')
    page.get_by_text('153.5 cm', exact=True).wait_for()
    assert page.get_by_text('48.2 kg', exact=True).is_visible()
    assert page.get_by_text('활동적', exact=True).is_visible()
    page.get_by_role('button', name='둘째 정보 수정', exact=True).click()
    assert dialog.get_by_label('나이', exact=True).input_value() == '13'
    assert dialog.get_by_label('알레르기 (쉼표 구분)', exact=True).input_value() == '게'
    assert dialog.get_by_label('비선호 음식 (쉼표 구분)', exact=True).input_value() == '고수, 버섯'


@pytest.mark.parametrize('width', [390, 1280])
def test_real_api_cooking_and_saved_nutrition(live, browser_page, width):
    base, _ = live
    page = browser_page
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 800})
    open_recommend(page, base, from_home=True)
    assert page.locator('#recommendation-status').get_attribute('data-state') == 'recommendations_available'
    assert page.locator('.recipe-option').count() > 0
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path=f'output/ui/live-recommend-{width}.png',full_page=True)
    recipe = page.locator('.recipe-option').filter(has_text='두부').first
    recipe_name = recipe.get_by_role('heading').inner_text()
    recipe.get_by_role('button').click()
    page.get_by_label('실제 재료·분량 및 데이터 품질을 확인했습니다').check()
    page.get_by_role('button', name='조리 시작', exact=True).click()
    page.get_by_label('변경할 정확한 재료명').fill('버섯')
    page.get_by_label('이번 조리 총 사용량').fill('0')
    page.get_by_role('button', name='변경 및 영양 재검증').click()
    page.locator('body[aria-busy=false]').wait_for()
    assert '버섯:' not in page.locator('[data-view=cook]').inner_text()
    # A rejected change preserves the last server-validated ingredients.
    page.get_by_label('변경할 정확한 재료명').fill('새우')
    page.get_by_label('이번 조리 총 사용량').fill('100')
    page.get_by_role('button', name='변경 및 영양 재검증').click()
    page.get_by_role('alert').wait_for()
    assert '새우: 100g' not in page.locator('[data-view=cook]').inner_text()
    assert '계획 1인분' in page.locator('[data-view=cook]').inner_text()
    page.get_by_role('button', name='조리 완료 · 섭취량 확인', exact=True).click()
    assert page.get_by_label('실제 섭취량 (인분)').input_value() == ''
    page.get_by_label('실제 섭취량 (인분)').fill('0.5')
    page.get_by_label('실제 사용량과 개인별 섭취량을 확인했습니다').check()
    from app.services.recommendation_repository import open_db
    with open_db() as db:
        used=json.loads(db.execute("SELECT ingredients_json FROM cooking_sessions WHERE status='active'").fetchone()[0])
        stock_before={r['ingredient_id']:r['quantity_g'] for r in db.execute('SELECT ingredient_id,quantity_g FROM inventory_items')}
    # Check the A snapshot directly after applying the outflows and after B completes.
    page.get_by_role('button', name='수동 출고량 확인', exact=True).click()
    page.get_by_label('아직 출고하지 않은 재료이며 위 재고와 중량을 확인했습니다').check()
    with page.expect_response(lambda r: r.url.endswith('/api/data/inventory')) as withdrawn:
        page.get_by_role('button', name='수동 출고 확정', exact=True).click()
    after_withdrawal = withdrawn.value.json()
    page.get_by_label('실제 사용량과 개인별 섭취량을 확인했습니다').check()
    with page.expect_response(lambda r: r.url.endswith('/api/data/inventory')) as refreshed, page.expect_response(lambda r: r.url.endswith('/complete')) as saved:
        page.get_by_role('button', name='서버에 식사 기록 저장', exact=True).click()
    assert saved.value.status == 201
    assert refreshed.value.json() == after_withdrawal
    saved_nutrition=saved.value.json()['nutrition_per_serving']
    report = page.locator('[data-view=report]')
    recent = report.locator('.family-meals button').filter(has_text=recipe_name)
    recent.wait_for()
    from app.services.recommendation_repository import open_db
    with open_db() as db:
        assert db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0] == 1
        assert db.execute('SELECT servings FROM meal_record_items').fetchone()[0] == .5
        protein = db.execute("SELECT amount FROM nutrition_intakes WHERE nutrient='protein_g'").fetchone()[0]
        assert protein == round(saved_nutrition['protein_g'] * .5, 2)
        member_name = db.execute('SELECT name FROM members WHERE id=3').fetchone()[0]
        stock_after={r['ingredient_id']:r['quantity_g'] for r in db.execute('SELECT ingredient_id,quantity_g FROM inventory_items')}
        for ingredient in used:
            assert stock_after[ingredient['ingredient_id']]==stock_before[ingredient['ingredient_id']]-ingredient['quantity_g']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path=f'output/ui/live-record-{width}.png',full_page=True)
    feedback = page.get_by_role('dialog')
    recent.click()
    feedback.get_by_role('button', name='식후 평가', exact=True).click()
    feedback.get_by_role('group', name='맛', exact=True).wait_for()
    assert feedback.get_by_role('button', name='평가 저장', exact=True).is_disabled()
    feedback.get_by_role('button', name='평가 건너뛰기').click()
    page.reload()
    recent.wait_for()
    family_summary = report.get_by_role('table')
    protein_row = family_summary.get_by_role('row').filter(has_text='단백질')
    assert protein_row.get_by_text(f'{protein:g} g', exact=True).is_visible()
    assert protein_row.get_by_text('기록 더 필요', exact=True).is_visible()
    badge_tops = protein_row.locator('.badge').evaluate_all('badges => badges.map(badge => badge.getBoundingClientRect().top)')
    assert max(badge_tops) - min(badge_tops) < 0.5
    assert family_summary.get_by_role('row').filter(has_text='식이섬유').get_by_text('미제공', exact=True).is_visible()
    filters = report.get_by_role('group', name='영양 현황 대상')
    filter_names = filters.get_by_role('button').all_text_contents()
    other_member = next(name for name in filter_names if name not in ('우리 가족', member_name))
    filters.get_by_role('button', name=other_member, exact=True).click()
    report.locator('.nutrition-bottom [data-status=empty]').wait_for()
    assert not report.get_by_role('heading', name=recipe_name, exact=True).count()
    filters.get_by_role('button', name='우리 가족', exact=True).click()
    family_summary.get_by_role('button', name=member_name, exact=True).click()
    report.get_by_role('heading', name=recipe_name, exact=True).wait_for()
    member_summary = report.locator('section').filter(
        has=page.get_by_role('heading', name=f'{member_name}의 최근 영양 현황', exact=True),
    )
    assert member_summary.get_by_text(f'{protein:g} g', exact=True).is_visible()
    page.get_by_role('link', name='KINI 홈', exact=True).click()
    home_records = page.locator('[data-view=home] .receipts')
    home_records.get_by_role('heading', name=recipe_name, exact=True).wait_for()
    assert '0.5인분' in home_records.inner_text()
    home_records.get_by_role('button', name='식후 평가', exact=True).click()
    taste = feedback.get_by_role('group', name='맛', exact=True)
    salt = feedback.get_by_role('group', name='간', exact=True)
    taste.get_by_role('button', name='이 항목 건너뛰기', pressed=True).wait_for()
    taste.get_by_role('button', name='맛있어요', exact=True).click()
    salt.get_by_role('button', name='적당해요', exact=True).click()
    save = feedback.get_by_role('button', name='평가 저장', exact=True)
    with page.expect_response(lambda r: r.url.endswith('/feedback') and r.request.method == 'POST') as review:
        save.click()
    assert review.value.status == 200
    assert review.value.json()['feedback'] == {'taste': 'good', 'salt': 'balanced', 'difficulty': None}
    feedback.get_by_text('평가를 저장했어요.', exact=True).wait_for()
    page.reload()
    home_records.get_by_role('button', name='식후 평가', exact=True).click()
    taste.get_by_role('button', name='맛있어요', pressed=True).wait_for()
    assert salt.get_by_role('button', name='적당해요', pressed=True).is_visible()
    difficulty = feedback.get_by_role('group', name='난이도', exact=True)
    assert difficulty.get_by_role('button', name='이 항목 건너뛰기', pressed=True).is_visible()
    page.keyboard.press('Escape')
    page.locator('button[data-view-link=report]').click()
    with page.expect_response(lambda r: r.url.endswith('/api/recommendations') and r.request.method == 'POST') as family_request:
        report.get_by_role('button', name='가족 맞춤 메뉴 찾기', exact=True).click()
    assert family_request.value.request.post_data_json['participant_ids'] == [1, 2, 3, 4]


def test_terminal_states_never_show_previous_candidates(live, browser_page):
    base, mode = live
    page=browser_page
    open_recommend(page,base)
    request_recommend(page)
    page.get_by_text('추천 조건', exact=True).click()
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
    page.locator('.recipe-option').first.get_by_role('button').click()
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
    page.get_by_text('기타 식재료 도구', exact=True).click()
    page.get_by_role('button',name='재고에 대해 질문',exact=True).click()
    page.get_by_label('질문',exact=True).fill('테스트 질문')
    page.get_by_role('button',name='질문 보내기',exact=True).click()
    page.get_by_text('로컬 채팅 테스트 응답',exact=True).wait_for()
    page.get_by_role('button',name='닫기',exact=True).click()
    page.locator('[data-view-link=profile]:visible').first.click()
    page.get_by_text('가구 계정 및 데이터 출처', exact=True).click()
    assert '/api/data-quality/summary' in page.locator('[data-view=profile]').inner_text()
    page.set_viewport_size({'width':1280,'height':900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path='output/ui/live-desktop-data.png',full_page=True)


def test_inventory_registration_uses_normalization_without_replacing_legacy_stock(live,browser_page):
    base,_=live;page=browser_page
    open_recommend(page,base)
    page.locator('[data-view-link=inventory]:visible').first.click()
    page.get_by_role('button', name='+ 식재료 추가', exact=True).click()
    page.get_by_label('등록할 정확한 재료명').fill('당근')
    page.get_by_label('측정 중량').fill('0.123')
    page.get_by_label('단위', exact=True).select_option('kg')
    page.get_by_label('재료명·실제 중량을 확인했습니다').check()
    page.get_by_role('button',name='입고 확정',exact=True).click()
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
    sign_in(page)
    page.get_by_role('button',name='맞춤 메뉴 추천받기',exact=True).wait_for()
    assert page.locator('#app-shell').get_attribute('data-device')==device
    assert page.locator('[data-view=home] .ai-answer:visible').count() == 0
    columns = page.locator('.home-summary').evaluate('(n)=>getComputedStyle(n).gridTemplateColumns.split(" ").length')
    assert columns == (1 if device == 'mobile' else 2)
    nav = page.get_by_role('navigation', name='주요 메뉴')
    assert nav.evaluate('(n)=>getComputedStyle(n).position') == ('fixed' if device == 'mobile' else 'sticky')
    for width,height in [(320,780),(390,844),(870,943),(1280,960)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.locator('#app-shell').get_attribute('data-device')==device
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    Path('output/ui').mkdir(parents=True,exist_ok=True)
    page.screenshot(path=f'output/ui/separate-{device}-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    if device == 'mobile':
        before = nav.bounding_box()
        assert before['y'] + before['height'] == pytest.approx(844)
        page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        assert page.evaluate('window.scrollY') > 0
        assert nav.bounding_box()['y'] == pytest.approx(before['y'])
        last_card = page.locator('[data-view=home] > :last-child')
        assert last_card.bounding_box()['y'] + last_card.bounding_box()['height'] <= before['y']
        page.evaluate('window.scrollTo(0, 0)')
    page.screenshot(path=f'output/ui/separate-{device}-narrow.png',full_page=True)
    page.reload()
    page.get_by_role('button',name='맞춤 메뉴 추천받기',exact=True).wait_for()
    assert page.locator('#app-shell').get_attribute('data-device')==device
    # Switching layouts retains the in-memory household selection and active workflow.
    first=page.locator('[data-view=home] .member').first
    first.click()
    selected=page.locator('[data-view=home] .member[aria-pressed=true]').count()
    other='mobile' if device=='fridge' else 'fridge'
    page.locator('button[data-view-link=recommend]').click()
    switcher = page.get_by_role('group', name='화면 버전')
    assert switcher.locator(f'[data-device-choice={device}]').get_attribute('aria-pressed') == 'true'
    switcher.get_by_role('button', name='앱 버전' if other=='mobile' else '냉장고 버전', exact=True).click()
    assert page.locator('#app-shell').get_attribute('data-device')==other
    assert page.url.endswith(f'/{other}#recommend')
    assert page.locator('[data-view=recommend] .member[aria-pressed=true]').count() == selected
    page.go_back()
    switcher.locator(f'[data-device-choice={device}][aria-pressed="true"]').wait_for()
    assert page.url.endswith(f'/{device}#recommend')
    page.go_forward()
    switcher.locator(f'[data-device-choice={other}][aria-pressed="true"]').wait_for()
    assert page.url.endswith(f'/{other}#recommend')
    page.locator('#household-settings').click()
    logout = page.get_by_role('button', name='로그아웃', exact=True)
    if not logout.is_visible():
        page.get_by_text('가구 계정 및 데이터 출처', exact=True).click()
    logout.click()
    page.get_by_role('heading', name='로컬 가구 로그인', exact=True).wait_for()
    page.reload()
    page.get_by_role('heading', name='로컬 가구 로그인', exact=True).wait_for()



@pytest.mark.parametrize('width,height', [(1280, 800), (1024, 768), (390, 844)])
def test_tablet_screens_fit_and_pending_actions_are_explicit(live, browser_page, width, height):
    base, _ = live
    page = browser_page
    output = Path('output/ui')
    output.mkdir(parents=True, exist_ok=True)
    from app.services.recommendation_repository import open_db

    today = datetime.now(timezone(timedelta(hours=9))).date()
    with open_db() as db:
        db.execute('UPDATE inventory_items SET expiry_date=NULL WHERE household_id=1')
        ids = db.execute('SELECT id FROM inventory_items WHERE household_id=1 AND quantity_g>0 ORDER BY id LIMIT 2').fetchall()
        db.executemany('UPDATE inventory_items SET expiry_date=? WHERE id=?', [
            ((today - timedelta(days=1)).isoformat(), ids[0]['id']),
            ((today + timedelta(days=2)).isoformat(), ids[1]['id']),
        ])

    page.set_viewport_size({'width': width, 'height': height})
    page.goto(base + '/fridge')
    sign_in(page)
    assert page.locator('[data-view=home] .expiry-list').get_by_text('D-2 · 임박', exact=True).is_visible()
    assert page.locator('[data-view=home] .expiry-list').get_by_text('기한 지남', exact=True).is_visible()
    assert not page.get_by_text('KINI가 궁금해요', exact=True).is_visible()
    assert page.get_by_role('button', name='맞춤 메뉴 추천받기', exact=True).is_disabled()
    nav = page.get_by_role('navigation', name='주요 메뉴')
    assert nav.get_by_role('button').count() == 4
    assert nav.evaluate('(nav) => getComputedStyle(nav).position') == ('sticky' if width > 760 else 'static')
    for screen in ['home', 'inventory', 'profile', 'report', 'recommend']:
        page.locator(f'button[data-view-link={screen}]').click()
        page.locator('body[aria-busy=false]').wait_for()
        assert page.locator('[data-root-view]:visible').count() == 1
        assert page.locator('button[aria-current=page]').get_attribute('data-view-link') == screen
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert nav.get_by_role('button').evaluate_all(
            '(buttons) => buttons.every(button => button.getBoundingClientRect().height >= 44)'
        )
        page.screenshot(path=output / f'tablet-{width}-{screen}.png', full_page=True, animations='disabled')
        if screen == 'home':
            assert page.get_by_role('heading', name='최근 식사 기록', exact=True).is_visible()
            assert page.get_by_role('heading', name='KINI 맞춤 구매 제안', exact=True).is_visible()
        elif screen == 'inventory':
            page.get_by_role('button', name='채소', exact=True).wait_for()
            assert page.get_by_role('button', name='채소', exact=True).is_disabled()
            edit = page.locator('.inventory-table button', has_text='수정').first
            edit.click()
            assert page.get_by_role('dialog').get_by_label('식재료명').evaluate('(input) => input.readOnly')
            assert page.get_by_role('button', name='변경 저장', exact=True).is_disabled()
            page.keyboard.press('Escape')
            assert edit.evaluate('(button) => button === document.activeElement')
            page.locator('.inventory-table button', has_text='삭제').first.click()
            assert page.get_by_role('dialog').get_by_role('button', name='재고에서 삭제', exact=True).is_disabled()
            page.keyboard.press('Escape')
            page.get_by_label('식재료 검색').fill('일치하지 않는 재료명')
            assert page.locator('.inventory-list [data-status="empty"]').is_visible()
        elif screen == 'report':
            assert page.get_by_role('heading', name='우리 가족 영양 밸런스', exact=True).is_visible()
            assert page.get_by_role('table').get_by_text('기록 없음', exact=True).count() > 0
            assert page.get_by_role('button', name='가족 맞춤 메뉴 찾기', exact=True).is_enabled()
        elif screen == 'recommend':
            assert page.get_by_role('heading', name='냉장고 속 재료로 만들 수 있는 메뉴', exact=True).is_visible()
            assert page.locator('.recipe-option').count() == 0

    page.route('**/api/data/inventory', lambda route: route.fulfill(status=500, json={'error': 'test failure'}))
    page.locator('[data-view-link=inventory]').click()
    page.locator('.inventory-list [data-status="error"]').wait_for()


def test_short_stock_preflight_creates_no_events_and_preserves_intake_draft(live,browser_page):
    base,_=live;page=browser_page
    open_recommend(page,base);request_recommend(page)
    page.locator('.recipe-option').filter(has_text='두부').first.get_by_role('button').click()
    page.get_by_label('실제 재료·분량 및 데이터 품질을 확인했습니다').check()
    page.get_by_role('button',name='조리 시작',exact=True).click()
    page.get_by_role('button',name='조리 완료 · 섭취량 확인',exact=True).click()
    from app.services.recommendation_repository import open_db
    with open_db() as db:
        db.execute('UPDATE inventory_items SET quantity_g=1,quantity=1 WHERE ingredient_id=4')
        before=[tuple(r) for r in db.execute('SELECT id,quantity_g FROM inventory_items ORDER BY id')]
    page.get_by_label('실제 섭취량 (인분)').fill('0.5')
    page.get_by_role('button',name='수동 출고량 확인',exact=True).click()
    page.locator('[role=alert][data-http-status="409"]').wait_for()
    assert page.get_by_label('실제 섭취량 (인분)').input_value()=='0.5'
    with open_db() as db:
        assert db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM inventory_events').fetchone()[0]==0
        assert [tuple(r) for r in db.execute('SELECT id,quantity_g FROM inventory_items ORDER BY id')]==before
        assert db.execute('SELECT status FROM cooking_sessions').fetchone()[0]=='active'


def test_login_failure_and_completion_http_errors(live, browser_page):
    base, _ = live
    page = browser_page
    page.goto(base + '/mobile')
    sign_in(page, password='synthetic-invalid-password')
    assert page.locator('[role=alert][data-http-status="401"]').is_visible()
    assert page.get_by_label('비밀번호').input_value() == ''

    open_recommend(page, base, 'browser-viewer')
    request_recommend(page)
    page.locator('.recipe-option').first.get_by_role('button').click()
    page.get_by_label('실제 재료·분량 및 데이터 품질을 확인했습니다').check()
    page.get_by_role('button', name='조리 시작', exact=True).click()
    page.get_by_role('button', name='조리 완료 · 섭취량 확인', exact=True).click()
    page.get_by_label('기존 확정 출고 ID (쉼표 구분)').fill('99999')
    page.get_by_label('실제 섭취량 (인분)').fill('1')

    def save_with_error(status):
        page.get_by_label('실제 사용량과 개인별 섭취량을 확인했습니다').check()
        with page.expect_response(lambda r: r.url.endswith('/complete')) as saved:
            page.get_by_role('button', name='서버에 식사 기록 저장', exact=True).click()
        assert saved.value.status == status
        page.locator(f'[role=alert][data-http-status="{status}"]').wait_for()

    save_with_error(403)
    page.get_by_role('button', name='다시 로그인', exact=True).click()
    owner_token = sign_in(page)
    # A different account must not inherit the viewer's unfinished form.
    assert page.get_by_label('실제 섭취량 (인분)').count() == 0
    open_recommend(page, base, token=owner_token)
    request_recommend(page)
    page.locator('.recipe-option').first.get_by_role('button').click()
    page.get_by_label('실제 재료·분량 및 데이터 품질을 확인했습니다').check()
    page.get_by_role('button', name='조리 시작', exact=True).click()
    page.get_by_role('button', name='조리 완료 · 섭취량 확인', exact=True).click()
    page.get_by_label('기존 확정 출고 ID (쉼표 구분)').fill('99999')
    page.get_by_label('실제 섭취량 (인분)').fill('1')
    save_with_error(409)
    assert '출고 확인 필요' in page.get_by_role('alert').inner_text()

    def omit_header(route):
        headers = {key: value for key, value in route.request.headers.items() if key.lower() != 'authorization'}
        route.continue_(headers=headers)

    page.route('**/complete', omit_header)
    save_with_error(401)
    page.get_by_role('heading', name='로컬 가구 로그인', exact=True).wait_for()
    assert not page.evaluate('localStorage.length || sessionStorage.length')
