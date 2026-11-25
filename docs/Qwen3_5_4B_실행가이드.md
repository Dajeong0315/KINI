# Qwen3.5 4B 로컬 실행

기본 모델은 `qwen3.5:4b-q4_K_M`이다. Ollama 모델 파일은 별도 다운로드하며 저장소에 넣지 않는다. 로컬 실행에는 외부 AI API 키가 필요하지 않다.

## 준비

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3.5:4b-q4_K_M
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

기존 `.env`의 모델명은 자동으로 덮어쓰지 않는다. 다음 값을 확인하고 앱을 재시작한다.

```env
OLLAMA_MODEL=qwen3.5:4b-q4_K_M
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

음성을 사용하려면 `python scripts/download_voice_models.py`로 Whisper·Piper 모델도 준비한다. 최초 모델 준비에는 인터넷이 필요하며, 이후 STT/TTS는 로컬 파일을 사용한다.

## 실행과 확인

```powershell
.\.venv\Scripts\python.exe scripts/local_runtime.py --check
.\.venv\Scripts\python.exe scripts/local_runtime.py
```

실행 도구는 로컬 Ollama와 선택된 모델을 확인하고 웹 앱을 실행한다. 웹 주소는 http://127.0.0.1:5000 이다. `OLLAMA_NO_CLOUD=1`은 Ollama 서버 시작 시 적용되며, 이미 실행 중인 서버의 설정을 변경하려면 해당 서버를 재시작해야 한다. 별도로 설정한 외부 레시피·영양 API는 이 환경 변수로 차단되지 않는다.

앱은 `think:false`, 컨텍스트 4096, 짧은 출력 예산을 사용한다. Qwen3.5에는 이전 Qwen3용 `/no_think` 접두사를 붙이지 않는다. 사실 재표현은 reply JSON 스키마로 요청하며 출력 오류가 있으면 기존 응답 경로에서 처리한다.

가족 추천 요청 해석과 설명도 채팅과 같은 `OLLAMA_MODEL` 설정을 사용한다. 설정이 없거나 공백이면 같은 기본 모델을 사용하고, 명시한 기존 모델은 유지한다. 추천 체인은 `think:false`, 컨텍스트 4096, 요청 제한시간 90초를 적용한다. 모델 해석 실패 시 구조화 요청으로 재시도해야 하며, 설명 실패 시 고정 문구로 대체한다. 추천 순위와 안전 판정은 로컬 규칙 엔진이 담당한다. 응답 시간과 JSON 형식 준수 여부는 대상 기기에서 별도로 확인한다.

`local_runtime.py`는 `APP_HOST`가 지정되지 않은 경우 앱을 `127.0.0.1`에 바인딩한다. 이미 `APP_HOST`를 명시한 경우 기존 설정이 우선한다. 인증과 DB 암호화는 아직 구현되지 않았다.

모델 교체가 영양 계산이나 알레르기 판정의 신뢰성을 보장하지 않는다. 현재 앱의 재고·응답 규칙과 사용자 확인 절차를 유지해야 한다. 음성 런타임에서 DLL 차단이 발생하면 해당 기기의 승인된 패키지·보안 정책을 확인한다. 모델 교체만으로 STT 환경 오류가 해결되는 것은 아니다.

## 검증·비교

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/benchmark_models.py --models qwen3.5:4b-q4_K_M
```

이전 모델을 설치한 경우 `.env`의 `OLLAMA_MODEL`을 해당 이름으로 변경해 비교할 수 있다. 예: `qwen3:4b`. 모델은 한 번에 하나씩 비교하고 음성·전체 앱 지연과 직접 LLM 응답 시간을 구분한다. Qwen2.5 3B를 비교할 경우 연구용 라이선스 조건을 확인한다.
