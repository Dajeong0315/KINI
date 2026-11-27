"""Reuse the Vision branch's capture UUID contract with the strict local client.

Only candidates leave this process. Never forwards photos, count as grams,
ingredient IDs, or confirmation requests. Run the real comparer separately
with --no-sync; public Supabase stock synchronization is not used by KINI.
"""
from __future__ import annotations

from uuid import UUID, uuid5

from .kini_inventory_client import KiniInventoryClient

# Same contract as feature/fridge-vision-yolo@9159259 kini_inventory_sink.py.
EVENT_NAMESPACE = UUID('6f1c7f0e-3a51-4d4e-9b1e-0b6a4b1f7c21')


def events_from_result(result: dict) -> list[dict]:
    if not isinstance(result, dict):
        raise ValueError('Expected a result object')
    capture = result.get('capture_id')
    if not isinstance(capture, str) or not 1 <= len(capture) <= 200:
        raise ValueError('A stable capture_id is required')
    if not isinstance(result.get('source'), str) or not result['source'].startswith('yolo'):
        raise ValueError('A real YOLO result is required; mock is not accepted')
    events = []
    for action, key in (('stock_in', 'added'), ('stock_out', 'removed')):
        entries = result.get(key, [])
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError('Invalid candidate list')
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Expected a candidate object')
            item = entry.get('item_id')
            if not isinstance(item, str) or not item or item in seen:
                raise ValueError('Candidate IDs must be unique per direction')
            seen.add(item)
            count = entry.get('quantity')
            if type(count) is not int or not 0 <= count <= 100:
                raise ValueError('Object count must be a bounded integer, never grams')
            if item.lower().startswith('unknown'):
                continue
            label = str(entry.get('name') or item).split('·')[0].strip()
            if not label or label.lower().startswith('unknown'):
                continue
            for index in range(count):
                events.append(dict(client_event_id=str(uuid5(EVENT_NAMESPACE,
                    f'{capture}|{action}|{item}|{index}')), source='yolo',
                    action=action, detected_label=label[:100], confidence=None))
                if len(events) > 1000:
                    raise ValueError('Too many candidates in one capture')
    return events


def submit_result(client: KiniInventoryClient, result: dict) -> list[dict]:
    # Validate the whole result before sending any candidates. Retransmission
    # uses the same UUID; the client queues transport failures without secrets.
    return [client.record_detection(**event) for event in events_from_result(result)]
