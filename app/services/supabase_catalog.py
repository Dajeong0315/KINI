"""Read the existing public catalog without copying it into the app schema.

Uses the caller's restricted database connection. Every catalog SQL statement
is SELECT, schema-qualified and parameterized; private links stay private.
"""
from decimal import Decimal
import hashlib
import json

from .public_catalog import PublicCatalog, PublicRecordMissing, NUTRIENTS, _validate


def _number(value):
    return float(value) if isinstance(value, Decimal) else value


class SupabasePublicCatalog(PublicCatalog):
    def context(self, db, source='mfds_nutrition'):
        row = db.execute('''SELECT a.release_id,a.cache_version,s.rights_status,r.status
            FROM public.active_data_releases a
            JOIN public.data_sources s ON s.code=a.source_code
            JOIN public.data_releases r ON r.id=a.release_id AND r.source_code=a.source_code
            WHERE a.source_code=?''', (source,)).fetchone()
        return [str(row['release_id']), row['cache_version'], row['rights_status'], row['status']] if row else None

    def _pointer(self, db, source):
        context = self.context(db, source)
        if not context or context[2:] != ['approved', 'published']:
            return None
        return {'release_id': context[0], 'cache_version': context[1]}

    def ingredient_link(self, db, ingredient_id):
        # Separate physical links are necessary: existing app catalog links have
        # FKs to the private catalog, which must not be populated with copies.
        return db.execute('''SELECT release_id,food_id,expected_name
            FROM public_catalog_ingredient_links
            WHERE ingredient_id=? AND trim(reviewed_at)<>'' AND trim(review_evidence)<>'' ''', (ingredient_id,)).fetchone()

    def mapping_revision(self, db):
        rows = db.execute('''SELECT ingredient_id,release_id,food_id,expected_name,reviewed_at,review_evidence
            FROM public_catalog_ingredient_links ORDER BY ingredient_id''').fetchall()
        return hashlib.sha256(json.dumps([dict(r) for r in rows], sort_keys=True,
                                        ensure_ascii=False).encode()).hexdigest()

    def _recipe_rows(self, db, release_id, limit, offset):
        # The app's public role can read projected recipe data.
        return db.execute("""SELECT external_id,name,NULL AS payload_json FROM public.catalog_recipes
            WHERE release_id=? ORDER BY external_id LIMIT ? OFFSET ?""",
                          (release_id, limit, offset)).fetchall()

    def _load(self, db, source, release, version, kind, external_id):
        table = 'catalog_foods' if kind == 'food' else 'catalog_recipes'
        row = db.execute(f'SELECT * FROM public.{table} WHERE release_id=? AND external_id=?',
                         (release, external_id)).fetchone()
        if not row:
            raise PublicRecordMissing()
        result = dict(kind=kind, source=source, release_id=release, cache_version=version,
                      external_id=external_id, name=row['name'])
        if kind == 'food':
            # Compact public schema stores core nutrients on the food itself.
            # NULL is missing, never zero. The provider requires all five core values.
            result.update(basis_amount=_number(row['basis_amount']), basis_unit=row['basis_unit'],
                          nutrients={key: _number(row[key]) for key in NUTRIENTS if row[key] is not None})
        else:
            result.update(servings=_number(row['servings']), cook_time_minutes=row['cook_time_minutes'],
                          ingredients_text=row['ingredients_text'],
                          steps=[r[0] for r in db.execute('''SELECT instruction FROM public.catalog_recipe_steps
                              WHERE release_id=? AND recipe_id=? ORDER BY step_no''', (release, external_id))],
                          nutrition={r['nutrient']: {'amount': _number(r['amount']),
                              'basis_amount': _number(r['basis_amount']), 'basis_unit': r['basis_unit']}
                              for r in db.execute('''SELECT nutrient,amount,basis_amount,basis_unit
                                  FROM public.catalog_recipe_nutrients WHERE release_id=? AND recipe_id=?''',
                                  (release, external_id))})
        return _validate(result)
