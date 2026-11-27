# Vision main 통합 검증

작업·검증일: 2026-11-27 KST. 사용자가 남은 Vision 두 브랜치를 검사하고 문제를 해결한 뒤 병합하도록 승인했다. 기준 main은 `b617bb1`, Vision 구현은 `9159259`, 구형 연동 검증은 `e730d90`이다. 별도 worktree와 `codex/vision-main-integration-20261127`에서 작업했으며 원래 작업 공간의 미커밋 변경을 보존했다. 이번 작업은 서버 배포·실기기 촬영 리허설이 아니며 8단계 보류를 유지한다.

## 통합과 충돌 해결

- 최신 Vision 구현 병합은 파일 충돌 없이 진행됐다. 인식·OCR·재인식·갤러리·휴대폰 촬영·샘플·테스트를 포함했다. 기존 샘플 사진의 Git blob을 재사용했으며 사용자 사진·모델·로컬 DB·키·갤러리·결과 로그를 새로 올리지 않았다.
- 구형 검증 브랜치는 이전 Vision 스냅샷을 기반으로 해서 15개 add/add 충돌이 발생했다. 이 파일들은 최신 Vision 또는 현재 main 버전을 유지했다. 고유한 619행 검증 테스트와 당시 검증 문서는 가져오고, 테스트 import를 최신 `kini_inventory_sink`로 변경했다. 최신 인식·재인식 코드를 과거 버전으로 되돌리지 않았다.
- main의 엄격한 `kini_inventory_client.py`와 7단계 result bridge를 보존했다. 스트림용 sink도 같은 loopback URL 검사를 사용하고 환경 proxy·redirect를 비활성화했다. 임의 `allow_remote=True`를 허용하지 않으며 원격은 기존 SSH loopback 터널을 사용한다.
- 별도 공개 개수 재고 동기화는 `VISION_ALLOW_LEGACY_SUPABASE_WRITE=0`이 기본이다. 키만 있으면 자동 활성화되는 동작을 막았다. 명시적으로 1을 선택한 별도 용도만 허용하며, KINI_API_URL 또는 KINI_A_API_URL이 있으면 동시 활성화하지 않는다. KINI의 private g 단위 lot을 이 테이블로 전환하거나 복제하지 않았다.
- 기존 점검 도구의 `--write`는 `reset_demo`로 기록을 삭제하므로 운영 Supabase에서는 실행을 막았다. loopback 별도 시험 DB와 명시적 시험 환경 표시가 필요하며 `--yes`로 우회할 수 없다. 이번 PostgreSQL 검증은 신규 Docker DB만 사용했다.
- 휴대폰 비교 화면은 기본 모드에서 직접 DB 반영 버튼을 표시하지 않고 KINI의 실제 g 확인을 안내한다. 서버 commit API도 비활성 상태에서는 거부한다. 촬영·실시간/전후 비교·Unknown 라벨 기능은 기존 구현을 재사용한다. 기본 KINI 경로는 결과 JSON→후보 bridge→앱 표준 재료·실제 g·lot 확인이다.
- 구형 검증의 무인증 재고 입력 문제는 현재 main에서 이미 401로 차단됐다. 수정할 코드를 재구현하지 않고 오래된 strict xfail을 제거해 정상 회귀 시험으로 유지했다. 당시 발견 내용은 역사 기록으로 표시했다.

## 검증 조건과 결과

