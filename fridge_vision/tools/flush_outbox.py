"""DB 저장 대기열(state/db_outbox.jsonl)을 지금 다시 보냄.

인터넷이 끊겼다가 다시 연결됐을 때 다음 촬영을 기다리지 않고 바로 보내려면:
  python tools/flush_outbox.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from supabase_sync import InventorySync  # noqa: E402


def main() -> int:
    sync = InventorySync(log=lambda m: print(m))
    if not sync.enabled:
        return 1
    before = sync.outbox_count()
    if not before:
        print(f"보낼 것이 없습니다 ({config.SUPABASE_QUEUE_FILE} 비어 있음)")
        return 0
    print(f"대기 중 {before}건 → 다시 보냄")
    left = sync.flush()
    print(f"완료: 보냄 {before - left}건, 남음 {left}건" + (" (연결 확인 후 다시 실행)" if left else ""))
    return 0 if not left else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
