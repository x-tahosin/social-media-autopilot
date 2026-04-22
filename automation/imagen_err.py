#!/usr/bin/env python3
import requests, json
from _creds import get as _cred

K = _cred("N8N_API_KEY", required=True)
h={'X-N8N-API-KEY':K}
eid=requests.get('http://127.0.0.1:5678/api/v1/executions?limit=1', headers=h).json()['data'][0]['id']
rd=requests.get(f'http://127.0.0.1:5678/api/v1/executions/{eid}?includeData=true', headers=h).json()['data']['resultData']['runData']

for nn in ['📸 Imagen Wide']:
    r = rd[nn][0]
    print(f"=== {nn} ===")
    if 'data' in r and r['data'].get('main'):
        item = r['data']['main'][0][0].get('json', {})
        err = item.get('error', {})
        print("error.message:", str(err.get('message', ''))[:500])
        print("error.description:", str(err.get('description', ''))[:500])
        # show full json response for HTTP (body may be in error.response.data)
        print("full keys:", list(item.keys()))
        if 'error' in item and 'response' in item['error']:
            print("response:", str(item['error']['response'])[:800])
    if r.get('error'):
        print("node error:", str(r['error'])[:600])
