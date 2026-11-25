"""Prepare local Ollama, then optionally run KINI without shell activation."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Prepare Ollama only')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    os.environ.update({
        'OLLAMA_NO_CLOUD': '1', 'OLLAMA_HOST': '127.0.0.1:11434',
        'OLLAMA_BASE_URL': 'http://127.0.0.1:11434', 'LOCAL_ONLY': '1',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONUTF8': '1',
    })
    model = (os.getenv('OLLAMA_MODEL') or '').strip() or 'qwen3.5:4b-q4_K_M'
    session = requests.Session()
    session.trust_env = False

    def ready() -> bool:
        try:
            session.get('http://127.0.0.1:11434/api/version', timeout=1).raise_for_status()
            return True
        except requests.RequestException:
            return False

    if not ready():
        binary = shutil.which('ollama')
        if not binary:
            candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Ollama/ollama.exe'
            if not candidate.is_file():
                raise RuntimeError('Install Ollama: winget install --id Ollama.Ollama --exact')
            binary = str(candidate)
        log_path = Path(tempfile.gettempdir()) / 'kini-ollama-runtime.log'
        with log_path.open('ab') as log:
            subprocess.Popen(
                [binary, 'serve'], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        for _ in range(20):
            if ready():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f'Ollama did not start. See {log_path}')
    response = session.get('http://127.0.0.1:11434/api/tags', timeout=5)
    response.raise_for_status()
    if model not in {item['name'] for item in response.json()['models']}:
        raise RuntimeError(f'Missing model. Run: ollama pull {model}')
    print(f'Ollama ready: {model}', flush=True)
    if args.check:
        return 0
    print('KINI: http://127.0.0.1:5000', flush=True)
    return subprocess.call([sys.executable, '-m', 'app.main'], cwd=ROOT)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, requests.RequestException) as exc:
        print(f'Local setup error: {exc}', file=sys.stderr)
        raise SystemExit(1)
