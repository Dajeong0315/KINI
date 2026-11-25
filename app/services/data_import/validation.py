from __future__ import annotations

import math

NUTRIENTS = {
    'energy_kcal': 'kcal', 'carbohydrate_g': 'g', 'protein_g': 'g',
    'fat_g': 'g', 'sodium_mg': 'mg',
}

def normalize_name(value: str) -> str:
    return ' '.join(str(value).strip().lower().split())

def validate_food(record: dict) -> tuple[dict | None, str | None]:
    code = str(record.get('food_code') or record.get('FOOD_CD') or '').strip()
    name = str(record.get('food_name') or record.get('DESC_KOR') or '').strip()
    basis = record.get('basis_amount_g', record.get('SERVING_SIZE'))
    try:
        basis = float(basis)
    except (TypeError, ValueError):
        return None, 'missing_or_invalid_basis_amount'
    if not code:
        return None, 'missing_food_code'
    if not name:
        return None, 'missing_food_name'
    if not math.isfinite(basis) or basis <= 0:
        return None, 'missing_or_invalid_basis_amount'
    values = record.get('nutrients') or {
        'energy_kcal': record.get('energy_kcal', record.get('ENERGY_KCAL')),
        'carbohydrate_g': record.get('carbohydrate_g', record.get('CHOCDF')),
        'protein_g': record.get('protein_g', record.get('PROT')),
        'fat_g': record.get('fat_g', record.get('FATCE')),
        'sodium_mg': record.get('sodium_mg', record.get('NA')),
    }
    cleaned = {}
    for nutrient, unit in NUTRIENTS.items():
        value = values.get(nutrient)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None, f'missing_or_invalid_{nutrient}'
        if not math.isfinite(value) or value < 0:
            return None, f'missing_or_invalid_{nutrient}'
        supplied_unit = (record.get('nutrient_units') or {}).get(nutrient, unit)
        if supplied_unit != unit:
            return None, f'unit_mismatch_{nutrient}'
        cleaned[nutrient] = value
    return {'code': code, 'name': name, 'normalized_name': normalize_name(name), 'basis_amount_g': basis, 'nutrients': cleaned, 'food_category': record.get('food_category') or record.get('food_category_name'), 'manufacturer': record.get('manufacturer'), 'product_report_number': record.get('product_report_number'), 'serving_reference': record.get('serving_reference'), 'source_record_date': record.get('source_record_date')}, None
