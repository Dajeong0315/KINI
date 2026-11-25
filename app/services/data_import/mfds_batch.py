"""Administrative pre-collection only. Never called by a recommendation endpoint."""
import gzip
import hashlib
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote
import requests

RECIPE_URL = 'https://openapi.foodsafetykorea.go.kr/api'
NUTRITION_URL = 'https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03/getFoodNtrCpntDbInq03'


def _get(session, url, *, params=None):
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=(10, 45), allow_redirects=False)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if response.status_code != 200:
                raise ValueError('식약처 HTTP 응답 오류. 인증/호출 한도를 확인하세요.')
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError('식약처 응답이 객체가 아닙니다.')
            return data
        except (requests.RequestException, requests.exceptions.JSONDecodeError):
            if attempt == 2:
                # No raw HTTP exception/URL: service keys appear in path/query.
                raise ValueError('식약처 연결 또는 JSON 응답 실패. 원본 URL은 출력하지 않습니다.') from None
            time.sleep(2 ** attempt)
    raise ValueError('식약처 수집 실패')


def fetch_page(session, source, key, page, page_size):
    if not key or page < 1 or not 1 <= page_size <= 100:
        raise ValueError('정식 인증키와 페이지 설정이 필요합니다.')
    if source == 'mfds_recipe':
        start = (page - 1) * page_size + 1
        data = _get(session, f'{RECIPE_URL}/{quote(key, safe="")}/COOKRCP01/json/{start}/{start+page_size-1}')
        body = data.get('COOKRCP01', {})
        code = body.get('RESULT', {}).get('CODE') if isinstance(body, dict) else None
        if code == 'INFO-200':
            return [], None
        if code != 'INFO-000':
            raise ValueError('식약처 레시피 서비스 인증·활용신청·호출 한도를 확인하세요.')
        rows = body.get('row', [])
        total = body.get('total_count')
    elif source == 'mfds_nutrition':
        data = _get(session, NUTRITION_URL, params={'serviceKey': unquote(key), 'pageNo': page,
                     'numOfRows': page_size, 'type': 'json'})
        root = data.get('response', data)
        if not isinstance(root, dict):
            raise ValueError('식품영양 응답 구조를 확인하세요.')
        header = root.get('header', {})
        if str(header.get('resultCode')) not in ('00', '0', '0000'):
            raise ValueError('식약처 영양 서비스 인증·활용신청·호출 한도를 확인하세요.')
        body = root.get('body', {})
        items = body.get('items', [])
        rows = items.get('item', []) if isinstance(items, dict) else items
        if isinstance(rows, dict):
            rows = [rows]
        total = body.get('totalCount')
    else:
        raise ValueError('지원하지 않는 출처입니다.')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('식약처 행 응답 형식을 확인하세요.')
    return rows, int(total) if total is not None else None


def collect(source, key, directory, *, session=None, page_size=100, max_pages=10000):
    """Retain compressed public source pages locally. A page file is the resume checkpoint."""
    if not key:
        raise ValueError('식약처 키를 .env에 먼저 설정하세요.')
    directory = Path(directory) / source
    directory.mkdir(parents=True, exist_ok=True)
    client = session or requests.Session()
    collected, expected_total, seen_pages = 0, None, set()
    completed = False
    for page in range(1, max_pages + 1):
        path = directory / f'page-{page:06d}.json.gz'
        if path.is_file():
            with gzip.open(path, 'rt', encoding='utf-8') as handle:
                snapshot = json.load(handle)
            if snapshot.get('source') != source or snapshot.get('page_size') != page_size or snapshot.get('page') != page:
                raise ValueError('기존 수집 페이지 설정이 다릅니다. 새 디렉터리를 사용하세요.')
            rows, total = snapshot['rows'], snapshot['total']
        else:
            rows, total = fetch_page(client, source, key, page, page_size)
            snapshot = {'source': source, 'page': page, 'page_size': page_size, 'total': total, 'rows': rows}
            temporary = path.with_suffix('.tmp')
            with gzip.open(temporary, 'wt', encoding='utf-8') as handle:
                json.dump(snapshot, handle, ensure_ascii=False, allow_nan=False)
            temporary.replace(path)
            time.sleep(.25)
        if total is not None:
            if expected_total is not None and expected_total != total:
                raise ValueError('수집 중 원본 건수가 변경됐습니다. 새 snapshot으로 다시 수집하세요.')
            expected_total = total
        if rows:
            fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if fingerprint in seen_pages:
                raise ValueError('동일 페이지가 반복되어 전체 수집을 중단했습니다.')
            seen_pages.add(fingerprint)
        collected += len(rows)
        if expected_total is not None and collected >= expected_total:
            if collected != expected_total:
                raise ValueError('원본 total과 수집 행 수가 다릅니다.')
            completed = True
            break
        if not rows:
            completed = expected_total is None
            break
    manifest = {'source': source, 'rows': collected, 'expected_total': expected_total,
                'complete': completed, 'page_size': page_size, 'last_page': page}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    if not completed:
        raise ValueError('전체 수집이 완료되지 않았습니다. 배포하지 말고 checkpoint에서 재개하세요.')
    return manifest


def _number(value):
    if value is None or not str(value).strip() or str(value).strip() in ('-', 'N/A', '미기재'):
        return None
    try:
        number = float(str(value).replace(',', ''))
    except ValueError:
        raise ValueError('영양 수치가 숫자가 아닙니다.') from None
    if not math.isfinite(number) or number < 0:
        raise ValueError('유한한 음수 아닌 영양 수치가 필요합니다.')
    return number


def normalize_food(row):
    """Current FoodNtrCpntDbInfo03 fields. Never assume absent basis means 100g."""
    external_id, name = str(row.get('FOOD_CD', '')).strip(), str(row.get('FOOD_NM_KR', '')).strip()
    if not external_id or not name:
        raise ValueError('식품 코드와 이름 mapping을 확인하세요.')
    raw_basis = str(row.get('SERVING_SIZE') or row.get('NUTRI_SERVING_SIZE') or '').strip()
    unit = str(row.get('NUTRI_SERVING_UNIT_NM') or row.get('SERVING_UNIT') or '').strip().lower()
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(g|ml)', raw_basis, re.I)
    if match:
        amount, unit = float(match[1]), match[2].lower()
    elif unit in ('g', 'ml'):
        amount = _number(raw_basis)
    else:
        raise ValueError('영양 기준량/단위가 불명확합니다. 실제 응답 fixture로 mapping을 확인하세요.')
    if amount is None or amount <= 0:
        raise ValueError('영양 기준량이 잘못되었습니다.')
    nutrients = {target: _number(row.get(source)) for target, source in (
        ('energy_kcal', 'AMT_NUM1'), ('protein_g', 'AMT_NUM3'), ('fat_g', 'AMT_NUM4'),
        ('carbohydrate_g', 'AMT_NUM6'), ('sugar_g', 'AMT_NUM7'), ('sodium_mg', 'AMT_NUM13'))}
    if not any(value is not None for value in nutrients.values()):
        raise ValueError('사용 가능한 영양값이 없습니다.')
    return {'external_id': external_id, 'kind': 'food', 'name': name, 'category': row.get('FOOD_CAT1_NM'),
            'basis_amount': amount, 'basis_unit': unit, 'nutrients': nutrients}
