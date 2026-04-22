#!/bin/bash
# Diagnostic: probe different n8n API endpoints to find the right manual-trigger one.
CREDS="${AUTOPILOT_CREDS_FILE:-/opt/autopilot/.creds.json}"
K=$(python3 -c "import json; print(json.load(open('$CREDS'))['N8N_API_KEY'])")
ID=$(curl -s -H "X-N8N-API-KEY: $K" http://127.0.0.1:5678/api/v1/workflows?limit=20 | python3 -c "import json,sys; d=json.load(sys.stdin); m=[w for w in d.get('data',[]) if 'Autopilot' in w.get('name','')]; print(m[0]['id'] if m else '')")
if [ -z "$K" ] || [ -z "$ID" ]; then echo "Missing N8N_API_KEY or workflow"; exit 1; fi

echo "=== public API endpoints ==="
for EP in run execute trigger; do
  echo "--- POST /api/v1/workflows/$ID/$EP ---"
  curl -s -X POST -H "X-N8N-API-KEY: $K" -H 'Content-Type: application/json' "http://127.0.0.1:5678/api/v1/workflows/$ID/$EP" | head -c 300
  echo
done

echo "=== internal /rest endpoints (accept API key header?) ==="
for EP in run execute; do
  echo "--- POST /rest/workflows/$ID/$EP ---"
  curl -s -X POST -H "X-N8N-API-KEY: $K" -H 'Content-Type: application/json' -d '{"startNodes":["⏰ Schedule"],"runData":{}}' "http://127.0.0.1:5678/rest/workflows/$ID/$EP" | head -c 300
  echo
done
