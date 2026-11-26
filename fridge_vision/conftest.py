"""pytest 수집 범위 (fridge_vision).
- test_fridge_cls.py 는 Kaggle 사진 전체를 돌리는 평가 스크립트(python test_fridge_cls.py)라 pytest 에서 제외
- tests/ 는 Vision 패키지(opencv·torch·ultralytics)가 있는 환경에서만 수집
  → KINI 서버 환경(저장소 루트에서 python -m pytest)에서는 건너뛰어 팀 전체 시험을 깨지 않게 함
Vision 환경에서 실행: cd fridge_vision && python -m pytest tests -q"""
import importlib.util

_VISION_DEPS = ("cv2", "torch", "ultralytics")
collect_ignore = ["test_fridge_cls.py"]
collect_ignore_glob = [] if all(importlib.util.find_spec(m) for m in _VISION_DEPS) else ["tests/test_*.py"]
