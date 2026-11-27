"""Cache only approved published public catalog rows. No personal recommendations."""
from collections import Counter,OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock,RLock
import hashlib
import json
import math
import os
import random
import re
import time
from pathlib import Path
from uuid import UUID
from .integrations.cache import MemoryCache,RedisCache,DisabledCache,CacheUnavailable

SOURCES=frozenset(('mfds_recipe','mfds_nutrition','10000recipe'))
NUTRIENTS=frozenset(('energy_kcal','protein_g','carbohydrate_g','fat_g','sodium_mg','sugar_g'))


class PublicRecordMissing(LookupError): pass


def cache_key(source,release,version,kind,external_id):
    if source not in SOURCES or kind not in ('food','recipe') or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',external_id):
        raise ValueError('허용된 공개 자료 ID가 필요합니다.')
    if type(version) is not int or version<1: raise ValueError('잘못된 캐시 버전입니다.')
    release=str(UUID(release))
    return f'kini:v1:public:{source}:{release}:cv{version}:{kind}:{external_id}'


class PatternStats:
    """Bounded process-local counts, never user text/IDs. No automatic cloud export."""
    def __init__(self,clock=time.monotonic):
        self.counts=Counter(); self.hot=OrderedDict(); self.clock=clock; self.lock=RLock()
    def record(self,kind,outcome,key=None):
        with self.lock:
            self.counts[(kind,outcome)]+=1
            now=self.clock()
            for old in [k for k,v in self.hot.items() if now-v[0]>=7*86400]: self.hot.pop(old)
            if key:
                digest=hashlib.sha256(key.encode()).hexdigest()
                old=self.hot.pop(digest,(now,0))
                self.hot[digest]=(now,old[1]+1)
                while len(self.hot)>1000: self.hot.popitem(last=False)
    def summary(self):
        with self.lock:
            now=self.clock()
            for old in [k for k,v in self.hot.items() if now-v[0]>=7*86400]: self.hot.pop(old)
            return {'counts':{f'{kind}_{outcome}':value for (kind,outcome),value in self.counts.items()},
                    'tracked_public_key_count':len(self.hot),'retention':'process_memory_only',
                    'raw_queries_recorded':False,'automatic_cloud_export':False}


@dataclass
class ReadResult:
    item:dict
    cache_status:str
    backend:str


def _validate(value):
    common={'kind','source','release_id','cache_version','external_id','name'}
    food={'basis_amount','basis_unit','nutrients'}
    recipe={'servings','cook_time_minutes','ingredients_text','steps','nutrition'}
    if not isinstance(value,dict) or value.get('kind') not in ('food','recipe'): raise ValueError('공개 자료 형식 오류')
    fields=common|(food if value['kind']=='food' else recipe)
    if set(value)!=fields: raise ValueError('허용되지 않은 공개 자료 필드')
    cache_key(value['source'],value['release_id'],value['cache_version'],value['kind'],value['external_id'])
    if not isinstance(value['name'],str): raise ValueError('이름 형식 오류')
    nutrients=value['nutrients'] if value['kind']=='food' else value['nutrition']
    if not isinstance(nutrients,dict) or not set(nutrients)<=NUTRIENTS: raise ValueError('영양 필드 오류')
    for item in nutrients.values():
        amount=item if value['kind']=='food' else item.get('amount') if isinstance(item,dict) else None
        if type(amount) not in (float,int) or not math.isfinite(amount) or amount<0: raise ValueError('영양 수치 오류')
        if value['kind']=='recipe' and (set(item)!={'amount','basis_amount','basis_unit'} or
            type(item['basis_amount']) not in (int,float) or not math.isfinite(item['basis_amount']) or
            item['basis_amount']<=0 or item['basis_unit'] not in ('g','ml','serving')): raise ValueError('영양 기준 오류')
    if value['kind']=='food':
        if type(value['basis_amount']) not in (float,int) or not math.isfinite(value['basis_amount']) or value['basis_amount']<=0 or value['basis_unit'] not in ('g','ml'): raise ValueError('기준량 오류')
    elif not isinstance(value['steps'],list) or any(not isinstance(step,str) for step in value['steps']) or not isinstance(value['ingredients_text'],str):
        raise ValueError('공개 레시피 형식 오류')
    if value['kind']=='recipe':
        servings=value['servings']; minutes=value['cook_time_minutes']
        if servings is not None and (type(servings) not in (int,float) or not math.isfinite(servings) or servings<=0): raise ValueError('인분 오류')
        if minutes is not None and (type(minutes) is not int or minutes<=0): raise ValueError('조리 시간 오류')
    return value


