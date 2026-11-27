"""Explicit entry routes select the surface before browser initialization."""
import os

import pytest
from jinja2 import FileSystemLoader


@pytest.mark.parametrize('path,device',[('/fridge','fridge'),('/mobile','mobile'),('/','auto')])
def test_surface_entry_route(path,device):
    from app.main import app
    response=app.test_client().get(path)
    assert response.status_code==200
    assert f'data-device="{device}"' in response.get_data(as_text=True)


def test_installed_app_always_opens_mobile():
    from app.main import app
    manifest=app.test_client().get('/static/manifest.webmanifest').json
    assert manifest['start_url']=='/mobile'


def test_template_changes_are_served_without_restarting(tmp_path, monkeypatch):
    from app.main import app

    template = tmp_path / 'index.html'
    template.write_text('First tablet layout', encoding='utf-8')
    monkeypatch.setattr(app.jinja_env, 'loader', FileSystemLoader(tmp_path))
    client = app.test_client()
    assert client.get('/fridge').get_data(as_text=True) == 'First tablet layout'

    next_mtime = template.stat().st_mtime + 2
    template.write_text('Updated tablet layout', encoding='utf-8')
    os.utime(template, (next_mtime, next_mtime))
    assert client.get('/fridge').get_data(as_text=True) == 'Updated tablet layout'
