"""Bounded operator warmup outside the browser/API deadline; no model prose logs."""
import json
import os
import time
import requests


def main():
    started = time.monotonic()
    try:
        model = os.environ['OLLAMA_MODEL']
        with requests.Session() as session:
            session.trust_env = False
            response = session.post('http://127.0.0.1:11434/api/generate', json={
                'model': model, 'stream': False, 'prompt': 'Return only the word READY.',
                'keep_alive': '5m', 'options': {'num_ctx': 2048, 'num_predict': 8},
            }, timeout=(2, 40))
            response.raise_for_status()
            value = response.json()
            if value.get('done') is not True:
                raise ValueError('Incomplete warmup')
        print(json.dumps({'status': 'model_loaded', 'seconds': round(time.monotonic()-started, 3),
                          'load_seconds': round(value.get('load_duration', 0)/1e9, 3)}))
        return 0
    except Exception:
        print(json.dumps({'status': 'model_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
