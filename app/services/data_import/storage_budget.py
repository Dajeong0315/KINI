"""Conservative DB admission estimates; actual database bytes must be re-read per batch."""
import json
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[3] / 'config/catalog_storage_policy.json'


def policy():
    return json.loads(POLICY_PATH.read_text(encoding='utf-8'))


def admission(database_bytes, batch_payload_bytes, *, catalog_bytes=None, limits=None):
    limits = limits or policy()
    if type(database_bytes) is not int or database_bytes < 0 or type(batch_payload_bytes) is not int or batch_payload_bytes < 0:
        raise ValueError('용량은 음수가 아닌 바이트 정수여야 합니다.')
    if batch_payload_bytes > limits['max_batch_payload_bytes']:
        raise ValueError('배치를 더 작게 나눠야 합니다.')
    estimate = batch_payload_bytes * limits['payload_expansion_factor'] + limits['reserve_bytes']
    projected = database_bytes + estimate
    if catalog_bytes is not None and (type(catalog_bytes) is not int or catalog_bytes < 0):
        raise ValueError('카탈로그 크기는 음수가 아닌 바이트 정수여야 합니다.')
    catalog_projected = catalog_bytes + estimate if catalog_bytes is not None else None
    catalog_blocked = catalog_projected is not None and catalog_projected >= limits['catalog_pool_bytes']
    return {'database_bytes': database_bytes, 'projected_bytes': projected,
            'catalog_projected_bytes': catalog_projected,
            'catalog_check_deferred_to_database': catalog_bytes is None,
            'allow': projected < limits['stop_bytes'] and not catalog_blocked,
            'status': 'stop' if projected >= limits['stop_bytes'] or catalog_blocked else
                      'warning' if projected >= limits['warning_bytes'] else
                      'above_target' if projected >= limits['target_bytes'] else 'normal'}


def batches(rows, *, limits=None):
    limits = limits or policy()
    batch, size = [], 2
    for row in rows:
        row_bytes = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode('utf-8')) + 1
        if row_bytes + 2 > limits['max_batch_payload_bytes']:
            raise ValueError('단일 행이 배치 용량을 초과했습니다.')
        if batch and (len(batch) >= limits['max_batch_rows'] or size + row_bytes > limits['max_batch_payload_bytes']):
            yield batch
            batch, size = [], 2
        batch.append(row)
        size += row_bytes
    if batch:
        yield batch
