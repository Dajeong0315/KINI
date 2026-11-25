from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.recommendation_repository import open_db

with open_db() as db:
    pointer=dict(db.execute('SELECT * FROM data_release_pointers WHERE id=1').fetchone())
    releases=[dict(row) for row in db.execute('SELECT id,status,checksum,record_count,accepted_count,quarantined_count,imported_at FROM dataset_releases ORDER BY id DESC')]
    allergen_versions=[dict(row) for row in db.execute('SELECT id,status,legal_or_reference_version,checksum,imported_at FROM allergen_reference_versions ORDER BY id DESC')]
print(json.dumps({'current':pointer,'nutrition_releases':releases,'allergen_reference_versions':allergen_versions},ensure_ascii=False,indent=2))
