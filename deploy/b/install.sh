#!/bin/sh
# Run as root from a reviewed release directory. No migration/data import here.
set -eu
test "$(id -u)" = 0
test -f deploy/b/requirements.lock.txt
getent passwd kini-b >/dev/null || useradd --system --home /var/lib/kini-b --shell /usr/sbin/nologin kini-b
install -d -m 0755 /opt/kini-b /etc/kini-b
install -o root -g kini-b -m 0640 deploy/b/meal-feedback-options.v1.json /etc/kini-b/meal-feedback-options.v1.json
install -d -o kini-b -g kini-b -m 0700 /var/lib/kini-b
python3 -m venv /opt/kini-b/venv
/opt/kini-b/venv/bin/pip install -r deploy/b/requirements.lock.txt
install -o root -g root -m 0644 deploy/b/kini-b.service /etc/systemd/system/kini-b.service
if ! test -f /etc/kini-b/runtime.env; then
    install -o root -g kini-b -m 0640 deploy/b/runtime.env.example /etc/kini-b/runtime.env
fi
systemctl daemon-reload
printf '%s\n' 'Installed. Prepare synthetic store and current symlink before starting kini-b.'
