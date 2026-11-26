# PostgreSQL 재료 검색·단위 변환

작업·검증일: 2026-11-26 (KST). 기준 main: `12bab13` (PR #37 병합).
작업 브랜치: `codex/postgres-ingredient-tools`.

## 완료 범위

PostgreSQL에서 501로 차단되던 두 API를 지원한다.
화면·인식 모델·Supabase 직접 적용은 변경하지 않는다.

| 요청 | 기존 응답 계약 |
| --- | --- |
| `GET /api/ingredients/search?q=재료명` | `ingredients` 배열: id, canonical_name, category, source_type, is_composite, requires_label_check |
| `POST /api/inventory/normalize` | `ingredient`, `conversion`, `requires_confirmation` |

POST 요청 필드는 기존과 동일한 `name`, `quantity`, `unit`이다.
quantity는 0 초과·1,000,000 이하의 유한 수, name은 1~150자, unit은 1~30자다.
잘못된 요청은 400이다. 유효한 요청에서 재료를 못 찾거나 단위를 환산할 수 없는 것은
HTTP 오류가 아니라 200 + 확인 필요 상태다. 미등록 재료를 자동 등록하지 않는다.

PostgreSQL 두 API는 가구 `Authorization: Bearer <세션>`이 필요하다.
무효·만료/누락은 401, 권한 조건 불충족은 403이다. owner/editor/viewer 모두 조회할 수 있다.
가구별 재고가 아니라 공통 재료 카탈로그만 읽으므로 다른 가구의 로그인도 같은 공통 검색 결과를 받는다.
가구별 재고·가족·건강정보는 반환하지 않는다.
응답은 PostgreSQL 기존 after_request 정책에 따라 no-store다.
SQLite의 기존 무인증 공개 카탈로그 조회 계약은 유지한다.

## 재료 검색·정규화

- 활성 재료만, 표준 이름 부분 문자열로 검색한다. canonical_name이 null이면 name을 사용한다.
- 최대 30개, 이름/id 순서. 빈 검색어 또는 150자 초과는 400이다.
- SQL 바인딩과 PostgreSQL strpos/SQLite instr를 사용한다. %, _는 와일드카드가 아니다.
- 검색은 표준 이름 선택용이며 별칭 자동완성·유사도 검색을 새로 추가한 것은 아니다.
- 정규화는 기존 NFKC·공백·포장 수량 정리와 정확 일치/별칭 확인을 유지한다.
- 미등록 unknown, 복수 일치 ambiguous, 복합/제품 needs_label_check는 사용자 확인 상태다.
- 별칭의 unknown 출처·낮은 신뢰도·비정상 신뢰도는 자동 일치로 승인하지 않는다.
  이번에 별칭 신뢰도의 유한성 및 1 이하 조건도 확인한다.
- ambiguous/unknown은 재료별 환산 근거에 자동 연결하지 않는다. 명시적 g/kg 변환 자체는 할 수 있지만
  그 무게에 대응할 표준 재료까지 확정됐다는 뜻은 아니다.

## 단위 변환과 확인

- g/kg는 g로 변환한다. ml/mL/L/l은 부피로만 정리하고 g는 null이다. 밀도를 임의로 가정하지 않는다.
- 개/컵/봉지 등은 해당 재료의 from_unit→g 근거가 있어야 한다.
- source_type은 official_imported/verified_product, confidence는 유한한 0.95~1,
  factor는 유한한 양수여야 한다. 기존 unit/grams만 있는 행은 검증된 근거로 취급하지 않는다.
- 신뢰 가능한 환산 근거가 서로 다른 factor를 제시하면 임의 선택하지 않고 확인을 요구한다.
  곱셈 결과가 무한대가 되는 경우도 g를 반환하지 않는다.
- 재료별 환산값이 있어도 실제 상품·상태가 근거와 일치하는지 확인해야 하므로
  conversion.warning이 있으면 최상위 requires_confirmation도 true다.
- 근거가 없으면 conversion.status=needs_confirmation, quantity/unit/quantity_g는 null이다.
- 데모 재료와 제품 경고도 유지한다. 환산 성공은 입출고 확정이나 알레르기 안전 승인과 다르다.

## DB·C 연동

`008_ingredient_normalization.sql`은 별칭의 source_type/confidence/alias_type,
단위 환산의 from_unit/to_unit/factor/confidence/source_type/note 컬럼을 추가한다.
001~007은 수정하지 않는다. 기존 alias·unit·grams를 삭제하거나 승인된 값으로 자동 보정하지 않는다.
기존 행의 source_type 기본값은 unknown이다. 별도의 명시적 migration 실행만 사용한다.

요청 트랜잭션은 PostgreSQL READ ONLY다. 재고·A 이벤트·버전·식사/섭취·권한 정보를 쓰지 않는다.
요청 처리에서 PostgreSQL DDL·demo seed·외부 API를 실행하지 않는다.

**C 연결 필요:** PostgreSQL에서 두 호출에 기존 가구 Bearer를 전달해야 한다.
현재 `app/static/api_client.js`의 addInventory 정규화 호출은 인증 옵션이 없어 C 담당자가
기존 request 헬퍼의 세 번째 인자 `true`를 전달해야 한다(`request(path, body, true)`;
GET은 body 자리에 undefined). C 파일은 이번 서버 작업에서 수정하지 않았다.
UI는 requires_confirmation과 ingredient/conversion 경고를 표시하고, 재료·실제 중량·제품 확인을 거쳐
A pending 생성→명시적 confirm으로 이어야 한다. quantity_g가 있다는 이유만으로 자동 확정하지 않는다.
API 응답 필드 이름은 변경하지 않았다.

## 검증

`tests/test_ingredient_tools_postgres.py`: 합성 데이터·실제 PostgreSQL 16.15·루프백 HTTP.

- SI 질량/부피, 근거 없는 개수/컵/봉지, 승인 환산·충돌·낮은 신뢰도·비정상 값·overflow.
- 한글 별칭, 복수 일치·불확실 별칭·비활성·미등록·제품 확인.
- 검색 literal/최대 30개/표준 이름 fallback, 로그인·viewer·공통 카탈로그 경계.
- 잘못된 입력, 읽기 전용 트랜잭션의 쓰기 거절, 재고/이벤트/섭취 불변.
- 007 DB의 기존 alias/unit/grams 및 재고를 보존하는 008 적용·재실행과 unknown 기본값.

SQLite 공유 환산 회귀는 `tests/test_data_quality.py`에 충돌·overflow 시험을 추가했다.
전체 실행기는 이번 PostgreSQL 모듈을 기본 검증 목록에 포함한다.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:KINI_USE_OPENAI='0'
$env:PYTHONPATH=(Get-Location).Path
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_vision_postgres.ps1 -Python <프로젝트-Python-절대경로>
```

신규만 실행하려면 `-TestPaths tests/test_ingredient_tools_postgres.py`를 덧붙인다.
실제 Supabase/AWS 적용·실제 가족 데이터·카메라·외부 AI 호출은 수행하지 않는다.
검증 결과:

- 실제 PostgreSQL 16.15 전체 **250 passed / 298.33초**. 기존 209개와 신규 41개를 같은 실행에서 통과했다.
- 신규 PostgreSQL 모듈 단독: **41 passed / 53.26초**.
- 일반 전체 **296 passed, 250 skipped / 243.10초**. 기존 Chrome E2E 13개 포함.
  PostgreSQL 전용 skip은 위 별도 실제 DB 묶음에서 모두 통과했다.
- SQLite 데이터 품질/환산 관련 단독: **17 passed / 10.51초**.
- JavaScript **35 passed**. C 화면·스타일·UI 시험 파일 변경 없음.
- `git diff --check` 통과. 자신의 임시 DB 컨테이너와 업그레이드 schema 정리 완료.

표시한 실행 시간은 테스트 묶음 소요 시간이며 API 응답 지연·서비스 부하 성능 수치가 아니다.
