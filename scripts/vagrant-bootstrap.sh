#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/sns-risk-checker
VENV_DIR=/opt/sns-risk-checker/.venv
DATA_DIR=/opt/sns-risk-checker/data
SERVICE_NAME=sns-risk-checker

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip

mkdir -p "$DATA_DIR"
cd "$APP_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=SNS Risk Checker Flask App
After=network.target

[Service]
Type=simple
User=vagrant
WorkingDirectory=${APP_DIR}
Environment=AUTH_MODE=${AUTH_MODE:-local}
Environment=OWNER_EMAILS=${OWNER_EMAILS:-keikamotushige@gmail.com}
Environment=SECRET_KEY=${SECRET_KEY:-dev-change-me-in-production}
Environment=DATA_DIR=${DATA_DIR}
Environment=GEMINI_API_KEY=${GEMINI_API_KEY:-}
Environment=PER_PERSON_LIMIT=3
ExecStart=${VENV_DIR}/bin/python app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "Deployed. Open http://192.168.56.10:5000 or http://localhost:5000"
echo "Owner (unlimited): keikamotushige@gmail.com"
echo "Other users: free trial 3 times"
