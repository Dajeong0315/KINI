import pytest
from app.services.ollama import configured_model
from app.services.recommendation_llm import _local_settings, explain_recommendation


@pytest.mark.parametrize('value', [None, '', '   ', ' qwen3:4b '])
def test_recommendation_and_chat_use_same_model(monkeypatch, value):
    if value is None:
        monkeypatch.delenv('OLLAMA_MODEL', raising=False)
    else:
        monkeypatch.setenv('OLLAMA_MODEL', value)
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    settings = _local_settings()
    assert settings['model'] == configured_model()
    assert settings['model'] == ('qwen3:4b' if value and value.strip() else 'qwen3.5:4b-q4_K_M')
    assert settings['client_kwargs']['timeout'] == 90.0


def test_chatollama_receives_non_thinking_context_parameters(monkeypatch):
    from langchain_core.messages import HumanMessage
    from langchain_ollama import ChatOllama
    monkeypatch.delenv('OLLAMA_MODEL', raising=False)
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    params = ChatOllama(**_local_settings())._chat_params([HumanMessage(content='test')])
    assert params['think'] is False
    assert params['options']['num_ctx'] == 4096
    assert params['options']['seed'] == 7


def test_recommendation_chain_rejects_remote_ollama_and_falls_back(monkeypatch):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'https://example.com')
    with pytest.raises(ValueError):
        _local_settings()
    assert explain_recommendation({'recommendations':[]}) == '조건에 맞는 추천 메뉴가 없습니다.'
