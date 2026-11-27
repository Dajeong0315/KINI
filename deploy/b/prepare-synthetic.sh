#!/bin/sh
# New dedicated server only. Run after install.sh, from the release directory.
# No real DB, external OpenAI call, migration or tailnet approval is performed.
set -eu
test "$(id -u)" = 0
test -x /opt/kini-b/venv/bin/python
test ! -e /var/lib/kini-b/synthetic.sqlite3
KINI_DB_BACKEND=sqlite /opt/kini-b/venv/bin/python - <<'PY'
from pathlib import Path
import os
import secrets
from scripts.b_synthetic_store import prepare
password = secrets.token_urlsafe(32)
prepare('/var/lib/kini-b/synthetic.sqlite3', 'synthetic-b', password)
credentials = Path('/etc/kini-b/smoke.env')
credentials.write_text('KINI_B_SMOKE_LOGIN=synthetic-b\nKINI_B_SMOKE_PASSWORD=' + password + '\n')
credentials.chmod(0o600)
print('Synthetic fixture ready; credentials retained only in root-only file.')
PY
chown kini-b:kini-b /var/lib/kini-b/synthetic.sqlite3
chmod 0600 /var/lib/kini-b/synthetic.sqlite3
# First stage is explicitly offline. Switch to synthetic-ollama after model prep.
sed -i 's/^KINI_B_AI_MODE=.*/KINI_B_AI_MODE=synthetic-fake/' /etc/kini-b/runtime.env
systemctl start kini-b
systemctl is-active kini-b
printf '%s\n' 'B_SYNTHETIC_SERVICE_READY'
