"""Synthetic phone frame UI; no camera permission, real model, or cloud writes."""
from pathlib import Path
from threading import Thread

from flask import Flask, jsonify, send_from_directory
from werkzeug.serving import make_server
from test_live_flow import browser_page


def test_default_compare_ui_has_no_direct_inventory_commit(browser_page):
    static = Path(__file__).resolve().parents[2] / 'phone_live/static'
    app = Flask('phone_capture_controls', static_folder=str(static), static_url_path='/static')
    @app.get('/compare')
    def screen(): return send_from_directory(static, 'compare.html')
    @app.get('/api/info')
    def info(): return jsonify(db=False)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True);thread.start()
    page = browser_page
    try:
        page.add_init_script("Object.defineProperty(navigator, 'userAgent', {get: () => 'Mozilla/5.0 (Linux; Android 13) Chrome/130.0 Mobile Safari/537.36'})")
        page.goto(f'http://127.0.0.1:{server.server_port}/compare')
        page.evaluate("""() => {
            const canvas = document.createElement('canvas'); canvas.width = canvas.height = 2;
            shots = {before:{canvas,w:2,h:2},after:{canvas,w:2,h:2}};
            res = {ms:1000,added:[],removed:[],reasons:[],before:{w:2,h:2,dets:[]},after:{w:2,h:2,dets:[]}};
            document.getElementById('start').style.display='none';
            update();
        }""")
        assert page.locator('[onclick="commit(\'confirm\')"]').count() == 0
        assert page.locator('[onclick="commit(\'reject\')"]').count() == 0
        assert '실제 g' in page.locator('#sheetBody').inner_text()
        page.get_by_role('button', name='↺ 처음부터', exact=True).click()
        assert 'show' not in page.locator('#sheet').get_attribute('class').split()
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()
