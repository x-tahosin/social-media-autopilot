#!/bin/bash
set -e

echo "[1/4] Update helper-service systemd env to use HTTPS domain..."
VM_IP=$(python3 -c "import json; print(json.load(open('/opt/autopilot/.creds.json'))['VM_PUBLIC_IP'])")
DOMAIN=$(echo "$VM_IP" | tr . -).sslip.io
sudo sed -i "s|Environment=PUBLIC_URL=http://.*|Environment=PUBLIC_URL=https://$DOMAIN|" /etc/systemd/system/autopilot-helper.service
grep PUBLIC_URL /etc/systemd/system/autopilot-helper.service

echo "[2/4] Reload systemd + restart helper (has new User-Agent fix)..."
sudo systemctl daemon-reload
sudo systemctl restart autopilot-helper
sleep 2
systemctl is-active autopilot-helper
curl -s http://127.0.0.1:3001/health

echo ""
echo "[3/3] Redeploy workflow with fixes (reads creds from /opt/autopilot/.creds.json)..."
cd /opt/autopilot && python3 deploy_cloud.py 2>&1 | tail -30
