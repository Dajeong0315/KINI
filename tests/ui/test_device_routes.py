"""Explicit entry routes select the surface before browser initialization."""
import pytest


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
