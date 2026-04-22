#!/usr/bin/env python3
"""Deep introspection of the latest execution"""
import requests, json, sys
from _creds import get as _cred

API_KEY = _cred("N8N_API_KEY", required=True)
BASE = "http://127.0.0.1:5678/api/v1"
hdr = {"X-N8N-API-KEY": API_KEY}

r = requests.get(f"{BASE}/executions?limit=1&includeData=true", headers=hdr)
ex_id = r.json()["data"][0]["id"]
print(f"Execution {ex_id}\n")

r2 = requests.get(f"{BASE}/executions/{ex_id}?includeData=true", headers=hdr)
rd = r2.json()["data"]["resultData"]["runData"]

def peek(node_name, limit=600):
    if node_name not in rd:
        print(f"  [NOT RUN] {node_name}")
        return
    runs = rd[node_name]
    for i, run in enumerate(runs):
        if run.get("error"):
            print(f"  [{node_name}] ERR: {str(run['error'].get('message', run['error']))[:400]}")
        out = run.get("data", {}).get("main", [[]])
        if out and out[0]:
            j = out[0][0].get("json", {})
            # trim long fields
            cleaned = {}
            for k, v in j.items():
                if isinstance(v, str) and len(v) > limit:
                    cleaned[k] = v[:limit] + f"...({len(v)} chars)"
                elif isinstance(v, list) and len(v) > 5:
                    cleaned[k] = v[:5] + ["...and more"]
                else:
                    cleaned[k] = v
            print(f"  [{node_name}] run {i}:")
            print(json.dumps(cleaned, indent=4)[:1500])

print("\n========== STORY SELECTION ==========")
peek("📊 Parse Story", 300)

print("\n========== IMAGE FLOW ==========")
peek("☁️ Upload Blog", 200)
peek("☁️ Upload Social", 200)
peek("☁️ Upload Square", 200)
peek("🔗 Get Image URL", 200)

print("\n========== HASHNODE DEBUG ==========")
peek("📝 Hashnode Prompt", 100)
peek("📄 Parse Hashnode", 400)

print("\n========== DEV.TO DEBUG ==========")
peek("📄 Parse Dev.to", 400)

print("\n========== INSTAGRAM DEBUG ==========")
peek("📄 Parse Instagram", 300)
