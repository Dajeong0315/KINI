"""한국 시간 기준 날짜별 팀 기록을 만든다. 기존 파일은 덮어쓰지 않는다."""
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
NAMES = ("전다정", "김진아", "조민준", "김재웅", "이원준")

def main():
    if len(sys.argv) > 2:
        raise SystemExit("사용법: python team-logs/새_날짜_만들기.py [YYYY-MM-DD]")
    try:
        day = date.fromisoformat(sys.argv[1]) if len(sys.argv) == 2 else datetime.now(timezone(timedelta(hours=9))).date()
    except ValueError:
        raise SystemExit("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    day_text = day.isoformat()
    target = ROOT / day_text
    target.mkdir(exist_ok=True)
    for name in (*NAMES, "팀 전체"):
        template = "팀_전체_템플릿.md" if name == "팀 전체" else "개인_템플릿.md"
        content = (ROOT / "templates" / template).read_text(encoding="utf-8")
        content = content.replace("{{DATE}}", day_text).replace("{{NAME}}", name)
        destination = target / f"{name}.md"
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as output:
                output.write(content)
            print(f"생성: {destination}")
        except FileExistsError:
            print(f"유지: {destination}")

if __name__ == "__main__":
    main()
