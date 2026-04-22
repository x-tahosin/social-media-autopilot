#!/usr/bin/env python3
import base64, requests, json, sys

with open('/var/www/images/618e3fa790a94e18dd675f00.jpg', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()
print(f"image size: {len(b64) * 3 // 4 // 1024} KB")

r = requests.post('https://freeimage.host/api/1/upload',
    data={'key': '6d207e02198a847aa98d0a2a901485a5',
          'type': 'base64', 'format': 'json', 'source': b64},
    timeout=60)
print(f"status: {r.status_code}")
print(f"body: {r.text[:500]}")
try:
    d = r.json()
    print(f"IMAGE URL: {d.get('image', {}).get('url')}")
except:
    pass