class PublicCatalog:
    def __init__(self,cache=None,ttl=86400,jitter=None,stats=None):
        self.cache=cache or MemoryCache(); self.ttl=ttl
        self.jitter=jitter or (lambda:random.uniform(.9,1.1)); self.stats=stats or PatternStats()
        self.locks=[Lock() for _ in range(64)]

    def _pointer(self,db,source):
        return db.execute('''SELECT p.release_id,p.cache_version FROM active_data_releases p
            JOIN data_releases r ON r.id=p.release_id AND r.source_code=p.source_code
            JOIN catalog_data_sources s ON s.code=p.source_code
            WHERE p.source_code=? AND r.status='published' AND s.rights_status='approved' ''',(source,)).fetchone()

    def _load(self,db,source,release,version,kind,external_id):
        table='catalog_foods' if kind=='food' else 'catalog_recipes'
        row=db.execute(f'SELECT * FROM {table} WHERE release_id=? AND external_id=?',(release,external_id)).fetchone()
        if not row: raise PublicRecordMissing()
        result={'kind':kind,'source':source,'release_id':release,'cache_version':version,'external_id':external_id,'name':row['name']}
        if kind=='food':
            result.update({'basis_amount':row['basis_amount'],'basis_unit':row['basis_unit'],
                'nutrients':{r['nutrient']:r['amount'] for r in db.execute('SELECT nutrient,amount FROM catalog_food_nutrients WHERE release_id=? AND food_id=?',(release,external_id))}})
        else:
            result.update({'servings':row['servings'],'cook_time_minutes':row['cook_time_minutes'],'ingredients_text':row['ingredients_text'],
                'steps':[r[0] for r in db.execute('SELECT instruction FROM catalog_recipe_steps WHERE release_id=? AND recipe_id=? ORDER BY step_no',(release,external_id))],
                'nutrition':{r['nutrient']:{'amount':r['amount'],'basis_amount':r['basis_amount'],'basis_unit':r['basis_unit']} for r in db.execute('SELECT * FROM catalog_recipe_nutrients WHERE release_id=? AND recipe_id=?',(release,external_id))}})
        return _validate(result)

    def read(self,db,source,kind,external_id):
        # Validate request before touching cache: no free text, IDs or health filters.
        if source not in SOURCES or kind not in ('food','recipe') or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',external_id): raise ValueError('공개 식품 ID 오류')
        pointer=self._pointer(db,source)
        if not pointer: raise PublicRecordMissing()
        release,version=pointer['release_id'],pointer['cache_version']
        key=cache_key(source,release,version,kind,external_id)
        lock=self.locks[int(hashlib.sha256(key.encode()).hexdigest()[:8],16)%len(self.locks)]
        acquired=lock.acquire(timeout=2)
        try:
            if not acquired or self.cache.backend=='disabled':
                self.stats.record(kind,'bypass',key)
                return ReadResult(self._load(db,source,release,version,kind,external_id),'bypass',self.cache.backend)
            status='miss'
            try:
                raw=self.cache.get(key)
                if raw is not None:
                    if len(raw)>65536: raise ValueError('캐시 값 초과')
                    item=_validate(json.loads(raw))
                    if cache_key(item['source'],item['release_id'],item['cache_version'],item['kind'],item['external_id'])!=key: raise ValueError('캐시 키 불일치')
                    self.stats.record(kind,'hit',key)
                    return ReadResult(item,'hit',self.cache.backend)
            except CacheUnavailable:
                status='unavailable'; self.stats.record(kind,'cache_error')
            except (ValueError,TypeError):
                self.stats.record(kind,'cache_error')
                try: self.cache.delete(key)
                except CacheUnavailable: status='unavailable'
            item=self._load(db,source,release,version,kind,external_id)
            self.stats.record(kind,'miss',key)
            if status!='unavailable':
                try:
                    ttl=self.ttl[kind] if isinstance(self.ttl,dict) else self.ttl
                    self.cache.set(key,json.dumps(item,ensure_ascii=False,allow_nan=False).encode(),max(1,int(ttl*self.jitter())))
                except CacheUnavailable:
                    status='unavailable'; self.stats.record(kind,'cache_error')
            return ReadResult(item,status,self.cache.backend)
        finally:
            if acquired: lock.release()


@lru_cache(maxsize=4)
def _service(backend,url,catalog_backend='app'):
    policy=json.loads((Path(__file__).resolve().parents[2]/'config/cache_policy.json').read_text(encoding='utf-8'))
    if not policy['enabled']: backend='disabled'
    if backend=='redis': cache=RedisCache(url)
    elif backend=='memory': cache=MemoryCache(max_bytes=policy['memory_cache_target_bytes'],max_entries=policy['max_entries'])
    elif backend=='disabled': cache=DisabledCache()
    else: raise ValueError('캐시 backend는 memory/redis/disabled입니다.')
    ratio=policy['ttl_jitter_ratio']
    catalog_type=PublicCatalog
    if catalog_backend=='supabase':
        from .supabase_catalog import SupabasePublicCatalog
        catalog_type=SupabasePublicCatalog
    elif catalog_backend!='app':
        raise ValueError('공개 카탈로그 backend는 app/supabase입니다.')
    return catalog_type(cache,ttl={'food':policy['targets']['food_detail']['ttl_seconds'],
        'recipe':policy['targets']['recipe_detail']['ttl_seconds']},jitter=lambda:random.uniform(1-ratio,1+ratio))


def service():
    return _service(os.getenv('KINI_CACHE_BACKEND','memory'),os.getenv('REDIS_URL','redis://127.0.0.1:6379/0'),
                    os.getenv('KINI_PUBLIC_CATALOG_BACKEND','app'))
