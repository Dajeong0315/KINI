"""Nutrition provider contract for safe recommendation candidates.

Only reviewed links to an approved, published public catalog release are
eligible.  The legacy local nutrition row is retained as an explicit demo
fallback so the existing API remains compatible.
"""
from dataclasses import dataclass
from math import fsum, isfinite
from .public_catalog import PublicRecordMissing, service
from .integrations.cache import CacheUnavailable

NUTRIENTS = ('energy_kcal', 'protein_g', 'carbohydrate_g', 'fat_g', 'sodium_mg')


def per_serving_nutrition(weighted_rows):
    """Rows carry validated nutrient facts and grams per serving."""
    rows = list(weighted_rows)
    return {n: round(fsum(q[n] * grams / q['basis_amount_g'] for q, grams in rows), 2)
            for n in NUTRIENTS}


@dataclass(frozen=True)
class NutritionRecord:
    values: dict
    source: str
    release_id: str | None = None
    cache_status: str | None = None
    external_id: str | None = None


def catalog_context(db):
    catalog = service()
    if hasattr(catalog, 'context'):
        return [catalog.context(db), catalog.mapping_revision(db)]
    row = db.execute("""SELECT a.release_id,a.cache_version,s.rights_status,r.status
        FROM active_data_releases a JOIN catalog_data_sources s ON s.code=a.source_code
        JOIN data_releases r ON r.id=a.release_id WHERE a.source_code='mfds_nutrition'""").fetchone()
    return [row[key] for key in ('release_id', 'cache_version', 'rights_status', 'status')] if row else None


def _valid_number(value, *, positive=False):
    return type(value) in (int, float) and isfinite(value) and (value > 0 if positive else value >= 0)


class RecommendationNutritionProvider:
    """One contract for catalog, imported-official and demo nutrition data."""

    def __init__(self, catalog=None):
        self.catalog = catalog or service()

    def get(self, db, ingredient_id: int, legacy_row) -> NutritionRecord | None:
        """Return validated nutrition or None when the candidate is unsafe.

        A public catalog row is used only through an explicit reviewed link.
        Legacy rows are permitted solely as demo/official fallback and retain
        their source label for the response provenance.
        """
        cloud = hasattr(self.catalog, 'ingredient_link')
        pointer = self.catalog.ingredient_link(db, ingredient_id) if cloud else db.execute('''SELECT l.release_id,l.food_id
            FROM catalog_ingredient_links l
            JOIN active_data_releases a ON a.source_code='mfds_nutrition'
                AND a.release_id=l.release_id
            JOIN data_releases r ON r.id=l.release_id AND r.source_code=a.source_code AND r.status='published'
            JOIN catalog_data_sources s ON s.code=a.source_code AND s.rights_status='approved'
            WHERE l.ingredient_id=? AND trim(l.reviewed_at)<>'' ORDER BY l.reviewed_at DESC LIMIT 1''', (ingredient_id,)).fetchone()
        if pointer:
            try:
                result = self.catalog.read(db, 'mfds_nutrition', 'food', pointer['food_id'])
                if (result.item['basis_unit'] != 'g' or result.item['release_id'] != str(pointer['release_id'])
                    or (cloud and result.item['name'] != pointer['expected_name'])):
                    return None
                values = {n: result.item['nutrients'].get(n) for n in NUTRIENTS}
                if all(_valid_number(v) for v in values.values()) and _valid_number(result.item['basis_amount'], positive=True):
                    values['basis_amount_g'] = result.item['basis_amount']
                    return NutritionRecord(values, 'catalog_published', result.item['release_id'], result.cache_status,
                                           result.item['external_id'])
                return None
            except (PublicRecordMissing, CacheUnavailable, ValueError, KeyError, TypeError):
                return None

        # Explicit cloud mode must not quietly return demo nutrition if mapping,
        # release, or source validation is missing.
        if cloud or legacy_row is None:
            return None
        values = {n: legacy_row[n] for n in NUTRIENTS}
        if not all(_valid_number(v) for v in values.values()) or not _valid_number(legacy_row['basis_amount_g'], positive=True):
            return None
        source = legacy_row['nutrition_source'] or legacy_row['source_type'] or 'unknown'
        if source not in ('official_imported', 'verified_product', 'demo'):
            return None
        return NutritionRecord(values, source)
