#!/usr/bin/env python3
"""Diagnose the last failed execution: pull Dev.to and Instagram actual errors."""
import requests, json
from _creds import get as _cred

API_KEY = _cred("N8N_API_KEY", required=True)
BASE = "http://127.0.0.1:5678/api/v1"
hdr = {"X-N8N-API-KEY": API_KEY}

# Get latest execution
r = requests.get(f"{BASE}/executions?limit=5&includeData=true", headers=hdr)
execs = r.json().get("data", [])
print(f"Found {len(execs)} executions\n")

for ex in execs[:2]:
    print(f"=== Execution {ex['id']}  mode={ex.get('mode')}  finished={ex.get('finished')}  status={ex.get('status')} ===")
    # Get full execution data
    r2 = requests.get(f"{BASE}/executions/{ex['id']}?includeData=true", headers=hdr)
    data = r2.json()
    run_data = data.get("data", {}).get("resultData", {}).get("runData", {})

    # Look at Dev.to nodes
    for key in ["🚀 Post Dev.to", "✅ Dev.to Result", "📤 IG Create Media", "✅ Instagram Result", "🔗 Get Image URL", "☁️ Upload Image"]:
        if key in run_data:
            runs = run_data[key]
            for i, run in enumerate(runs):
                out = run.get("data", {}).get("main", [[]])
                if out and out[0]:
                    for item in out[0]:
                        j = item.get("json", {})
                        err = j.get("error") or j.get("_error")
                        if err or "failed" in str(j.get("status", "")):
                            print(f"  [{key}] run {i}: {json.dumps(j, indent=2)[:1000]}")
                        elif key in ["🔗 Get Image URL", "☁️ Upload Image"]:
                            print(f"  [{key}] imgUrl={j.get('imgUrl') or j.get('url')}")
                if run.get("error"):
                    print(f"  [{key}] ERROR: {json.dumps(run['error'], indent=2)[:600]}")
    print()
