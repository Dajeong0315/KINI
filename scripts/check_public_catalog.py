"""Read-only public catalog smoke; emits public facts, never credentials."""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.postgres_repository import connect_postgres
from app.services.supabase_catalog import SupabasePublicCatalog
from app.services.integrations.cache import DisabledCache
from app.services.recommendation_provider import NUTRIENTS, per_serving_nutrition, _valid_number


def inspect(db, food_id, grams, recipe_id=None):
    if not math.isfinite(grams) or not 0 < grams <= 1000000:
        raise ValueError('invalid_weight')
    catalog = SupabasePublicCatalog(DisabledCache())
    food = catalog.read(db, 'mfds_nutrition', 'food', food_id).item
    values = {key: food['nutrients'].get(key) for key in NUTRIENTS}
    if food['basis_unit'] != 'g' or not all(_valid_number(value) for value in values.values()):
        raise ValueError('gram_basis_and_complete_core_nutrients_required')
    values['basis_amount_g'] = food['basis_amount']
    result = dict(status='ok', food=food, grams=grams,
                  ingredient_contribution=per_serving_nutrition([(values, grams)]),
                  measurement='public ingredient only; not whole meal or per serving')
    if recipe_id:
        recipe = catalog.read(db, 'mfds_recipe', 'recipe', recipe_id).item
        result.update(recipe=recipe, recipe_servings_confirmed=recipe['servings'] is not None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--food-id', required=True)
    parser.add_argument('--grams', type=float, required=True)
    parser.add_argument('--recipe-id')
    args = parser.parse_args()
    db = None
    try:
        db = connect_postgres()
        db.execute('SET TRANSACTION READ ONLY')
        result = inspect(db, args.food_id, args.grams, args.recipe_id)
    except Exception:
        # Database exceptions may contain endpoints/usernames; do not echo them.
        print(json.dumps(dict(status='unavailable', public_catalog='not_verified')))
        return 1
    finally:
        if db is not None:
            db.rollback()
            db.close()
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
