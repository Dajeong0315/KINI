"""Stage a random DB password on the approved server; user registers its SCRAM verifier.

Never prints a password/verifier. The generated SQL is local, ACL-restricted,
outside Git, and must be entered/submitted in Supabase by the user.
"""
import base64
import csv
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import subprocess


def scram(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 4096)
    client = hmac.new(salted, b'Client Key', hashlib.sha256).digest()
    stored = hashlib.sha256(client).digest()
    server = hmac.new(salted, b'Server Key', hashlib.sha256).digest()
    b64 = lambda value: base64.b64encode(value).decode()
    return f'SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}'


if __name__ == '__main__':
    directory = Path(os.environ['LOCALAPPDATA']) / 'KINI' / 'SupabaseSetup' / secrets.token_hex(6)
    directory.mkdir(parents=True, exist_ok=False)
    rows = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader(rows.splitlines()))[1]
    subprocess.run(['icacls', str(directory), '/inheritance:r', '/grant:r',
                    f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], check=True, capture_output=True)
    password = secrets.token_urlsafe(48)
    path = directory / 'register_kini_app.sql'
    path.write_text("-- Register this verifier in the KINI project only. Never share this file.\n"
                    "ALTER ROLE kini_app PASSWORD '" + scram(password) + "';\n"
                    "SELECT 'kini_app_credential_registered' AS status;\n", encoding='utf-8')
    code = "import os,pathlib\np=pathlib.Path('/etc/kini-b/db-password.pending')\n"
    code += "fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
    code += f"with os.fdopen(fd,'w') as f: f.write({password!r})\n"
    code += "print('Pending credential staged; no service configuration changed.')\n"
    process = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'StrictHostKeyChecking=yes', '-i', str(Path.home()/'.ssh/kini_b_runtime_20261127'),
        'ubuntu@100.81.162.14', 'sudo python3 -'], input=code.encode(), capture_output=True)
    if process.returncode:
        path.unlink()
        raise SystemExit('Credential staging failed; no credential output.')
    print('Protected SQL file:', path)
    print('Pending server credential: staged. User must register SQL in Supabase.')
