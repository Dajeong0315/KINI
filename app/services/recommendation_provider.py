"""Nutrition provider contract for safe recommendation candidates.

Only reviewed links to an approved, published public catalog release are
eligible.  The legacy local nutrition row is retained as an explicit demo
fallback so the existing API remains compatible.
"""
from dataclasses import dataclass
from .public_catalog import PublicRecordMissing, service
from .integrations.cache import CacheUnavailable

NUTRIENTS = ('energy_kcal', 'protein_g', 'carbohydrate_g', 'fat_g', 'sodium_mg')


@dataclass(frozen=True)
class NutritionRecord:
    values: dict
    source: str
    release_id: str | None = None
    cache_status: str | None = None


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
        pointer = db.execute('''SELECT l.release_id,l.food_id
            FROM catalog_ingredient_links l
            JOIN active_data_releases a ON a.source_code='mfds_nutrition'
                AND a.release_id=l.release_id
            JOIN data_releases r ON r.id=l.release_id AND r.status='published'
            JOIN catalog_data_sources s ON s.code=a.source_code AND s.rights_status='approved'
            WHERE l.ingredient_id=? ORDER BY l.reviewed_at DESC LIMIT 1''', (ingredient_id,)).fetchone()
        if pointer:
            try:
                result = self.catalog.read(db, 'mfds_nutrition', 'food', pointer['food_id'])
                values = {n: result.item['nutrients'].get(n) for n in NUTRIENTS}
                if all(isinstance(v, (int, float)) and v >= 0 for v in values.values()):
                    values['basis_amount_g'] = result.item['basis_amount']
                    return NutritionRecord(values, 'catalog_published', result.item['release_id'], result.cache_status)
            except (PublicRecordMissing, CacheUnavailable, ValueError, KeyError, TypeError):
                return None

        if legacy_row is None:
            return None
        values = {n: legacy_row[n] for n in NUTRIENTS}
        if not all(isinstance(v, (int, float)) and v >= 0 for v in values.values()):
            return None
        source = legacy_row['nutrition_source'] or legacy_row['source_type'] or 'unknown'
        if source not in ('official_imported', 'verified_product', 'demo'):
            return None
        return NutritionRecord(values, source)
