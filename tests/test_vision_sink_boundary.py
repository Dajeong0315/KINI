"""The optional stream sink preserves the same local-only transport boundary."""
import pytest
import requests
from fridge_vision.kini_inventory_sink import KiniApiError, KiniInventoryClient


@pytest.mark.parametrize('url', ['https://example.com', 'http://127.0.0.1@evil.test',
    'http://localhost/path', 'http://127.0.0.1?proxy=evil', 'https://127.0.0.1'])
def test_stream_sink_rejects_untrusted_origins(url):
    with pytest.raises(ValueError):
        KiniInventoryClient(url, token='synthetic-token')


def test_stream_sink_cannot_opt_into_arbitrary_remote():
    with pytest.raises(ValueError):
        KiniInventoryClient('http://127.0.0.1', allow_remote=True)


def test_stream_sink_disables_proxy_and_redirects(monkeypatch):
    session = requests.Session()
    calls = []
    def redirect(method, url, **kwargs):
        calls.append(kwargs)
        reply = requests.Response()
        reply.status_code = 302
        reply._content = b'{}'
        return reply
    monkeypatch.setattr(session, 'request', redirect)
    client = KiniInventoryClient('http://127.0.0.1', token='synthetic-token', session=session)
    assert not session.trust_env
    with pytest.raises(KiniApiError) as error:
        client.snapshot()
    assert error.value.status == 302
    assert calls[0]['allow_redirects'] is False
