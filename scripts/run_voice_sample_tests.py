"""준비된 음성 샘플의 로컬 STT·답변·TTS 통합 테스트를 실행한다."""
from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import _answer_question
from app.services.errors import ServiceError
from app.services.local_stt import transcribe_audio
from app.services.local_tts import OUTPUT_DIR, synthesize_speech


SAMPLE_DIR = PROJECT_ROOT / "samples" / "audio"
SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".mp4"}


def main() -> int:
    samples = sorted(
        path for path in SAMPLE_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not samples:
        print("테스트할 지원 음성 파일이 samples/audio에 없습니다.")
        return 1

    failures = 0
    print(f"음성 통합 테스트: {len(samples)}개 파일")
    for path in samples:
        print(f"\n[{path.name}]")
        try:
            transcript = transcribe_audio(path)
            result = _answer_question(transcript)
            wav_filename = synthesize_speech(str(result["answer"]))
            wav_path = OUTPUT_DIR / wav_filename
            print(f"전사: {transcript}")
            print(f"답변 출처: {result['answer_source']}")
            print(f"답변: {result['answer']}")
            print(f"생성 WAV: {wav_path.relative_to(PROJECT_ROOT)}")
        except (ServiceError, ValueError) as exc:
            failures += 1
            print(f"실패: {exc}")

    print(f"\n완료: 성공 {len(samples) - failures}개, 실패 {failures}개")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
