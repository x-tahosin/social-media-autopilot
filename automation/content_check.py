#!/usr/bin/env python3
"""Inspect actual content posted: image URLs, writing style, anti-AI compliance"""
import requests, json
from _creds import get as _cred

API_KEY = _cred("N8N_API_KEY", required=True)
BASE = "http://127.0.0.1:5678/api/v1"
hdr = {"X-N8N-API-KEY": API_KEY}

ex_id = requests.get(f"{BASE}/executions?limit=1", headers=hdr).json()["data"][0]["id"]
rd = requests.get(f"{BASE}/executions/{ex_id}?includeData=true", headers=hdr).json()["data"]["resultData"]["runData"]

def get_json(node):
    if node not in rd: return None
    out = rd[node][0].get("data", {}).get("main", [[]])
    return out[0][0].get("json", {}) if out and out[0] else None

print(f"=== Execution {ex_id} ===\n")

# Image URLs (confirm 3 distinct)
img = get_json("🔗 Get Image URL")
if img:
    print("IMAGE URLS:")
    print(f"  blog   (Dev.to/Hashnode): {img.get('blogImgUrl')}")
    print(f"  social (Twitter/Facebook): {img.get('socialImgUrl')}")
    print(f"  square (Instagram):       {img.get('igImgUrl')}")
    distinct = len({img.get('blogImgUrl'), img.get('socialImgUrl'), img.get('igImgUrl')} - {None})
    print(f"  distinct images: {distinct}/3")

# Story picked
story = get_json("📊 Parse Story")
if story:
    print(f"\nSTORY PICKED:")
    print(f"  title:     {story.get('original_title', '')[:100]}")
    print(f"  angle:     {story.get('angle', '')[:120]}")
    print(f"  seo_kw:    {story.get('seo_keyword')}")
    print(f"  category:  {story.get('category')}")

# Writing quality check
def scan(text, label):
    em = text.count('—') + text.count('–')
    ast_bold = len([m for m in text.split('**') if True]) // 2 if '**' in text else 0
    ai_tells = sum(1 for p in ['delve', 'dive into', 'in today\'s world', 'moreover', 'furthermore',
                                'in conclusion', 'revolutionize', 'game-changer', 'cutting-edge',
                                'seamless', 'robust', 'leverage'] if p.lower() in text.lower())
    print(f"\n  {label}:  em-dashes={em}  **bold-pairs={ast_bold}  ai-tells={ai_tells}  length={len(text)}")

# Dev.to body
devto = get_json("📄 Parse Dev.to")
if devto and devto.get('proxyBody'):
    body = json.loads(json.loads(devto['proxyBody'])['articleBody'])['article']['body_markdown']
    scan(body, "Dev.to body")
    print(f"    title: {json.loads(json.loads(devto['proxyBody'])['articleBody'])['article']['title']}")

hn = get_json("📄 Parse Hashnode")
if hn and hn.get('postBody'):
    vars_ = hn['postBody']['variables']['i']
    scan(vars_['contentMarkdown'], "Hashnode body")
    print(f"    title: {vars_['title']}")
    print(f"    slug:  {vars_.get('slug')}")

fb = get_json("📄 Parse Facebook")
if fb and fb.get('fbUrl'):
    import urllib.parse
    msg = urllib.parse.parse_qs(urllib.parse.urlparse(fb['fbUrl']).query).get('message', [''])[0] \
       or urllib.parse.parse_qs(urllib.parse.urlparse(fb['fbUrl']).query).get('caption', [''])[0]
    scan(msg, "Facebook body")
    print(f"    preview: {msg[:200]}")

ig = get_json("📄 Parse Instagram")
if ig and ig.get('igUrl'):
    import urllib.parse
    cap = urllib.parse.parse_qs(urllib.parse.urlparse(ig['igUrl']).query).get('caption', [''])[0]
    scan(cap, "Instagram caption")
    print(f"    first 125: {cap[:125]}")

tw_prompt = get_json("🐦 Twitter Prompt")
tw_build = get_json("📄 Build Tweet")
if tw_build and tw_build.get('tweetBody'):
    scan(tw_build['tweetBody']['text'], "Twitter tweet")
    print(f"    text: {tw_build['tweetBody']['text']}")
