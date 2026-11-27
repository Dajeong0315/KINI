"""Package reviewed source only; never include .env, databases or credentials."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import argparse

ROOT=Path(__file__).resolve().parents[1]
ALLOWED={'.py','.json','.sql','.md','.txt','.service','.conf','.sh','.in','.js','.css',
         '.html','.png','.webmanifest','.ps1','.cjs','.example'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['step2','step3','step5','step6','step7'],default='step2')
    args=parser.parse_args()
    files={}
    for directory in ('app','config','db','deploy','scripts'):
        for path in (ROOT/directory).rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix not in ALLOWED:
                continue
            if path.name.startswith('.env') and path.name!='.env.example':
                raise SystemExit('Refusing environment file')
            files[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    if args.stage == 'step7':
        for filename in ('__init__.py','kini_inventory_client.py','kini_result_bridge.py'):
            path = ROOT / 'fridge_vision' / filename
            files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}
    fingerprint=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    release=args.stage+'-20261127-'+fingerprint[:12]
    manifest=json.dumps(dict(release=release,base_main='eaea05e',integrated_deployment='c6be1e0',
        source_fingerprint=fingerprint,git_commit_created=False,files=hashes),sort_keys=True,indent=2).encode()
    destination=Path(tempfile.gettempdir())/(release+'.tar.gz')
    with tarfile.open(destination,'w:gz') as archive:
        for name,data in {**files,'DEPLOYMENT_MANIFEST.json':manifest}.items():
            entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o644
            archive.addfile(entry,io.BytesIO(data))
    print(json.dumps(dict(release=release,archive=str(destination),files=len(files),
                         archive_sha256=hashlib.sha256(destination.read_bytes()).hexdigest())))
