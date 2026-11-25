from __future__ import annotations
import json, os
from urllib.parse import urlparse
from pathlib import Path
from .recommendation_models import RecommendationRequest
from .ollama import configured_model

PROMPT_VERSION='recommendation-1'
PROMPT_DIR=Path(__file__).resolve().parents[1] / 'prompts'

def _local_settings():
    base_url=os.getenv('OLLAMA_BASE_URL','http://localhost:11434')
    if urlparse(base_url).hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('개인화 요청은 로컬 Ollama만 사용할 수 있습니다.')
    return {'model':configured_model(),'base_url':base_url,'temperature':0,'num_predict':160,'num_ctx':4096,'reasoning':False,'seed':7,'client_kwargs':{'timeout':90.0}}

def extract_preference_signal(text, ingredient_names):
    """Accept only short, exact food-preference statements; no raw text leaves the process."""
    from .recommendation_models import PreferenceSignal
    import re
    normalized = ' '.join(text.strip().split())
    for name in sorted(ingredient_names, key=len, reverse=True):
        match = re.fullmatch(re.escape(name) + r'(?:은|는|이|가)?\s*(싫어|싫어요|좋아|좋아요|빼줘|빼주세요)[.!]?', normalized)
        if match:
            return PreferenceSignal(feature_type='ingredient', feature_value=name, signal='avoid' if match.group(1) in ('싫어','싫어요','빼줘','빼주세요') else 'prefer')
    return None

def parse_recommendation_request(text: str) -> RecommendationRequest:
    from langchain_ollama import ChatOllama
    model=ChatOllama(**_local_settings())
    chain=model.with_structured_output(RecommendationRequest)
    return chain.invoke((PROMPT_DIR/'recommendation_parse.md').read_text(encoding='utf-8')+'\n'+text)

def explain_recommendation(result: dict) -> str:
    names=[c['name'] for c in result['recommendations']]
    fallback=('추천 메뉴: '+', '.join(names)+'. 영양 정보는 데모 추정치입니다.') if names else '조건에 맞는 추천 메뉴가 없습니다.'
    try:
        from langchain_ollama import ChatOllama
        from pydantic import BaseModel
        class Explanation(BaseModel):
            reply: str
        model=ChatOllama(**_local_settings())
        facts={'recommended_recipes':[{'name':c['name'],'cook_time_minutes':c['cook_time_minutes'], 'preference_signals':[{k:s[k] for k in ('feature_type','feature_value','inference') if k in s} for s in c.get('explanation_facts', {}).get('preference_signals', [])]} for c in result['recommendations']]}
        response=model.with_structured_output(Explanation).invoke((PROMPT_DIR/'recommendation_explain.md').read_text(encoding='utf-8')+'\n'+json.dumps(facts,ensure_ascii=False))
        # Generated prose is not returned as authoritative facts; the deterministic template is authoritative.
        if response and response.reply and all(name in response.reply for name in names) and response.reply.strip()==fallback: return response.reply
    except Exception:
        pass
    return fallback
