# Security policy

## Where secrets live

**Rule: no secret ever enters git.**

All API keys, access tokens, and the VM's own n8n API key live in a single JSON file on the VM:

```
/opt/autopilot/.creds.json   (owner: deploy user, chmod 600)
```

This path is listed in `.gitignore` at every level. The repo ships an `automation/.creds.example.json` template showing every required key but zero real values.

## How the code loads them

- **`automation/deploy_cloud.py`** calls `load_creds()` at startup. It reads `/opt/autopilot/.creds.json` by default, or the path in the `AUTOPILOT_CREDS_FILE` env var, then overlays any individual env vars on top. If a required key is missing, the script exits with a clear message before making any API call.
- **`automation/_creds.py`** exposes a tiny `get(key)` helper used by every diagnostic script, so the same credentials file backs all tooling with no duplication.
- **Shell scripts** (`run_manual.sh`, `deploy_and_run.sh`, `test_devto.sh`, ...) read the creds file through `python3 -c "json.load(...)"` on demand and error out if the file is missing or the key is not set.

## Workflow runtime

Inside the n8n workflow JSON, credentials are baked into the HTTP node URLs/headers **at build time** by `deploy_cloud.py`. The workflow JSON therefore contains the live values and must **never** be exported to a public place. If you need to share the workflow, use the `export_workflow.py --redact` pattern (TODO) which replaces every credential with a placeholder.

## Setting up a new deploy

1. Copy the template: `cp automation/.creds.example.json /opt/autopilot/.creds.json`
2. Fill in the real values
3. Lock down permissions: `chmod 600 /opt/autopilot/.creds.json`
4. Optionally set `AUTOPILOT_CREDS_FILE` in your shell if you keep the file somewhere else

## Reporting a vulnerability

If you find a way to exfiltrate credentials from this repo (for example, a hardcoded key we missed in some helper file, a leaky log line, or a permissive `.gitignore` pattern), **please do not open a public issue**. Email the maintainer directly instead. Include the file path, the line number, and the nature of the leak. Fixes will be rolled out within 24 hours and a public post-mortem published once the keys have been rotated.

## Credential rotation checklist

If a key is suspected leaked:

1. Revoke it in the provider's dashboard (Dev.to, Hashnode, Meta, X, Google AI, NewsData, GCP).
2. Generate a new one.
3. Update `/opt/autopilot/.creds.json` on the VM and save.
4. Redeploy the workflow so the new values are baked in: `bash automation/deploy_and_run.sh`
5. Verify the next scheduled run still posts to every platform.
