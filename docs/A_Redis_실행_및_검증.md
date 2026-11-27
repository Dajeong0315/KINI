# A Redis 연결·공개 카탈로그 캐시 실행

2026-11-25 / 브랜치 codex/redis-cache-integration.
팀 main f21be76을 통합한 A 데이터 플랫폼·사전 적재 후속 브랜치다.

## 구현 범위

- MemoryCache: 16MB payload/key 예산·1000 entries·TTL·LRU 제거. Python 객체/RSS 전체를 제한하는 것은 아니다.
- RedisCache: redis-py 8.2.0, GET/SET EX/DEL, 짧은 timeout·5초 장애 cooldown, 오류 원문 비노출.
- PublicCatalog: 승인된 published 로컬 공개 카탈로그만 cache-aside, release/version 키, ±10% TTL jitter.
- 실제 활성 pointer와 source 권리는 cache hit 때도 DB에서 확인한다. 캐시로 미승인 자료를 우회하지 않는다.
- 64개 고정 잠금으로 프로세스 내 동일 key 동시 miss를 합친다. 최대 2초 대기 후 DB로 전환한다.
  여러 프로세스의 분산 잠금은 아직 없다.
- 허용 필드로만 캐시 값을 검증한다. 개인 조건·원문·토큰·개인화 추천·현재 재고를 저장하지 않는다.
- PatternStats는 공개 조회 hit/miss/error/bypass와 공개 key hash의 횟수를 프로세스 메모리에만 보관한다.
  항목별 기록은 최대 1000개, 7일 미접근 항목을 다음 기록/요약 때 정리한다. 자동 cloud 전송은 없다.
- 최종 추천·B RAG retriever·OpenAI 답변 cache는 연결하지 않았다. FAQ 캐시는 아직 설계다.

## Docker 없이 실행

```dotenv
KINI_CACHE_BACKEND=memory
```

memory/redis/disabled 중 선택한다. memory가 기본이며 추가 서버가 필요 없다.
원본 자료는 반드시 사전 DB에 존재해야 한다. 데이터가 없다고 식약처 API를 호출하지 않는다.

## 로컬 Redis 선택 실행

```powershell
docker compose -f compose.redis.yaml up -d
python scripts/check_cache.py --backend redis
```

compose는 공식 redis:8.2-alpine image digest를 고정하고 host 127.0.0.1:6379에만 바인딩한다.
maxmemory=32MB/allkeys-lfu, RDB/AOF 저장 없음, 별도 kini-cache 프로젝트이며 다른 컨테이너를 변경하지 않는다.
공유/외부 접속용 설정은 아니다. 서버를 원격에 둘 경우 ACL/TLS·비밀 관리가 별도로 필요하다.
32MB는 Redis maxmemory 설정이며 전체 Docker/Redis 프로세스 메모리 사용량과 동일하지 않다.

```dotenv
KINI_CACHE_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
```

이 PC에는 위 로컬 설정을 .env에 추가하고 실제 컨테이너 연결을 확인했다.
.env는 Git에서 제외한다. 다른 PC의 기본 .env.example은 memory이며 Redis는 선택이다.
config/cache_policy.json은 factory 초기화 시 읽는다. 설정 변경 후 앱을 재시작한다.
캐시 장애는 DB 조회로 처리한다. DB 장애를 오래된 cache hit로 숨기지 않는다.

중지:

```powershell
docker compose -f compose.redis.yaml stop redis
```

## HTTP API

| API | 인증 | 동작 |
|---|---|---|
| GET /api/data/catalog/foods/{external_id}?source=mfds_nutrition | 없음 | 승인·배포된 공개 식품 상세 |
| GET /api/data/catalog/recipes/{external_id}?source=mfds_recipe | 없음 | 승인·배포된 공개 레시피 상세 |
| GET /api/data/cache/status | 로컬 Bearer session | backend·집계 카운트, raw query/key/URL/비밀은 반환하지 않음 |

성공 envelope는 item과 cache(status,backend)다.
cache status는 hit/miss/unavailable/bypass. unavailable이어도 DB 조회 성공이면 HTTP 200이다.
미배포·권리 미승인·없는 ID는 404이며 오류/없는 자료는 캐시에 저장하지 않는다.
source 이외 query parameter(가족 ID·건강 조건·원문 등)는 400으로 차단한다.
원본 SQLite 오류는 기존 Flask 500 경로이며 cache가 성공 응답으로 위장하지 않는다.

현재 운영 DB에는 공식 식품·레시피가 적재되지 않았으므로 일반 상세 조회 404는 정상이다.
이 연결이 실제 식약처 사전 적재나 Supabase→SQLite 다운로드를 완료한 것은 아니다.

## main 통합 시 데이터 보존

main의 공식 import는 data_sources(id,source_key,...)를 사용한다.
이전 A 공개 카탈로그의 data_sources(code,...)와 충돌하여 SQLite A 쪽을 catalog_data_sources로 분리했다.
이전 A DB는 컬럼을 확인해 기존 테이블을 rename하고 SQLite FK를 보존한 뒤 main schema를 생성한다.
Supabase public.data_sources 이름은 변경하지 않았다. 양쪽 source/release의 자동 통일은 아직 없다.
기존 DB 파일을 삭제하거나 main의 import 데이터·B 정책을 덮어쓰지 않는다.

## 검증

- 실제 Redis+임시 SQLite 합성 자료: 첫 read miss, 두 번째 hit 확인.
- 해당 KINI Redis만 실제 stop한 상태: unavailable 응답으로 DB의 동일 자료 반환, 이후 start로 복구.
- 시험 자료는 임시 SQLite이며 운영 가족/공식 식품 데이터를 변경하지 않았다.
- check_cache.py는 합성 캐시 키만 직접 삭제한다. FLUSHDB/FLUSHALL을 사용하지 않는다.
- tests/test_public_cache.py: 만료·메모리 제거·동시 조회 합치기·권리 철회·publish/rollback·오류 fallback·개인 필드 오염·HTTP·legacy source migration.
- tests/test_data_import.py와 tests/test_data_platform.py를 포함한 초기 통합 검사 33개 통과.
- 전체 최종 결과는 A_개발현황.md의 최신 Redis 항목을 따른다.

## 미완료

공식 식품 사전 적재(두 키 미설정), 원격→로컬 downloader, B 엔진의 공개 provider 통일,
FAQ/RAG cache, 다중 프로세스 잠금, 영속 패턴 분석·자동 지표 업로드는 남아 있다.
최신 main에 추가된 B 개인정보 게이트·OpenAI 경로는 통합했지만 이 작업에서 실제 모델/키로 실행하지 않았다.
