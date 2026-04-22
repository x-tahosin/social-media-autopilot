#!/bin/bash
# ============================================================
# Social Media Autopilot — GCP VM Setup Script
# Ubuntu 24.04 (Noble) | e2-medium (2 vCPU, 4GB RAM)
# ============================================================
set -e

# ─── Configuration ───
VM_PUBLIC_IP="${VM_PUBLIC_IP:-}"
if [ -z "$VM_PUBLIC_IP" ] && [ -r "/opt/autopilot/.creds.json" ]; then
  VM_PUBLIC_IP=$(python3 -c "import json; print(json.load(open('/opt/autopilot/.creds.json'))['VM_PUBLIC_IP'])")
fi
if [ -z "$VM_PUBLIC_IP" ]; then echo "FATAL: set VM_PUBLIC_IP env var"; exit 1; fi
N8N_PORT=5678
HELPER_PORT=3001
IMG_DIR="/var/www/images"
APP_DIR="/opt/autopilot"

echo "============================================"
echo "  Social Media Autopilot — VM Setup"
echo "  VM IP: $VM_PUBLIC_IP"
echo "============================================"

# ─── 1. System packages ───
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq curl nginx python3 python3-pip python3-requests

# ─── 2. Node.js 20 ───
echo "[2/7] Installing Node.js 20..."
if ! command -v node &>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi
echo "  Node.js $(node -v)"

# ─── 3. n8n ───
echo "[3/7] Installing n8n..."
if ! command -v n8n &>/dev/null; then
  sudo npm install -g n8n
fi
echo "  n8n $(n8n --version 2>/dev/null || echo 'installed')"

# ─── 4. Create directories ───
echo "[4/7] Creating directories..."
sudo mkdir -p "$IMG_DIR" "$APP_DIR"
sudo chown "$USER:$USER" "$IMG_DIR" "$APP_DIR"

# ─── 5. Nginx config (serve images + reverse proxy n8n) ───
echo "[5/7] Configuring nginx..."
sudo tee /etc/nginx/sites-available/autopilot > /dev/null <<NGINX
server {
    listen 80 default_server;
    server_name _;

    # Serve uploaded images
    location /images/ {
        alias $IMG_DIR/;
        expires 30d;
        add_header Cache-Control "public, immutable";
        add_header Access-Control-Allow-Origin "*";
    }

    # Reverse proxy n8n (so it's accessible on port 80)
    location / {
        proxy_pass http://127.0.0.1:$N8N_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
NGINX
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/autopilot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx
echo "  nginx configured"

# ─── 6. n8n systemd service ───
echo "[6/7] Creating n8n service..."
sudo tee /etc/systemd/system/n8n.service > /dev/null <<SERVICE
[Unit]
Description=n8n Workflow Automation
After=network.target

[Service]
Type=simple
User=$USER
Environment=N8N_HOST=0.0.0.0
Environment=N8N_PORT=$N8N_PORT
Environment=N8N_PROTOCOL=http
Environment=WEBHOOK_URL=http://$VM_PUBLIC_IP/
Environment=N8N_RUNNERS_ENABLED=true
Environment=N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=false
ExecStart=$(which n8n) start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

# ─── 7. Helper service (image upload + Dev.to proxy) ───
echo "[7/7] Creating helper service..."
# Copy helper-service.js to APP_DIR (must exist already or be pasted)
if [ -f "$APP_DIR/helper-service.js" ]; then
  echo "  helper-service.js already in $APP_DIR"
else
  echo "  ⚠️  Copy helper-service.js to $APP_DIR/helper-service.js"
fi

sudo tee /etc/systemd/system/autopilot-helper.service > /dev/null <<SERVICE
[Unit]
Description=Autopilot Helper Service (image upload + API proxy)
After=network.target

[Service]
Type=simple
User=$USER
Environment=PUBLIC_URL=http://$VM_PUBLIC_IP
WorkingDirectory=$APP_DIR
ExecStart=$(which node) $APP_DIR/helper-service.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

# ─── Start services ───
sudo systemctl daemon-reload
sudo systemctl enable n8n autopilot-helper
sudo systemctl start n8n
echo ""
echo "============================================"
echo "  ✅ Setup Complete!"
echo "============================================"
echo ""
echo "  n8n:     http://$VM_PUBLIC_IP/"
echo "  Images:  http://$VM_PUBLIC_IP/images/"
echo "  Helper:  http://127.0.0.1:$HELPER_PORT/"
echo ""
echo "  NEXT STEPS:"
echo "  1. Open http://$VM_PUBLIC_IP/ in your browser"
echo "  2. Create an n8n account (first-time setup)"
echo "  3. Go to Settings → API → Create API Key"
echo "  4. Copy helper-service.js to $APP_DIR/"
echo "     Then run: sudo systemctl start autopilot-helper"
echo "  5. Copy deploy_cloud.py to $APP_DIR/"
echo "     Update N8N API key, then run:"
echo "     python3 $APP_DIR/deploy_cloud.py"
echo ""
echo "  ⚠️  Your IP ($VM_PUBLIC_IP) is EPHEMERAL."
echo "  Reserve a static IP in GCP Console → VPC → External IPs"
echo "============================================"
