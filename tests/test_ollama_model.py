import pytest

from app.services import ollama
from app.services.errors import ServiceError


@pytest.mark.parametrize('value', [None, '', '   '])
def test_empty_model_configuration_uses_qwen35(monkeypatch, value):
    if value is None:
        monkeypatch.delenv('OLLAMA_MODEL', raising=False)
    else:
        monkeypatch.setenv('OLLAMA_MODEL', value)
    assert ollama.configured_model() == 'qwen3.5:4b-q4_K_M'


@pytest.mark.parametrize('model,legacy_prefix', [
    ('qwen3.5:4b-q4_K_M', False),
    ('qwen3:4b', True),
    ('qwen2.5:3b-instruct-q4_K_M', False),
])
def test_model_control_and_validated_fact_reply(monkeypatch, model, legacy_prefix):
    captured = {}

    class Response:
        status_code = 200
        ok = True

        def json(self):
            return {'message': {'content': '{"reply":"달걀 4개 있습니다."}'}}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    Session.post = staticmethod(post)

    monkeypatch.setenv('OLLAMA_MODEL', '  ' + model + '  ')
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
    monkeypatch.setattr(ollama.requests, 'post', post)
    monkeypatch.setattr(ollama.requests, 'Session', Session)
    answer, _ = ollama.ask_ollama('달걀 몇 개 있어?', '달걀 4개', {'item': '달걀', 'quantity': 4})
    payload = captured['json']
    assert payload['model'] == model
    assert payload['think'] is False
    assert payload['options']['num_ctx'] == 4096
    assert payload['messages'][0]['content'].startswith('/no_think\n') is legacy_prefix
    assert payload['format']['required'] == ['reply']
    assert answer == '달걀 4개 있습니다.'


def test_null_reply_is_reported_as_service_error(monkeypatch):
    class Response:
        status_code = 200
        ok = True

        def json(self):
            return {'message': {'content': '{"reply":null}'}}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setenv('OLLAMA_MODEL', 'qwen3.5:4b-q4_K_M')
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
    monkeypatch.setattr(ollama.requests, 'post', lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(ollama.requests, 'Session', Session)
    with pytest.raises(ServiceError, match='답변 형식'):
        ollama.ask_ollama('달걀 몇 개 있어?', '달걀 4개', {'quantity': 4})
