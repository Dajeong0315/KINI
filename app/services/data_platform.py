"""Additive A-owned schema. Personal and public tables stay on this device."""
from pathlib import Path


def migrate(db):
    sql = Path(__file__).with_name('data_platform_schema.sql').read_text(encoding='utf-8')
    db.executescript(sql)
