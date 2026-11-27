"""Conservative inventory priority, never a food safety determination."""
from datetime import date, timedelta
import math


def assess_inventory(item, today=None, guideline=None):
    today = today or date.today()
    flags = []
    quantity = item.get('quantity_g')
    if quantity is not None and (not math.isfinite(quantity) or quantity < 0):
        return {'usable':False, 'priority':0, 'flags':['수량 확인 필요'], 'recommended_by':None}
    if item.get('quantity_g') is None or item['quantity_g'] < 0:
        flags.append('수량 확인 필요')
    state = item.get('freshness_state')
    if state in ('unsafe', 'expired'):
        return {'usable':False, 'priority':0, 'flags':['상태 확인 필요'], 'recommended_by':None}
    if state not in ('good', 'normal', 'use_soon'):
        return {'usable':False, 'priority':0, 'flags':['신선도 상태 확인 필요'], 'recommended_by':None}
    raw = item.get('use_by')
    guideline = guideline or {}
    storage = item.get('storage_location')
    if guideline.get('recommended_storage') and storage and storage != guideline['recommended_storage'] and storage != 'freezer':
        flags.append('권장 보관 위치와 다름: 상태 확인 필요')
    duration = guideline.get('frozen_days') if storage == 'freezer' else guideline.get('opened_days') if item.get('opened_at') else guideline.get('unopened_days')
    start_raw = item.get('opened_at') or item.get('received_at')
    try:
        start = date.fromisoformat(start_raw) if start_raw else None
        product_deadline = date.fromisoformat(raw) if raw else None
        guideline_deadline = start + timedelta(days=int(duration)) if start and duration is not None and int(duration) >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return {'usable':False, 'priority':0, 'flags':['날짜 확인 필요'], 'recommended_by':None}
    deadlines=[d for d in (product_deadline,guideline_deadline) if d]
    if not raw: flags.append('소비기한 미입력')
    if not deadlines:
        return {'usable':True, 'priority':1.0 if state == 'use_soon' else .2, 'flags':flags, 'recommended_by':None}
    deadline = min(deadlines)
    if deadline < today:
        return {'usable':False, 'priority':0, 'flags':['소비 권장일 또는 소비기한 경과: 상태 확인 필요'], 'recommended_by':deadline.isoformat()}
    priority = .2
    if start and start < deadline and start <= today:
        fraction = (today-start).days / (deadline-start).days
        priority = 1.0 if fraction >= .9 else .8 if fraction >= .75 else .5 if fraction >= .5 else .2
    else:
        flags.append('입고·개봉일 미입력 또는 확인 필요')
    if state == 'use_soon': priority = 1.0
    return {'usable':True, 'priority':priority, 'flags':flags, 'recommended_by':deadline.isoformat()}
