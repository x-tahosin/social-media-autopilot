#!/usr/bin/env python3
"""Print clean status of each platform for the latest execution."""
import requests, json
from _creds import get as _cred

API_KEY = _cred("N8N_API_KEY", required=True)
BASE = "http://127.0.0.1:5678/api/v1"
hdr = {"X-N8N-API-KEY": API_KEY}

r = requests.get(f"{BASE}/executions?limit=1&includeData=true", headers=hdr)
ex = r.json()["data"][0]
print(f"Execution {ex['id']}  @ {ex.get('startedAt')}  status={ex.get('status')}\n")

r2 = requests.get(f"{BASE}/executions/{ex['id']}?includeData=true", headers=hdr)
rd = r2.json()["data"]["resultData"]["runData"]

def get_last(node):
    if node not in rd: return None
    out = rd[node][0].get("data", {}).get("main", [[]])
    if out and out[0]: return out[0][0].get("json", {})
    return None

result_nodes = [
    ("Dev.to",    "✅ Dev.to Result"),
    ("Hashnode",  "✅ Hashnode Result"),
    ("Twitter",   "✅ Twitter Result"),
    ("Facebook",  "✅ Facebook Result"),
    ("Instagram", "✅ Instagram Result"),
]

for name, nid in result_nodes:
    r = get_last(nid)
    if r:
        status = r.get("status", "?")
        emoji = "✅" if status == "published" else "❌"
        print(f"  {emoji} {name}: {status}")
        if r.get("url"):   print(f"      → {r['url']}")
        if r.get("error"): print(f"      ! {str(r['error'])[:160]}")
    else:
        print(f"  ⏸ {name}: no result node data")

img = get_last("🔗 Get Image URL")
if img:
    print(f"\n  🖼 Image URL used: {img.get('imgUrl')}")
