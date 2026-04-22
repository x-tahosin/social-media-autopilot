#!/usr/bin/env python3
"""Verify hashtags in FB/IG captions have the # sign"""
import requests, urllib.parse
from _creds import get as _cred

K = _cred("N8N_API_KEY", required=True)
h={"X-N8N-API-KEY":K}
eid=requests.get("http://127.0.0.1:5678/api/v1/executions?limit=1", headers=h).json()["data"][0]["id"]
rd=requests.get(f"http://127.0.0.1:5678/api/v1/executions/{eid}?includeData=true", headers=h).json()["data"]["resultData"]["runData"]

for n in rd:
    if "Parse Facebook" in n or "Parse Instagram" in n:
        j = rd[n][0]["data"]["main"][0][0]["json"]
        url = j.get("fbUrl") or j.get("igUrl", "")
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        msg = (qs.get("caption", qs.get("message", [""])))[0]
        print(f"=== {n} ===")
        print(msg)
        print()
