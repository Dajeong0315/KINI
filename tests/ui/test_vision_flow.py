"""390px Chrome, actual saved Vision output with synthetic local inventory only."""
import json
from pathlib import Path
from threading import Thread
from contextlib import closing

from flask import Flask, render_template, request
from werkzeug.serving import make_server

from app.b_runtime import internal_app
from app.services import inventory_events
from app.services.local_identity import login
from app.services.recommendation_repository import open_db
from fridge_vision.kini_result_bridge import events_from_result
from scripts.b_synthetic_store import prepare
from test_live_flow import browser_page


def test_real_capture_confirm_loss_retry_reject_reconnect(tmp_path, monkeypatch, browser_page):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / 'vision-ui.sqlite3'
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    monkeypatch.setenv('KINI_B_MEAL_FEEDBACK_ENABLED', '0')
    prepare(path, 'synthetic-vision-ui', 'synthetic-password')
    monkeypatch.setenv('KINI_DB_PATH', str(path))
    result = json.loads((root / 'tests/fixtures/vision/real_sample_9159259.json').read_text(encoding='utf-8'))
    with closing(open_db()) as db:
        token = login(db, 'synthetic-vision-ui', 'synthetic-password', 1)
        db.execute("INSERT INTO ingredients(id,name,canonical_name,source_type) VALUES (901,'버터','버터','demo')")
        db.execute("INSERT INTO inventory_items(household_id,ingredient_id,quantity_g) VALUES (1,901,100)")
        db.commit()
        pending = inventory_events.ingest(db, token, events_from_result(result)[0])
        rejected = inventory_events.ingest(db, token, events_from_result({**result, 'capture_id': 'reject-rehearsal'})[0])
    api = internal_app('rules-only')
    ui = Flask('vision_ui', static_folder=str(root / 'app/static'), static_url_path='/static',
               template_folder=str(root / 'app/templates'))
    @ui.get('/mobile')
    def screen(): return render_template('index.html', device_mode='mobile')
    @ui.route('/api/<path:rest>', methods=['GET', 'POST', 'PUT'])
    def dispatch(rest):
        return api.test_client().open(request.full_path, method=request.method, data=request.get_data(),
            headers={k:request.headers[k] for k in ('Authorization','Content-Type') if k in request.headers})
    server = make_server('127.0.0.1', 0, ui, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    page = browser_page
    base = f'http://127.0.0.1:{server.server_port}'
    def sign_in():
        page.get_by_label('로그인 이름', exact=True).fill('synthetic-vision-ui')
        page.get_by_label('비밀번호', exact=True).fill('synthetic-password')
        page.get_by_role('button', name='로그인', exact=True).click()
        page.locator('body[aria-busy=false]').wait_for()
        page.locator('[data-view-link=inventory]:visible').first.click()
        page.get_by_role('heading', name='인식 확인 대기', exact=True).wait_for()
    try:
        page.goto(base + '/mobile'); sign_in()
        page.get_by_role('button', name='재료·중량 확인', exact=True).first.click()
        page.get_by_label('확인한 표준 재료명', exact=True).fill('없는재료')
        page.get_by_role('button', name='표준 재료 확인', exact=True).click()
        page.locator('#dialog-error').wait_for(state='visible')
        page.get_by_label('확인한 표준 재료명', exact=True).fill('버터')
        page.get_by_role('button', name='표준 재료 확인', exact=True).click()
        page.get_by_label('실제 측정 중량 (g)', exact=True).wait_for()
        page.locator('body[aria-busy=false]').wait_for()
        with closing(open_db()) as db:
            lot = db.execute('SELECT id FROM inventory_items WHERE ingredient_id=901').fetchone()[0]
        page.get_by_label('출고할 실제 재고', exact=True).select_option(str(lot))
        page.get_by_label('실제 측정 중량 (g)', exact=True).fill('10')
        page.get_by_label('표준 재료·실제 중량과 입출고 방향을 확인했습니다', exact=True).check()
        lost = {'once': True}
        def response_loss(route):
            reply = route.fetch()
            if lost['once']:
                assert reply.status == 200
                lost['once'] = False
                route.abort('failed')
            else: route.fulfill(response=reply)
        page.route(f'**/inventory/events/{pending["event_id"]}/confirm', response_loss)
        page.get_by_role('button', name='재고 확정', exact=True).click()
        page.get_by_role('button', name='같은 내용으로 확정 재시도', exact=True).wait_for()
        page.locator('body[aria-busy=false]').wait_for()
        assert page.get_by_label('실제 측정 중량 (g)', exact=True).is_disabled()
        page.get_by_role('button', name='같은 내용으로 확정 재시도', exact=True).click()
        page.get_by_role('dialog').wait_for(state='hidden')
        page.locator('body[aria-busy=false]').wait_for()
        page.get_by_role('button', name='인식 거절', exact=True).click()
        page.locator('body[aria-busy=false]').wait_for()
        assert page.get_by_role('button', name='재료·중량 확인', exact=True).count() == 0
        incoming_result = json.loads((root / 'tests/fixtures/vision/real_incoming_9159259.json').read_text(encoding='utf-8'))
        with closing(open_db()) as db:
            incoming_event = inventory_events.ingest(db, token, events_from_result(incoming_result)[0])
        page.get_by_role('button', name='재고 새로고침', exact=True).click()
        page.locator('body[aria-busy=false]').wait_for()
        page.get_by_role('button', name='재료·중량 확인', exact=True).click()
        page.get_by_label('확인한 표준 재료명', exact=True).fill('버터')
        page.get_by_role('button', name='표준 재료 확인', exact=True).click()
        page.get_by_label('실제 측정 중량 (g)', exact=True).wait_for()
        page.locator('body[aria-busy=false]').wait_for()
        page.get_by_label('실제 측정 중량 (g)', exact=True).fill('20')
        page.get_by_label('표준 재료·실제 중량과 입출고 방향을 확인했습니다', exact=True).check()
        page.get_by_role('button', name='재고 확정', exact=True).click()
        page.get_by_role('dialog').wait_for(state='hidden')
        page.locator('body[aria-busy=false]').wait_for()
        page.reload(); sign_in()
        assert page.get_by_role('button', name='재료·중량 확인', exact=True).count() == 0
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        with closing(open_db()) as db:
            assert db.execute('SELECT quantity_g FROM inventory_items WHERE id=?', (lot,)).fetchone()[0] == 90
            assert db.execute('SELECT count(*) FROM inventory_event_changes WHERE event_id=?', (pending['event_id'],)).fetchone()[0] == 1
            assert db.execute('SELECT status FROM inventory_events WHERE id=?', (rejected['event_id'],)).fetchone()[0] == 'rejected'
            new_lot = db.execute('SELECT inventory_item_id FROM inventory_events WHERE id=?', (incoming_event['event_id'],)).fetchone()[0]
            assert new_lot != lot
            assert db.execute('SELECT quantity_g FROM inventory_items WHERE id=?', (new_lot,)).fetchone()[0] == 20
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close()
