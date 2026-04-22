#!/usr/bin/env python3
"""Add an Execute Workflow Trigger node alongside Schedule so CLI execute works."""
import requests, json, sys
from _creds import get as _cred

API_KEY = _cred("N8N_API_KEY", required=True)
BASE = "http://127.0.0.1:5678/api/v1"
hdr = {"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"}

# Find the Autopilot workflow by name
r = requests.get(f"{BASE}/workflows?limit=20", headers=hdr)
r.raise_for_status()
matches = [w for w in r.json().get("data", []) if "Autopilot" in w.get("name", "")]
if not matches:
    print("No Autopilot workflow found"); sys.exit(1)
WF_ID = matches[0]["id"]
print(f"Found workflow: {matches[0]['name']} (id={WF_ID})")

r = requests.get(f"{BASE}/workflows/{WF_ID}", headers=hdr)
r.raise_for_status()
wf = r.json()
print(f"Fetched workflow with {len(wf['nodes'])} nodes")

# Check if already has Execute Workflow Trigger
if any(n['type'] == 'n8n-nodes-base.executeWorkflowTrigger' for n in wf['nodes']):
    print("Already has Execute Workflow Trigger. Skipping.")
    sys.exit(0)

# Find Schedule node and the first real node (NewsData fetcher)
schedule_node = next((n for n in wf['nodes'] if n['type'] == 'n8n-nodes-base.scheduleTrigger'), None)
if not schedule_node:
    print("No Schedule node found!"); sys.exit(1)
print(f"Schedule node: {schedule_node['name']} at {schedule_node['position']}")

# Add Execute Workflow Trigger node
new_node = {
    "parameters": {},
    "id": "manual_trigger_cli",
    "name": "▶ Manual Exec",
    "type": "n8n-nodes-base.manualTrigger",
    "typeVersion": 1,
    "position": [schedule_node['position'][0], schedule_node['position'][1] + 150],
}
wf['nodes'].append(new_node)

# Find what schedule connects to, then connect manual trigger to the same target
conns = wf['connections']
sched_targets = conns.get(schedule_node['name'], {}).get('main', [[]])[0]
if sched_targets:
    conns["▶ Manual Exec"] = {"main": [[{"node": sched_targets[0]['node'], "type": "main", "index": 0}]]}
    print(f"Connected Manual Exec -> {sched_targets[0]['node']}")
else:
    print("Schedule has no downstream — cannot wire")
    sys.exit(1)

# n8n API requires only specific fields on PUT
payload = {
    "name": wf["name"],
    "nodes": wf["nodes"],
    "connections": wf["connections"],
    "settings": wf.get("settings", {}),
}

r = requests.put(f"{BASE}/workflows/{WF_ID}", headers=hdr, json=payload)
if not r.ok:
    print(f"PUT failed {r.status_code}: {r.text[:400]}")
    sys.exit(1)
print("Workflow updated with Execute Workflow Trigger")

# Re-activate to ensure everything is wired
requests.post(f"{BASE}/workflows/{WF_ID}/activate", headers=hdr)
print(f"Re-activated. Now run: bash run_manual.sh  (or: sudo systemctl stop n8n && sudo -u $USER HOME=$HOME n8n execute --id={WF_ID})")
