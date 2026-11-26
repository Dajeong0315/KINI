# 추천 데이터 provider 계약 (1단계)

작성일: 2026-11-26  
범위: B 추천 후보의 영양 데이터 공급 경계

## 책임 분리

- `RecommendationNutritionProvider`가 추천 엔진의 단일 영양 provider 계약이다.
- `catalog_*`는 A의 공개 카탈로그다. `catalog_ingredient_links`의 검토된 연결, `active_data_releases`의 현재 release, `published` 상태, `catalog_data_sources.rights_status=approved`를 모두 만족할 때만 사용한다. 이름이나 숫자 ID를 추측해 연결하지 않는다.
- 기존 `official_*`는 식약처 import release의 명시적 매핑 경로로 유지된다. provider가 직접 덮어쓰지 않으며 기존 품질 검사와 provenance를 보존한다.
- 공개 카탈로그 연결이 없거나 검증되지 않은 경우 기존 로컬 `ingredient_nutrition`의 `demo`/`official_imported`/`verified_product` 값만 fallback으로 허용한다. demo fallback은 응답의 `data_quality=demo_data`, `nutrition_confidence=데모 추정치`로 표시된다.
- 허용된 fallback도 없거나 수치가 누락·음수·비유한 값이면 `nutrition_unknown`으로 후보에서 제외한다.

## 캐시와 개인정보

공개 catalog cache는 source/release/cache version/kind/external ID만 키와 값에 포함한다. 개인 재고, 알레르기, 건강 제한, 사용자·가구·기기 ID, 토큰, 원문 음성·대화는 provider 또는 캐시에 전달하지 않는다. 캐시 장애는 공개 SQLite catalog를 직접 읽는 `unavailable` 경로로 처리하며, 그마저 실패하면 개인 데이터 fallback을 만들지 않고 후보를 제외한다.

## 기존 API 계약

`POST /api/recommendations`의 응답 필드와 상태값은 변경하지 않았다. 기존 `nutrition_per_serving`, `nutrition_confidence`, `data_quality`, `data_provenance_summary`, `warnings`, `requires_confirmation`을 유지하고, C 화면이 의존하는 템플릿·정적 파일은 수정하지 않았다. `inventory_usage.nutrition_source`는 서버 내부 provenance를 확인하기 위한 추가 필드다.

## 검증

`tests/test_recommendation_provider.py`는 승인 published catalog 연결, 누락·무효 데이터, cache 장애 경계를 검증한다. 관련 실행 명령:

```text
PYTHONPATH=. pytest -q tests/test_recommendation_provider.py tests/test_recommendation.py tests/test_public_cache.py
```

