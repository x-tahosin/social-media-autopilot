#!/bin/bash
set -e

# VM_IP must be set — either via env var or the creds file.
# Example: VM_IP=1.2.3.4 VM_USER=ubuntu bash vm_setup.sh
VM_IP="${VM_IP:-}"
VM_USER="${VM_USER:-$(whoami)}"
if [ -z "$VM_IP" ]; then
  if [ -r "/opt/autopilot/.creds.json" ]; then
    VM_IP=$(python3 -c "import json; print(json.load(open('/opt/autopilot/.creds.json'))['VM_PUBLIC_IP'])")
  fi
fi
if [ -z "$VM_IP" ]; then echo "FATAL: set VM_IP env var"; exit 1; fi

# ─── Nginx config: serve /images/ + reverse proxy n8n on port 80 ───
sudo tee /etc/nginx/sites-available/autopilot > /dev/null <<NGINX
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 50M;

    location /images/ {
        alias /var/www/images/;
        expires 30d;
        add_header Cache-Control "public, immutable";
        add_header Access-Control-Allow-Origin "*";
    }

    location / {
        proxy_pass http://127.0.0.1:5678;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
NGINX
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/autopilot /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
echo "[nginx] restarted"

# ─── n8n systemd service ───
sudo tee /etc/systemd/system/n8n.service > /dev/null <<SERVICE
[Unit]
Description=n8n Workflow Automation
After=network.target

[Service]
Type=simple
User=$VM_USER
Environment=HOME=/home/$VM_USER
Environment=N8N_HOST=0.0.0.0
Environment=N8N_PORT=5678
Environment=N8N_PROTOCOL=http
Environment=WEBHOOK_URL=http://$VM_IP/
Environment=N8N_RUNNERS_ENABLED=true
Environment=N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=false
Environment=N8N_SECURE_COOKIE=false
Environment=NODE_OPTIONS=--max-old-space-size=2048
ExecStart=/usr/bin/n8n start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

# ─── Helper service systemd ───
sudo tee /etc/systemd/system/autopilot-helper.service > /dev/null <<SERVICE
[Unit]
Description=Autopilot Helper (image upload + API proxy)
After=network.target

[Service]
Type=simple
User=$VM_USER
Environment=PUBLIC_URL=https://$(echo $VM_IP | tr . -).sslip.io
WorkingDirectory=/opt/autopilot
ExecStart=/usr/bin/node /opt/autopilot/helper-service.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable n8n autopilot-helper
sudo systemctl start autopilot-helper
sudo systemctl start n8n
sleep 5
echo "=== STATUS ==="
systemctl is-active nginx
systemctl is-active n8n
systemctl is-active autopilot-helper
echo "=== HELPER HEALTH ==="
curl -s http://127.0.0.1:3001/health
echo ""
echo "=== n8n PORT ==="
ss -ltn | grep 5678 || echo "n8n not listening yet, may take 30s more"
