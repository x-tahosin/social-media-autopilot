#!/bin/bash
set -e

# sslip.io gives us <IP-with-dashes>.sslip.io → resolves to our IP.
# Derive domain from VM_PUBLIC_IP env var or the creds file.
VM_IP="${VM_PUBLIC_IP:-}"
if [ -z "$VM_IP" ] && [ -r "/opt/autopilot/.creds.json" ]; then
  VM_IP=$(python3 -c "import json; print(json.load(open('/opt/autopilot/.creds.json'))['VM_PUBLIC_IP'])")
fi
if [ -z "$VM_IP" ]; then echo "FATAL: set VM_PUBLIC_IP env var"; exit 1; fi
DOMAIN="${DOMAIN:-$(echo $VM_IP | tr . -).sslip.io}"

echo "=== Installing Caddy ==="
if ! command -v caddy &>/dev/null; then
  sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq caddy
fi
caddy version

echo "=== Stopping nginx (Caddy will take over ports 80/443) ==="
sudo systemctl stop nginx
sudo systemctl disable nginx

echo "=== Writing Caddyfile ==="
sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
# HTTPS via Let's Encrypt (auto)
$DOMAIN {
    encode gzip
    handle /images/* {
        root * /var/www
        file_server
        header Cache-Control "public, max-age=2592000, immutable"
        header Access-Control-Allow-Origin "*"
    }
    handle {
        reverse_proxy 127.0.0.1:5678 {
            header_up Host {host}
            header_up X-Forwarded-For {remote_host}
        }
    }
}

# Also serve HTTP on IP so WhatsApp link-preview etc still work
:80 {
    handle /images/* {
        root * /var/www
        file_server
    }
    handle {
        redir https://$DOMAIN{uri} permanent
    }
}
CADDY

echo "=== Starting Caddy ==="
sudo systemctl enable caddy
sudo systemctl restart caddy
sleep 8

echo "=== Status ==="
systemctl is-active caddy
ss -ltn | grep -E ':80|:443' | head -5
echo "--- test HTTPS ---"
curl -sI "https://$DOMAIN/" -o - | head -5 || echo "(cert still provisioning, wait 30s)"

echo ""
echo "DONE. HTTPS domain: https://$DOMAIN"
