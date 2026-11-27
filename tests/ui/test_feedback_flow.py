"""Real browser + authenticated feedback APIs on an isolated synthetic SQLite."""
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

from flask import Flask, request, render_template
from werkzeug.serving import make_server

from scripts.b_synthetic_store import prepare
from scripts.setup_meal_feedback import install
from app.b_runtime import internal_app
from app.services.recommendation_repository import open_db
from test_live_flow import browser_page


def test_shared_meal_skip_save_edit_and_reconnect(tmp_path, monkeypatch, browser_page):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / 'feedback-ui.sqlite3'
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    prepare(path, 'synthetic-feedback-ui', 'synthetic-password')
    monkeypatch.setenv('KINI_DB_PATH', str(path))
    monkeypatch.setenv('KINI_B_MEAL_FEEDBACK_ENABLED', '1')
    monkeypatch.setenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE', str(root / 'deploy/b/meal-feedback-options.v1.json'))
    with open_db() as db:
        install(db)
        db.execute("INSERT INTO meal_sessions(id,household_id,meal_type) VALUES (901,1,'dinner')")
        db.executemany('INSERT INTO meal_session_participants VALUES (?,?)', [(901,3),(901,4)])
        db.execute('INSERT INTO meal_records(id,session_id,recipe_id,eaten_at) VALUES (901,901,1,?)',
                   (datetime.now(timezone.utc).isoformat(),))
        db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (901,3,.5),(901,4,0)')
    api = internal_app('rules-only')
    ui = Flask('feedback_ui', static_folder=str(root / 'app/static'), static_url_path='/static',
               template_folder=str(root / 'app/templates'))

    @ui.get('/mobile')
    def screen():
        return render_template('index.html', device_mode='mobile')

    @ui.route('/api/<path:rest>', methods=['GET','POST','PUT'])
    def dispatch(rest):
        return api.test_client().open(request.full_path, method=request.method, data=request.get_data(),
            headers={key: request.headers[key] for key in ('Authorization','Content-Type') if key in request.headers})

    server = make_server('127.0.0.1', 0, ui, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser_page
    page.set_default_timeout(15000)
    writes = []
    page.on('request', lambda req: writes.append(req.url) if req.method == 'PUT' else None)

    def login():
        page.get_by_label('로그인 이름', exact=True).fill('synthetic-feedback-ui')
        page.get_by_label('비밀번호', exact=True).fill('synthetic-password')
        page.get_by_role('button', name='로그인', exact=True).click()
        page.locator('body[aria-busy=false]').wait_for()

    def open_feedback(title='식후 평가'):
        page.locator('.family-meals button').first.click()
        page.get_by_role('dialog').get_by_role('button', name='식후 평가', exact=True).click()
        page.get_by_role('dialog').get_by_role('heading', name=title, exact=True).wait_for()
        page.locator('body[aria-busy=false]').wait_for()

    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/mobile')
        login()
        page.locator('[data-view-link=report]:visible').first.click()
        page.get_by_role('heading', name='최근 가족 식사 기록', exact=True).wait_for()
        open_feedback()
        page.get_by_role('dialog').get_by_role('button', name='평가 건너뛰기', exact=True).click()
        assert not writes
        open_feedback()
        page.get_by_label('응답하는 식사 참여자', exact=True).select_option('4')
        page.get_by_role('dialog').locator('section[aria-label="맛"]').get_by_role('button', name='맛있어요', exact=True).click()
        page.get_by_role('dialog').locator('section[aria-label="간"]').get_by_role('button', name='짜요', exact=True).click()
        page.get_by_role('dialog').get_by_role('button', name='평가 저장', exact=True).click()
        page.get_by_role('heading', name='평가 저장 완료', exact=True).wait_for()
        page.locator('#dialog-close').click()
        open_feedback('식후 평가 수정')
        assert page.get_by_label('응답하는 식사 참여자', exact=True).is_disabled()
        page.get_by_role('dialog').locator('section[aria-label="맛"]').get_by_role('button', name='별로예요', exact=True).click()
        page.get_by_role('button', name='평가 수정 저장', exact=True).click()
        page.get_by_role('heading', name='평가 저장 완료', exact=True).wait_for()
        with open_db() as db:
            assert db.execute('SELECT count(*) FROM meal_feedback').fetchone()[0] == 1
            row = db.execute('SELECT * FROM meal_feedback').fetchone()
            assert row['member_id'] == 4 and row['revision'] == 2 and row['taste'] == 'taste_dissatisfied'
        page.reload()
        login()
        page.get_by_role('heading', name='최근 가족 식사 기록', exact=True).wait_for()
        open_feedback('식후 평가 수정')
        assert page.get_by_role('dialog').locator('section[aria-label="맛"]').get_by_role('button', name='별로예요', exact=True).get_attribute('aria-pressed') == 'true'
        assert len(writes) == 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # A newly completed meal prompts once after successful persistence.
        page.locator('#dialog-close').click()
        page.locator('[data-view-link=home]:visible').first.click()
        with open_db() as db:
            name = db.execute('SELECT name FROM members WHERE id=3').fetchone()[0]
        page.get_by_role('button', name=name, exact=True).click()
        page.get_by_role('button', name='맞춤 메뉴 추천받기', exact=True).click()
        page.locator('body[aria-busy=false]').wait_for()
        page.locator('.recipe-option').first.get_by_role('button').click()
        page.get_by_label('실제 재료·분량 및 데이터 품질을 확인했습니다').check()
        page.get_by_role('button', name='조리 시작', exact=True).click()
        page.get_by_role('button', name='조리 완료 · 섭취량 확인', exact=True).click()
        page.get_by_label('실제 섭취량 (인분)').fill('0.5')
        page.get_by_role('button', name='수동 출고량 확인', exact=True).click()
        page.get_by_label('아직 출고하지 않은 재료이며 위 재고와 중량을 확인했습니다').check()
        page.get_by_role('button', name='수동 출고 확정', exact=True).click()
        page.locator('body[aria-busy=false]').wait_for()
        page.get_by_label('실제 사용량과 개인별 섭취량을 확인했습니다').check()
        page.get_by_role('button', name='서버에 식사 기록 저장', exact=True).click()
        page.get_by_role('heading', name='식사 기록 저장 완료', exact=True).wait_for()
        page.get_by_role('button', name='평가 건너뛰기', exact=True).click()
        assert len(writes) == 2
        with open_db() as db:
            assert db.execute('SELECT count(*) FROM meal_records').fetchone()[0] == 2
            assert db.execute('SELECT count(*) FROM meal_feedback').fetchone()[0] == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
