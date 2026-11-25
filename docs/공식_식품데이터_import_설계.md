# 공식 식품·영양·알레르겐 데이터 import 설계

## 실행 경로 분리

추천 요청은 로컬 SQLite만 읽으며 외부 HTTP, 검색(Tavily 포함), 법령 HTML 스크래핑을 호출하지 않는다. import는 운영자가 별도 CLI로 명시 실행한다. 이 경계는 오프라인 동작, API 장애 격리, release 기반 재현성과 안전 근거 고정을 위해 필요하다.

흐름은 `원천 데이터 → staging → 검증 → quarantine → validated → 명시적 publish → 추천 DB pointer`다. raw 원본은 Git 제외 경로에 두며 DB에는 checksum·안전한 참조 메타데이터만 기록한다. import 실패나 중단은 기존 published release를 바꾸지 않는다.

## 데이터 의미와 안전 경계

- `demo`: 데모 값이며 실제 안전 판단 근거가 아니다.
- `official_imported`: 출처·release·checksum을 보존한 공식 일반 식품 영양 데이터다.
- `verified_product`: 특정 제품 라벨과 필요한 확인 절차가 검토된 상태다.
- `unknown`: 출처 또는 검증이 부족하며 안전하다고 판단하지 않는다.

일반 재료는 레시피·재고의 내부 canonical 개체이고, 공식 식품은 release별 원천 레코드이며, 브랜드 제품은 라벨·교차접촉·제조 차이가 있는 별도 개체다. 이름 유사도만으로 연결하지 않으며 운영자가 승인한 mapping만 공식 영양값에 쓴다. 영양 DB만으로 제품 성분표, 알레르겐 표시, 교차접촉 정보를 알 수 없으므로 생성·추론하지 않는다.

알레르겐 표시 기준은 사람이 검토한 versioned JSON reference로 import한다. 특정 재료/제품의 실제 함유 태그와는 분리한다. 법령 HTML을 런타임에 수집하거나 해석하지 않는다.

## Release, rollback, 개인정보

`data_release_pointers`는 현재 영양 release와 알레르겐 reference version을 보관한다. `--publish --confirm-publish`만 pointer를 바꾸며, 이전 release는 `retired`로 남아 rollback할 수 있다. 추천 결과에는 사용한 release ID와 provenance를 저장한다. 이후 pointer가 변한 과거 결과는 자동 수정하지 않고 `stale_unvalidated`로 표시한다.

import 테이블은 가족, 건강 제한, 식사 기록, 대화 등 개인정보와 분리한다. 원본 payload·로컬 원본 경로는 상태 API나 로그로 공개하지 않는다.

이번 MVP는 fixture 기반 MFDS import, 선택적 명시 API 호출 구조, 검증/quarantine/release/publish, 알레르겐 reference fixture와 읽기 전용 상태 API까지다. 대용량 전체 import, API 키 발급, 바코드/OCR, 제품 라벨·교차접촉 자동 판정은 다음 단계다. 실제 서비스 전에는 인증, 권한, 암호화, 감사·백업·복구가 필요하다.
