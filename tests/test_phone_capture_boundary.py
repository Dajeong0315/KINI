"""Phone capture endpoint boundary, synthetic request; no model/camera/cloud calls."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_phone_cannot_commit_when_legacy_count_inventory_is_disabled():
    pytest.importorskip('aiohttp')
    pytest.importorskip('cv2')
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    file = Path(__file__).resolve().parents[1] / 'phone_live/server.py'
    spec = importlib.util.spec_from_file_location('kini_phone_capture_test', file)
    phone = importlib.util.module_from_spec(spec);spec.loader.exec_module(phone)
    detector = phone.Detector.__new__(phone.Detector)
    detector.sync = None
    detector.sessions = {'synthetic-capture': {'result': {'added': [], 'removed': []}}}
    async def verify():
        app = web.Application()
        app['hub'] = SimpleNamespace(det=detector)
        app.router.add_post('/api/compare/commit', phone.api_compare_commit)
        async with TestClient(TestServer(app)) as client:
            response = await client.post('/api/compare/commit', json={'cid':'synthetic-capture','decision':'confirm'})
            assert response.status == 400
            assert 'KINI' in (await response.json())['error']
    asyncio.run(verify())
