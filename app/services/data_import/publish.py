from __future__ import annotations

from .repository import run_row

def publish_nutrition_release(db, release_id: int) -> None:
    release=db.execute("SELECT r.*, s.id source_id FROM dataset_releases r JOIN data_sources s ON s.id=r.data_source_id WHERE r.id=?",(release_id,)).fetchone()
    if not release or release['status'] not in ('validated','retired','published'): raise ValueError('Only a validated or retained nutrition release can be published.')
    with db:
        old=db.execute('SELECT nutrition_release_id FROM data_release_pointers WHERE id=1').fetchone()[0]
        if old and old != release_id: db.execute("UPDATE dataset_releases SET status='retired' WHERE id=? AND status='published'",(old,))
        db.execute("UPDATE dataset_releases SET status='published' WHERE id=?",(release_id,))
        db.execute("UPDATE data_release_pointers SET nutrition_release_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",(release_id,))
        run_row(db,release['source_id'],release_id,'publish','completed','explicit publish')

def publish_allergen_version(db, version_id: int) -> None:
    version=db.execute('SELECT * FROM allergen_reference_versions WHERE id=?',(version_id,)).fetchone()
    if not version or version['status'] not in ('validated','retired','published'): raise ValueError('Only a validated or retained allergen reference version can be published.')
    with db:
        old=db.execute('SELECT allergen_reference_version_id FROM data_release_pointers WHERE id=1').fetchone()[0]
        if old and old != version_id: db.execute("UPDATE allergen_reference_versions SET status='retired' WHERE id=? AND status='published'",(old,))
        db.execute("UPDATE allergen_reference_versions SET status='published' WHERE id=?",(version_id,))
        db.execute("UPDATE data_release_pointers SET allergen_reference_version_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",(version_id,))
