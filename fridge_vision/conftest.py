"""pytest 수집 범위 (fridge_vision).

- test_fridge_cls.py 는 Kaggle 사진 전체를 돌리는 평가 스크립트(python test_fridge_cls.py)라 pytest 에서 제외
- tests/ 는 이 폴더 안에서 실행할 때만 수집:  cd fridge_vision && python -m pytest tests -q
  KINI 저장소 루트에서 python -m pytest 를 돌리면 건너뜀 — 이 폴더의 config.py 가 루트의 config/ 와 이름이 같고,
  Vision 패키지(opencv·torch·ultralytics·supabase)가 서버 환경에 없을 수 있어서 팀 전체 시험을 깨지 않게 함.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
collect_ignore = ["test_fridge_cls.py"]


def pytest_ignore_collect(collection_path, config):
    inv = Path(str(config.invocation_params.dir)).resolve()
    inside = inv == HERE or HERE in inv.parents
    if not inside and HERE in Path(str(collection_path)).resolve().parents:
        return True
    return None
