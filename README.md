# Social Media Autopilot

A fully automated tech-news publishing pipeline that runs on a single GCP VM and posts to **Dev.to, Hashnode, X (Twitter), Facebook, Instagram, and GitHub** every 12 hours with distinct, SEO-optimised, human-sounding content and three unique AI-generated images per cycle.

Built on n8n + Gemini 2.5 + Imagen 4 + a tiny Node.js helper service. Zero manual intervention once deployed. Zero secrets in this repo.

## Highlights

- **Same story, five platforms, five voices.** A single research step picks one tech news item, then five platform-specific prompts produce a Dev.to article, a Hashnode deep-dive, a Twitter thread, a Facebook post, and an Instagram caption. Each one gets its own voice, structure, SEO keyword, and hashtags.
- **Three distinct AI images per run.** One 16:9 editorial cover for blogs, one 16:9 cinematic social card for X and Facebook, one 1:1 iconographic square for Instagram. Generated in parallel by three separate Imagen 4 calls because Imagen rejects multi-instance batches.
- **Aggressive anti-AI-voice rules.** No em-dashes, no asterisk bold, no `delve`, no `dive into`, no `in the realm of`. A post-processing pass strips anything the model leaks through.
- **Dedup against recent posts.** Every cycle fetches the last eight posts from each platform and feeds them into the prompt as a "do not repeat these" list. Hashtags always get the `#` sign even if the model forgets.
- **Self-healing operations.** Twitter `CreditsDepleted` is detected and reported cleanly. Hashnode slug drift (the classic "undefined-" bug) is blocked with a defensive fallback. Imagen truncation is caught and the pipeline falls back to a reused image instead of posting with nothing.
- **WhatsApp cycle reports.** Every run ends with a Meta Cloud API message listing what was posted where, with links.
- **No secrets in source.** `/opt/autopilot/.creds.json` on the VM is the single source of truth. See `SECURITY.md`.

## Architecture

```
┌──────────────┐
│ ⏰ Schedule  │  every 12h
└──────┬───────┘
       │
┌──────▼──────────────────────────────────────────────┐
│ 🔍 News fetchers                                    │
│   NewsData · Reddit · Google News · GitHub trending │
└──────┬──────────────────────────────────────────────┘
       │
┌──────▼───────────┐   ┌───────────────────────────┐
│ 📚 Merge recent  │◄──┤ 📜 Last 8 posts per site  │
└──────┬───────────┘   └───────────────────────────┘
       │
┌──────▼──────────┐     Gemini 2.5 Flash
│ 🧠 Research AI  │   → picks 1 story, angle, SEO keyword,
└──────┬──────────┘     3 image prompts
       │
┌──────▼───────────────────────────────────────────┐
│ 🎨 Imagen 4 ×3 (blog 16:9, social 16:9, sq 1:1)  │
│ 🖼  helper-service → PNG→JPEG → freeimage.host   │
└──────┬───────────────────────────────────────────┘
       │
       ├─────► 📝 Dev.to    (proxied to bypass n8n body bug)
       ├─────► 📚 Hashnode  (GraphQL)
       ├─────► 🐦 X/Twitter (OAuth 1.0a signed in JS)
       ├─────► 📘 Facebook  (/photos + caption)
       ├─────► 📸 Instagram (2-step media container → publish)
       └─────► 🐙 GitHub    (daily markdown commit)
       │
┌──────▼───────────────┐
│ 📲 WhatsApp report   │
└──────────────────────┘
```

## Quick start

```bash
# 1. Provision a GCP e2-medium VM with Ubuntu 24.04
gcloud compute instances create autopilot-vm --zone=us-central1-a \
  --machine-type=e2-medium --image-family=ubuntu-2404-lts \
  --image-project=ubuntu-os-cloud --tags=http-server,https-server

# 2. SSH in and clone this repo
gcloud compute ssh autopilot-vm --tunnel-through-iap
git clone https://github.com/YOUR_USERNAME/social-media-autopilot.git
cd social-media-autopilot/automation

# 3. Fill in credentials (see automation/.creds.example.json for the schema)
sudo mkdir -p /opt/autopilot
sudo cp .creds.example.json /opt/autopilot/.creds.json
sudo nano /opt/autopilot/.creds.json   # paste real API keys
sudo chmod 600 /opt/autopilot/.creds.json

# 4. One-shot installer: Node, n8n, nginx, helper-service, systemd units
VM_PUBLIC_IP=$(curl -s ifconfig.me) VM_USER=$USER bash setup_vm.sh

# 5. Optional: free HTTPS via sslip.io + Caddy
VM_PUBLIC_IP=$(curl -s ifconfig.me) bash install_caddy.sh

# 6. Deploy the workflow
bash deploy_and_run.sh
```

First scheduled run fires 12 hours later; `run_manual.sh` triggers one now.

## Repository layout

```
automation/
├── deploy_cloud.py          # Builds + deploys the n8n workflow (single Python file, ~1000 lines)
├── helper-service.js        # Node HTTP service: image upload + Dev.to proxy
├── setup_vm.sh              # One-shot VM provisioning (nginx, systemd, node, n8n)
├── install_caddy.sh         # Free HTTPS via sslip.io
├── run_manual.sh            # Trigger one workflow run right now
├── deploy_and_run.sh        # Redeploy + trigger (your dev loop)
├── add_manual_trigger.py    # Injects an Execute Workflow Trigger node into the live flow
├── status_check.py          # "What happened in the last run?" — per-platform status
├── content_check.py         # Grades the last run's writing on em-dashes, AI-tells, hashtag format
├── deep_check.py            # Full per-node execution inspection
├── diagnose.py              # Focused Dev.to + Instagram error extraction
├── hashtag_check.py         # Verify FB/IG captions end with correctly-prefixed hashtags
├── image_debug.py           # Image-pipeline introspection
├── imagen_err.py            # Pull the raw Imagen error text
├── test_devto.sh            # Isolated Dev.to POST test (reused when CF blocks)
├── _creds.py                # Shared creds loader used by every diagnostic script
└── .creds.example.json      # Schema for /opt/autopilot/.creds.json
```

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | n8n 2.17 | Visual workflow, good enough scheduler, Code nodes let you write real JS when the built-in HTTP is too rigid |
| LLM | Gemini 2.5 Flash | 1M ctx, structured-output schema is reliable, fast, cheap |
| Image | Imagen 4.0 | Quality matches Midjourney for editorial illustration, runs from the same Google key |
| Hosting | GCP e2-medium, Ubuntu 24.04 | $24/mo all-in with a static IP |
| HTTPS | Caddy + sslip.io | Zero-config TLS without owning a domain |
| Image CDN | freeimage.host + VM nginx fallback | IG's Graph API rejects base64 and localhost URLs |
| Reports | WhatsApp Cloud API | I read WhatsApp. I don't read email. |

## What it does NOT do (yet)

- No engagement analytics feedback loop (likes, impressions) — planned
- No comment replies or DM handling
- No YouTube / TikTok — those need video generation, out of scope for now

## License

MIT — see `LICENSE`.
