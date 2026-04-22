#!/bin/bash
# Load credentials from /opt/autopilot/.creds.json (chmod 600, never committed)
CREDS="${AUTOPILOT_CREDS_FILE:-/opt/autopilot/.creds.json}"
if [ ! -r "$CREDS" ]; then
  echo "FATAL: cannot read $CREDS (set AUTOPILOT_CREDS_FILE to override)"; exit 1
fi
API_KEY=$(python3 -c "import json; print(json.load(open('$CREDS'))['N8N_API_KEY'])")
if [ -z "$API_KEY" ]; then echo "FATAL: N8N_API_KEY missing from $CREDS"; exit 1; fi

# Auto-find current Autopilot workflow ID via the n8n API
WF_ID=$(curl -s -H "X-N8N-API-KEY: $API_KEY" http://127.0.0.1:5678/api/v1/workflows?limit=20 | python3 -c "import json,sys; d=json.load(sys.stdin); m=[w for w in d.get('data',[]) if 'Autopilot' in w.get('name','')]; print(m[0]['id'] if m else '')")
if [ -z "$WF_ID" ]; then echo "No Autopilot workflow found"; exit 1; fi
echo "Workflow ID: $WF_ID"

echo "[1/3] Stopping n8n service..."
sudo systemctl stop n8n
sleep 3

echo "[2/3] Executing workflow..."
VM_USER="${VM_USER:-$(whoami)}"
sudo -u $VM_USER HOME=/home/$VM_USER /usr/bin/n8n execute --id="$WF_ID" 2>&1 | tail -30

echo "[3/3] Restarting n8n service..."
sudo systemctl start n8n
sleep 8
systemctl is-active n8n
echo "Done. Check WhatsApp for report."