- Vision 독립 테스트: **89 passed / 7.48초**, Docker PostgreSQL 16.15의 별도 `kini_vision_test` + tmpfs 포함. pgserver가 없는 Windows에서는 명시적 loopback 시험 URL을 지원하도록 시험 도구만 보완했다. 새 DB 여부·host·DB 이름을 검사한다. 실제 PostgreSQL 함수·권한·RLS 경계는 미니 PostgREST를 통해 확인한 것이며 실제 Supabase 서비스 검증으로 표시하지 않는다. 컨테이너는 종료 후 제거했다.
- KINI 연동·스트림 sink 경계·기존 엄격 클라이언트·실제 저장 결과 bridge: **75 passed / 64.60초**, 합성 계정·임시 SQLite·로컬 HTTP. URL 제한·proxy/redirect 차단·대기·확정·거절·재시도·중복 차감·가구/세션 경계를 포함한다.
- 휴대폰 commit 거부 API와 기존 390px Chrome Vision 화면: **2 passed / 11.00초**. commit 시험은 합성 요청이며 실제 휴대폰 카메라나 인식 정확도 시험이 아니다. Node API 클라이언트 **14 passed**, 휴대폰 HTML inline JS 구문 확인 통과.
- 모델은 병합 중인 최신 코드로 저장소 실제 샘플 1쌍을 비교했다. YOLOE-v8m + CLIP ViT-B/16 + DINOv2 gallery/OpenVINO GPU, Intel i7-10700/UHD630, Python 3.12. 버터 1개 출고, 추가 없음, Unknown 전·후 2개, comparer 구간 **4.91초**. 모델 로딩은 구간에서 제외했다. 이전 검증 worktree의 공개 가중치·컴파일 캐시를 재사용하고 갤러리·상태는 새 임시 디렉터리를 사용했다. 냉장고 전체 정확도·최대 메모리·실기기 지연 측정으로 확대하지 않는다.
- 모델 환경은 기존 별도 Vision venv를 사용했다. 휴대폰 선택 의존성 aiohttp 3.14.5를 이 환경에만 추가했고 cryptography 50.0.2를 확인했다. 원래 프로젝트 venv와 서버 의존성은 변경하지 않았다. 운영 DB 직접 동기화·OpenAI 호출은 실행하지 않았으며 실제 OpenAI 호출은 0회다.
- 별도 추가 시험: 파괴적인 원격 DB CLI 시험 차단 **1 passed / 0.50초**. 카메라를 사용하지 않는 합성 전후 프레임을 넣어 휴대폰 기본 화면에 직접 반영/거절 버튼이 없고 재촬영으로 초기화되는 Chrome 시험 **1 passed / 2.78초**.
- 전체 저장소 회귀: `pytest tests` **452 passed, 264 skipped / 537.19초**. 운영 HTTPS opt-in 시험 3개는 명령에서 명시적으로 제외했다. PostgreSQL 명시 설정·선택 의존성/외부 실행 조건이 필요한 시험은 건너뛰었으며 실패 0개다. Vision 폴더는 자체 conftest가 루트 수집에서 제외하므로 위의 독립 89개 결과와 구분한다. 새 Chrome 단독 시험은 전체 수집 이후 추가돼 별도로 실행했다. 이번 통합 후 운영 환경 전체를 다시 시험했다는 뜻이 아니다.
- Python compileall, 휴대폰 inline JavaScript 구문, Git 충돌 마커·공백 검사와 변경 파일의 민감 키 패턴 검사를 통과했다. 가중치·캡처·인증서·DB·개인 갤러리가 Git 추적 대상에 없는 것을 확인했다.

## Git 통합

최신 Vision과 이전 연동 검증은 각각 merge commit으로 통합해 두 원격 브랜치의 기존 커밋을 보존했다. [통합 PR #41](https://github.com/Dajeong0315/KINI/pull/41)은 전체 회귀 완료 후 main에 병합하며, 원격 source tip이 main의 조상임을 재확인한 후 완료 브랜치를 정리한다. 원래 작업 공간의 미커밋 변경·기존 로컬 보관 태그·모델 검증 worktree는 보존한다.

## 적용 경계

이번 통합은 GitHub main의 Vision 소스 통합이다. 운영 서비스는 기존 `step7-20261127-af93684d66a2`이며 이번에 서버 source·환경·DB schema·권한을 바꾸지 않는다. 서버에 무거운 Vision 의존성을 자동 설치하거나 인식 서비스를 새로 상주시키지 않는다. 새 main을 서버 전체에 적용하는 작업과 실제 휴대폰·저울·발표 장비/네트워크 검증은 별도이며 완료로 표시하지 않는다.
