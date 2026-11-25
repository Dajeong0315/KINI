"""COOKRCP01 response-row normalizer. Does not infer ingredient grams or servings."""
import math


def _number(value):
    if value is None or not str(value).strip() or str(value).strip() in ('-', '미기재'):
        return None
    try:
        parsed = float(str(value).replace(',', ''))
    except ValueError:
        raise ValueError('영양값을 확인해 주세요.') from None
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError('영양값을 확인해 주세요.')
    return parsed


def normalize(row):
    """Official COOKRCP01 nutrition is for one serving; not ingredient per-100g data."""
    external_id = str(row.get('RCP_SEQ', '')).strip()
    if not external_id:
        raise ValueError('레시피 원본 ID가 필요합니다.')
    nutrients = {target: _number(row.get(source)) for target, source in (
        ('energy_kcal', 'INFO_ENG'), ('protein_g', 'INFO_PRO'), ('carbohydrate_g', 'INFO_CAR'),
        ('fat_g', 'INFO_FAT'), ('sodium_mg', 'INFO_NA'))}
    return {
        'external_id': external_id, 'kind': 'recipe', 'name': row.get('RCP_NM', ''),
        'ingredients_text': row.get('RCP_PARTS_DTLS', ''), 'servings': None, 'cook_time_minutes': None,
        'steps': [str(row[f'MANUAL{i:02d}']).strip() for i in range(1, 21) if row.get(f'MANUAL{i:02d}') and str(row[f'MANUAL{i:02d}']).strip()],
        'nutrition': {'basis_amount': 1, 'basis_unit': 'serving', 'nutrients': nutrients} if any(v is not None for v in nutrients.values()) else None,
        'source_metadata': {field: row.get(field) for field in ('INFO_WGT','RCP_WAY2','RCP_PAT2','ATT_FILE_NO_MAIN','ATT_FILE_NO_MK','RCP_NA_TIP')},
    }
