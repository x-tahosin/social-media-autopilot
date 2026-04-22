#!/usr/bin/env python3
"""Debug image flow: find where blog/social images fail"""
import requests, json
from _creds import get as _cred

K = _cred("N8N_API_KEY", required=True)
h={'X-N8N-API-KEY':K}
eid=requests.get('http://127.0.0.1:5678/api/v1/executions?limit=1', headers=h).json()['data'][0]['id']
rd=requests.get(f'http://127.0.0.1:5678/api/v1/executions/{eid}?includeData=true', headers=h).json()['data']['resultData']['runData']

# Find all image nodes
print(f"=== Execution {eid} — image flow ===\n")
image_nodes = [k for k in rd.keys() if any(w in k for w in ['Imagen','Upload','Extract','Prep','Square','Wide','Image URL'])]
for n in image_nodes:
    r = rd[n][0]
    if r.get('error'):
        print(f"[ERR] {n}: {str(r['error'].get('message',''))[:300]}")
        continue
    d = {}
    if r.get('data',{}).get('main') and r['data']['main'][0]:
        d = r['data']['main'][0][0].get('json', {})
    # Show key indicators
    keys = list(d.keys())
    # Truncate big fields
    summary = {}
    for key in keys:
        v = d[key]
        if isinstance(v, str):
            if len(v) > 60: summary[key] = v[:60] + f"...({len(v)} chars)"
            else: summary[key] = v
        elif isinstance(v, list):
            summary[key] = f"[{len(v)} items]"
            if v and isinstance(v[0], dict):
                summary[key] += f" first_keys={list(v[0].keys())[:5]}"
        elif isinstance(v, dict):
            summary[key] = f"{{dict, keys={list(v.keys())[:5]}}}"
        else:
            summary[key] = v
    print(f"[OK] {n}:")
    for k, v in list(summary.items())[:15]:
        print(f"    {k}: {v}")
    print()
