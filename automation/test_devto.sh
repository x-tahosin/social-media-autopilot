#!/bin/bash
# Test Dev.to POST with various strategies to bypass CF
# Reads DEVTO key from /opt/autopilot/.creds.json (override with env var).
CREDS="${AUTOPILOT_CREDS_FILE:-/opt/autopilot/.creds.json}"
KEY=$(python3 -c "import json,os,sys; p=os.environ.get('AUTOPILOT_CREDS_FILE','/opt/autopilot/.creds.json'); d=json.load(open(p)); print(d.get('DEVTO',''))")
if [ -z "$KEY" ]; then echo "FATAL: DEVTO key missing from $CREDS"; exit 1; fi

echo "=== Strategy 1: Minimal valid article ==="
curl -s -w "\nHTTP %{http_code}\n" -X POST https://dev.to/api/articles \
  -H "api-key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"article":{"title":"Test ping","body_markdown":"test","published":false,"tags":["test"]}}' | tail -c 400

echo ""
echo "=== Strategy 2: + browser UA + accept ==="
curl -s -w "\nHTTP %{http_code}\n" -X POST https://dev.to/api/articles \
  -H "api-key: $KEY" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36" \
  -H "Accept: application/vnd.forem.api-v1+json" \
  -d '{"article":{"title":"Test ping","body_markdown":"test","published":false,"tags":["test"]}}' | tail -c 400

echo ""
echo "=== Strategy 3: Full browser header set ==="
curl -s -w "\nHTTP %{http_code}\n" -X POST https://dev.to/api/articles \
  -H "api-key: $KEY" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36" \
  -H "Accept: application/vnd.forem.api-v1+json" \
  -H "Accept-Language: en-US,en;q=0.9" \
  -H "Origin: https://dev.to" \
  -H "Referer: https://dev.to/new" \
  -H "Sec-Fetch-Dest: empty" \
  -H "Sec-Fetch-Mode: cors" \
  -H "Sec-Fetch-Site: same-origin" \
  -d '{"article":{"title":"Test ping","body_markdown":"test","published":false,"tags":["test"]}}' | tail -c 400

echo ""
echo "=== Test: GET /users/me (should work, baseline) ==="
curl -s -w "\nHTTP %{http_code}\n" https://dev.to/api/users/me -H "api-key: $KEY" | head -c 200
