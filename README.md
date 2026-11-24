# KINI — 온디바이스 냉장고 음성 비서 프로토타입

로컬 Ollama, 식재료 재고 JSON, faster-whisper STT, Piper TTS를 결합한 냉장고 음성 비서 프로토타입입니다. 음성 파일을 업로드하면 한국어로 전사하고, 재고 기반 답변을 생성한 뒤 한국어 WAV 음성으로 들을 수 있습니다.

기본 음성 경로는 외부 API 키 없이 동작합니다. 최초 한 번의 모델 준비에만 인터넷이 필요하며, 준비가 끝난 뒤 STT/TTS 런타임은 로컬 모델 파일을 사용합니다.

## 빠른 시작

PowerShell에서 다음을 실행합니다.

```powershell
Set-Location C:\Users\aicam\Desktop\Project_3
.\.venv\Scripts\Activate.ps1
ollama list
Invoke-RestMethod http://localhost:11434/api/tags
python -m pip install -r requirements.txt
python .\scripts\download_voice_models.py
python -m app.main
```

준비된 파일을 한 번에 점검하려면 `python .\scripts\run_voice_sample_tests.py`를 실행한다. 전사·답변·생성 WAV 경로를 출력하며, WAV는 `output/audio/`에 저장된다.

브라우저에서 `http://127.0.0.1:5000`을 엽니다. `OLLAMA_MODEL`을 비우면 현재 설치된 `qwen2.5:3b-instruct-q4_K_M`을 사용합니다. `qwen3:4b`를 설치한 뒤에는 `.env`의 `OLLAMA_MODEL=qwen3:4b`로 바꿔 비교할 수 있습니다.

기본 음성 경로는 `models/stt/small`의 faster-whisper 모델과 `models/tts/ko_KR-kss-medium.onnx`의 Piper 음성을 사용합니다. `FOODSAFETY_KOREA_API_KEY`는 COOKRCP01 레시피 검색, `DATA_GO_KR_SERVICE_KEY`는 식품영양성분 조회에만 사용합니다. ElevenLabs 모듈은 호환성을 위해 보존하지만 기본 실행 경로에서는 사용하지 않습니다.

자세한 Windows 모델 준비·오프라인 실행·문제 해결은 [로컬 음성 모델 준비 문서](docs/로컬_음성_모델_준비.md), 실제 업로드 검증은 [음성 통합 테스트셋](docs/음성_통합_테스트셋.md)을 확인하세요.

## 테스트와 벤치마크

```powershell
python -m pytest
python .\scripts\benchmark_models.py --models qwen2.5:3b-instruct-q4_K_M
# qwen3:4b 설치 후 공정 비교
python .\scripts\benchmark_models.py --models qwen2.5:3b-instruct-q4_K_M qwen3:4b
```

## 폴더

- `app/`: 웹 앱과 연동 코드
- `app/data/`: 로컬 재고 JSON
- `tests/`: 재고·응답·안전 규칙 테스트
- `samples/audio/`: STT 테스트 음성 파일 자리
- `output/audio/`: 생성 WAV 출력(기존 MP3 호환 경로도 유지)
- `models/stt/`, `models/tts/`: 사전 다운로드하는 로컬 STT/TTS 모델(커밋 제외)
- `docs/`, `scripts/`: 음성 모델 준비·검증 문서와 보조 도구
