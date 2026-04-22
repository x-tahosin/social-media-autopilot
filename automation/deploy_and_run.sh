#!/bin/bash
set -e

CREDS="${AUTOPILOT_CREDS_FILE:-/opt/autopilot/.creds.json}"
if [ ! -r "$CREDS" ]; then
  echo "FATAL: cannot read credentials file at $CREDS"
  echo "Copy automation/.creds.example.json to $CREDS and fill in real values (chmod 600)."
  exit 1
fi

echo "=== Deploy updated workflow ==="
cd /opt/autopilot && AUTOPILOT_CREDS_FILE="$CREDS" python3 deploy_cloud.py 2>&1 | tail -25

echo ""
echo "=== Add manual trigger + run ==="
AUTOPILOT_CREDS_FILE="$CREDS" python3 /opt/autopilot/add_manual_trigger.py
AUTOPILOT_CREDS_FILE="$CREDS" bash /opt/autopilot/run_manual.sh
