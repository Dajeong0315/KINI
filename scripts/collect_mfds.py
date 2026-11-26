"""Preload source data before service use. This command does not publish partial data."""
import argparse
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.data_import.mfds_batch import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['mfds_recipe', 'mfds_nutrition', 'all'], default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/catalog-build/mfds')
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--max-pages', type=int, default=10000)
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    sources = ['mfds_recipe', 'mfds_nutrition'] if args.source == 'all' else [args.source]
    settings = {'mfds_recipe': 'FOODSAFETY_KOREA_API_KEY', 'mfds_nutrition': 'DATA_GO_KR_SERVICE_KEY'}
    missing = [settings[source] for source in sources if not os.getenv(settings[source], '').strip()]
    if missing:
        print('필수 .env 항목 미설정: ' + ', '.join(missing))
        return 2
    maximum=500 if sources==['mfds_nutrition'] else 100
    if not 1 <= args.page_size <= maximum or args.max_pages < 1:
        parser.error('nutrition 단독 page-size 1~500, 그 외 1~100, max-pages 1 이상이 필요합니다.')
    for source in sources:
        try:
            report = collect(source, os.environ[settings[source]].strip(), args.output,
                             page_size=args.page_size, max_pages=args.max_pages)
            print(json.dumps(report, ensure_ascii=False))
        except ValueError as error:
            print(str(error))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
