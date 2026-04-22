#!/usr/bin/env python3
"""
Social Media Autopilot v3 — Cloud Edition
==========================================
Deploys n8n workflow to a GCP VM. Key differences from local:
  - Images saved to VM disk via helper-service, served by nginx
  - Dev.to posts go through helper-service proxy (bypasses n8n body bug)
  - Twitter error reporting captures real API error
  - All URLs point to VM's public IP

CREDENTIALS:
  All API keys live in /opt/autopilot/.creds.json (on the VM, chmod 600, never committed).
  You can override individual values via environment variables.
  Run `cp automation/.creds.example.json /opt/autopilot/.creds.json` and fill in real values.
"""
import os, sys, json, pathlib
import requests, time, hashlib, hmac, base64, urllib.parse

# ============================================================
# CONFIGURATION — read from env + creds file (no secrets in code)
# ============================================================

def load_creds():
    """Merge env vars on top of the creds JSON file. Env wins."""
    creds_path = os.environ.get(
        "AUTOPILOT_CREDS_FILE",
        "/opt/autopilot/.creds.json",
    )
    data = {}
    if os.path.exists(creds_path):
        try:
            with open(creds_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            sys.exit(f"FATAL: cannot read {creds_path}: {e}")
    # Every env var matching a known key overrides the file value
    for k in list(_REQUIRED_KEYS) + list(_OPTIONAL_KEYS):
        v = os.environ.get(k)
        if v:
            data[k] = v
    missing = [k for k in _REQUIRED_KEYS if not data.get(k)]
    if missing:
        sys.exit(
            "FATAL: missing required credentials: "
            + ", ".join(missing)
            + f"\nSet env vars OR create {creds_path} (see automation/.creds.example.json)."
        )
    return data


_REQUIRED_KEYS = [
    # Core API keys needed to run the workflow
    "NEWSDATA", "GEMINI", "DEVTO", "HN_TOKEN", "HN_PUB",
    "TW_CK", "TW_CS", "TW_AT", "TW_AS",
    "FB_ID", "FB_TK", "IG_ID", "IG_TK",
    "WA_TK", "WA_PH", "WA_NUM",
    # Used by deploy script itself
    "N8N_API_KEY",
    # VM location
    "VM_PUBLIC_IP", "PUBLIC_DOMAIN",
]

_OPTIONAL_KEYS = [
    "SERPAPI",              # Google News fallback (workflow skips if missing)
    "GH_TOKEN",             # GitHub PAT for daily-notes auto-commits
    "GH_OWNER", "GH_REPO",  # Target repo for daily notes
]

CRED = load_creds()

VM_PUBLIC_IP = CRED["VM_PUBLIC_IP"]
PUBLIC_DOMAIN = CRED["PUBLIC_DOMAIN"]
PUBLIC_URL = f"https://{PUBLIC_DOMAIN}"

N8N = {
    "key": CRED["N8N_API_KEY"],
    "base": "http://127.0.0.1:5678/api/v1",  # localhost since script runs ON the VM
}

HELPER_URL = "http://127.0.0.1:3001"  # helper-service.js

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={CRED['GEMINI']}"
IMAGEN_URL = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={CRED['GEMINI']}"

# ============================================================
# RESPONSE SCHEMAS
# ============================================================

_S = {"type": "STRING"}
_SA = {"type": "ARRAY", "items": {"type": "STRING"}}

def _obj(req_keys, **props):
    return {"type": "OBJECT", "properties": props, "required": req_keys}

SCHEMA = {
    "research": _obj(
        ["original_title", "angle", "key_facts", "image_prompt_blog", "image_prompt_social", "image_prompt_square", "category", "seo_keyword"],
        original_title=_S, angle=_S, key_facts=_SA, topic_tags=_SA,
        image_prompt_blog=_S, image_prompt_social=_S, image_prompt_square=_S,
        category=_S, code_relevance=_S, seo_keyword=_S,
    ),
    "devto":    _obj(["title", "body", "tags"], title=_S, body=_S, tags=_SA),
    "hashnode": _obj(["title", "subtitle", "body", "tags", "slug"], title=_S, subtitle=_S, body=_S, tags=_SA, slug=_S),
    "twitter":  _obj(["thread", "hashtags"], thread=_SA, hashtags=_SA),
    "facebook": _obj(["body", "hashtags"], body=_S, hashtags=_SA),
    "instagram": _obj(["caption", "hashtags"], caption=_S, hashtags=_SA),
}

# ============================================================
# SYSTEM PROMPTS
# ============================================================

# Global anti-AI style guide — injected into every platform prompt
ANTI_AI = (
    "CRITICAL HUMAN-VOICE RULES (strictly enforced):\n"
    "- NEVER use em-dashes (—), en-dashes (–), or double-hyphens (--). Use commas, periods, or parentheses instead.\n"
    "- NEVER use asterisks for emphasis like *word* or **word**. Use plain text or CAPS sparingly.\n"
    "- NEVER use these AI-tell phrases: 'delve', 'dive into', 'in today\\'s world', 'in the realm of', 'at the heart of', "
    "'moreover', 'furthermore', 'in conclusion', 'it is important to note', 'navigate the complexities', 'unleash', 'harness', "
    "'revolutionize', 'game-changer', 'cutting-edge', 'seamless', 'robust', 'leverage', 'paradigm'.\n"
    "- USE contractions: don't, can't, it's, you're, I've.\n"
    "- VARY sentence length: mix 4-word punchy lines with 20-word explanations. No uniform structure.\n"
    "- WRITE like a tired senior dev explaining over coffee: specific, opinionated, occasionally sarcastic.\n"
    "- Include ONE concrete number, version, or benchmark per paragraph.\n"
    "- Reference real names: actual repos, companies, or people tied to the story.\n"
    "- START sentences with 'And', 'But', 'So' occasionally. It reads natural.\n"
)

PROMPT = {
    "research": (
        "You are a senior tech analyst picking the SINGLE most impactful story for developers. "
        "You will be given: (a) fresh news articles and (b) a list of recent topics already posted — AVOID anything semantically similar to the recent topics. "
        "Prefer concrete, actionable stories: AI coding tools, GitHub trending repos, open-source releases, agentic AI, dev tools, security incidents, specific product launches. "
        "REJECT: generic 'AI is transforming X' fluff, opinion pieces, rehashes of stories from the last 14 days.\n\n"
        "Output fields:\n"
        "- original_title: the real source title\n"
        "- angle: ONE sentence — what's the sharp, specific take (not vague)\n"
        "- key_facts: 3-5 concrete facts with numbers/names/dates\n"
        "- seo_keyword: the PRIMARY search keyword (2-4 words) that real developers would Google\n"
        "- category: one of ai_coding, web_dev, open_source, dev_tools, big_tech, security\n"
        "- image_prompt_blog: 16:9 blog cover scene, photorealistic editorial illustration, dramatic lighting, hero subject of story\n"
        "- image_prompt_social: 16:9 scroll-stopping social card, bold colors, different composition from blog (e.g. close-up vs wide), cinematic\n"
        "- image_prompt_square: 1:1 Instagram visual, bold central subject, high contrast, iconographic, NOT matching blog composition\n"
        "NONE of the image prompts may contain text, words, letters, logos, or typography."
    ),
    "devto": (
        ANTI_AI +
        "\nWrite a Dev.to technical post (NOT generic tutorial — a grounded take tied to the news). First-person. 700-1000 words.\n"
        "STRUCTURE (each section 100-200 words):\n"
        "1. Opening: state the news in 2 sentences + your hot take in 1 sentence. No 'imagine a world where' fluff.\n"
        "2. ## Why this matters for [specific dev role]\n"
        "3. ## The technical reality (include 1-2 JavaScript or shell code blocks, 8-20 lines each, actually runnable)\n"
        "4. ## What I'd actually do today (3-5 short numbered steps, very concrete)\n"
        "5. ## Gotchas & unknowns (be honest about limits)\n"
        "6. Closing question tied to reader experience.\n\n"
        "SEO: title 55-70 chars, primary keyword in first 40 chars. tags: 3-4 lowercase single-word tags that match the story (e.g. 'ai', 'javascript', 'cursor', 'github'). Choose tags that match dev.to's popular tag list, not random ones.\n"
        "FORBIDDEN: YAML frontmatter (---), liquid tags ({% %}), em-dashes, asterisk-bold, generic AI platitudes.\n"
        "The article MUST be about the EXACT story provided. Do NOT drift to tangentially related topics."
    ),
    "hashnode": (
        ANTI_AI +
        "\nWrite a Hashnode technical post tied DIRECTLY to the news story. STRICT LIMIT: 600 to 800 words TOTAL. Do not exceed 800. Be concise and focused.\n"
        "STRUCTURE (each section short, one brief paragraph each, max 120 words per section):\n"
        "1. TL;DR: 3 one-line bullets, plain text\n"
        "2. ## The news in 60 seconds (what, when, why it matters)\n"
        "3. ## Under the hood (ONE code block, 8-15 lines, with the real technical concept)\n"
        "4. ## Try it yourself (3 short numbered steps)\n"
        "5. ## Notes & gotchas (2-3 specific points with numbers)\n"
        "6. ## Watch next (ONE concrete signal to track)\n\n"
        "SEO: title = primary SEO keyword first, 55-75 chars. subtitle 120-160 chars with secondary keyword. "
        "slug: url-safe lowercase kebab-case derived from the NEWS TOPIC (e.g. the product/company/event name). Do NOT output 'undefined' in the slug unless the news is literally about JavaScript undefined. "
        "tags: 3-5 lowercase single words picked from: javascript, typescript, python, ai, webdev, opensource, cursor, github, agents, llm, react, nextjs, docker, kubernetes, security, devops.\n"
        "HARD ANCHOR: stay on the given story. No evergreen tutorials. No generic language tutorials.\n"
        "HARD LENGTH CAP: If you hit 800 words, stop. Prefer fewer words over more."
    ),
    "twitter": (
        ANTI_AI +
        "\nWrite a 5-tweet thread about the story. Exact count: 5.\n"
        "- Tweet 1 (hook, max 260 chars): start with a concrete number or surprising fact from the news. No question mark, no emoji. Must make people stop scrolling.\n"
        "- Tweets 2-4 (max 270 chars each): each one contains a DIFFERENT angle — what, why, and so-what. Use specific numbers/names.\n"
        "- Tweet 5 (max 250 chars): end with a sharp question tied to reader workflow. 1 emoji max.\n"
        "hashtags: 3-4 total, lowercase, relevant to THIS story. Mix one broad (#AI, #OpenSource) with two niche ones."
    ),
    "facebook": (
        ANTI_AI +
        "\nWrite a Facebook post for a tech page audience. 120-180 words. ONE paragraph or max two.\n"
        "- Opening line (<90 chars): a human reaction to the news, like a friend texting. No hashtag stuff. Can use one emoji.\n"
        "- Middle: the story in plain English (what, who, why it's interesting). No numbered lists, no bullet lists. Flowing prose only.\n"
        "- One surprising specific fact with a number.\n"
        "- Closing question inviting comment.\n"
        "- 1-2 emojis total in the whole post. Zero hashtags inside body.\n"
        "hashtags field: 2-4 hashtags (lowercase, appended after body). Facebook reach is hurt by too many hashtags.\n"
        "TONE: friendly tech friend, not marketing copy. Absolutely no em-dashes, asterisks, or bullet chars."
    ),
    "instagram": (
        ANTI_AI +
        "\nWrite an Instagram caption for a tech/dev account.\n"
        "STRUCTURE:\n"
        "- First 125 chars (before 'more' cutoff): must contain the hook + the concrete news fact. This is the most important line.\n"
        "- Then: 80-130 more words, short paragraphs (1-3 sentences each), with ONE blank line between paragraphs.\n"
        "- Use 2-3 emojis total, placed at natural break points (not decorating every line).\n"
        "- End with 'save this for later' or a specific action CTA (1 line).\n"
        "- Zero em-dashes, zero asterisks.\n"
        "hashtags: 15-20 total in a mix: 5 broad (#programming, #developer), 8-10 niche (#aicoding, #cursorai, #nextjs), 3-5 branded/trend tied to THIS story. All lowercase, no spaces."
    ),
}

# ============================================================
# NODE FACTORIES
# ============================================================

_nid = 0
def _next_id():
    global _nid; _nid += 1; return f"n{_nid}"

def code_node(name, js, x, y):
    return {
        "parameters": {"jsCode": js},
        "id": _next_id(), "name": name,
        "type": "n8n-nodes-base.code", "typeVersion": 2,
        "position": [x, y],
    }

def http_node(name, method, url, x, y, headers=None, json_body=None,
              string_body=None, timeout=30000):
    p = {"method": method, "url": url, "options": {"timeout": timeout}}
    if headers:
        p["sendHeaders"] = True
        p["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in headers]}
    if json_body is not None:
        p["sendBody"] = True; p["specifyBody"] = "json"; p["jsonBody"] = json_body
    elif string_body is not None:
        p["sendBody"] = True; p["specifyBody"] = "string"; p["body"] = string_body
    return {
        "parameters": p, "id": _next_id(), "name": name,
        "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
        "position": [x, y], "onError": "continueRegularOutput",
    }

def gemini_node(name, x, y):
    return http_node(name, "POST", GEMINI_URL, x, y,
        headers=[("Content-Type", "application/json")],
        json_body="={{ JSON.stringify($json.gemBody) }}", timeout=120000)

def wait_node(name, seconds, x, y):
    return {
        "parameters": {"amount": seconds, "unit": "seconds"},
        "id": _next_id(), "name": name,
        "type": "n8n-nodes-base.wait", "typeVersion": 1.1,
        "position": [x, y],
    }

# ============================================================
# JS CODE BUILDER
# ============================================================

def gemini_request_js(schema_name, user_msg_js, sys_prompt_key=None, temp=0.7, max_tok=16384, input_node=None):
    key = sys_prompt_key or schema_name
    sys_text = json.dumps(PROMPT[key])
    gen_config = json.dumps({
        "temperature": temp,
        "maxOutputTokens": max_tok,
        "responseMimeType": "application/json",
        "responseSchema": SCHEMA[schema_name],
        "thinkingConfig": {"thinkingBudget": 1024},
    })
    # When input_node is set, pull story context from that node explicitly.
    # This avoids losing context when platform flows chain linearly and each
    # platform's previous result would otherwise overwrite the story data.
    input_src = f"$('{input_node}').first().json" if input_node else "$input.first().json"
    return (
        f"const input = {input_src};\n"
        f"const userMsg = {user_msg_js};\n"
        "const gemBody = {\n"
        "  contents: [{ parts: [{ text: userMsg }] }],\n"
        f"  systemInstruction: {{ parts: [{{ text: {sys_text} }}] }},\n"
        f"  generationConfig: {gen_config}\n"
        "};\n"
        "return [{ json: { ...input, gemBody } }];\n"
    )

PARSE_GEMINI = (
    "const raw = $input.first().json;\n"
    "const text = raw?.candidates?.[0]?.content?.parts?.[0]?.text || '';\n"
    "let data;\n"
    "try { data = JSON.parse(text); } catch (e) {\n"
    "  return [{ json: { _failed: true, _error: 'JSON parse: ' + e.message + ' | ' + text.substring(0, 120) } }];\n"
    "}\n"
)

# ============================================================
# WORKFLOW BUILDER
# ============================================================

nodes = []
connections = {}
_prev = None
_x = 100

def add(node):
    global _prev
    nodes.append(node)
    name = node["name"]
    if _prev:
        connections[_prev] = {"main": [[{"node": name, "type": "main", "index": 0}]]}
    _prev = name

def step(dx=200):
    global _x; _x += dx

print("=" * 55)
print("  Building Social Media Autopilot v3 — Cloud Edition")
print("=" * 55)

# ─── SCHEDULE ───
add({"parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 12}]}},
     "id": _next_id(), "name": "\u23f0 Schedule",
     "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [_x, 400]})

# ─── NEWS FETCHERS ───
step()
add(http_node("\U0001F50D NewsData", "GET",
    f"https://newsdata.io/api/1/latest?apikey={CRED['NEWSDATA']}&q=AI+OR+coding+OR+open+source&language=en&category=technology&size=10",
    _x, 400, timeout=15000))
step()
add(http_node("\U0001F50D Reddit", "GET",
    "https://www.reddit.com/r/programming+artificial+MachineLearning+opensource/hot.json?limit=15",
    _x, 400, timeout=10000))
step()
add(http_node("\U0001F50D Google News", "GET",
    f"https://serpapi.com/search.json?engine=google_news&q=AI+technology+coding&api_key={CRED.get('SERPAPI', 'MISSING_SERPAPI_KEY')}",
    _x, 400, timeout=15000))
step()
add(http_node("\U0001F50D GitHub", "GET",
    "https://api.github.com/search/repositories?q=stars:%3E50+pushed:%3E2026-04-14&sort=stars&order=desc&per_page=10",
    _x, 400,
    headers=[("Accept", "application/vnd.github.v3+json"), ("User-Agent", "n8n-bot")],
    timeout=10000))

# ─── AGGREGATE ───
step(250)
add(code_node("\U0001F9F9 Aggregate", """
const a = [];
try { const d = $('\U0001F50D NewsData').first().json; (d?.results || []).slice(0, 10).forEach(x => a.push({ t: x.title || '', d: x.description || '', u: x.link || '', s: 'NewsData' })); } catch(e) {}
try { const d = $('\U0001F50D Reddit').first().json; (d?.data?.children || []).slice(0, 10).forEach(x => { const p = x.data; if (!p.stickied) a.push({ t: p.title || '', d: (p.selftext || '').substring(0, 300), u: p.url || '', s: 'Reddit' }); }); } catch(e) {}
try { const d = $('\U0001F50D Google News').first().json; (d?.news_results || d?.organic_results || []).slice(0, 10).forEach(x => a.push({ t: x.title || '', d: x.snippet || x.description || '', u: x.link || x.url || '', s: x?.source?.name || 'Google' })); } catch(e) {}
try { const d = $('\U0001F50D GitHub').first().json; (d?.items || []).slice(0, 8).forEach(x => a.push({ t: (x.full_name + ': ' + (x.description || '')).substring(0, 200), d: 'Stars:' + x.stargazers_count + ' Lang:' + (x.language || '?'), u: x.html_url || '', s: 'GitHub' })); } catch(e) {}
const seen = new Set();
const uniq = a.filter(x => { const k = (x.t || '').toLowerCase().replace(/[^a-z0-9]/g, '').substring(0, 50); if (!k || seen.has(k)) return false; seen.add(k); return true; });
return [{ json: { articles: uniq, count: uniq.length } }];
""", _x, 400))

# ─── RECENT POSTS FETCHERS (for dedup context) ───
step()
add(http_node("\U0001F4DC Dev.to Recent", "GET",
    "https://dev.to/api/articles/me?per_page=8",
    _x, 400,
    headers=[("api-key", CRED['DEVTO'])],
    timeout=10000))
step()
HN_Q = {
    "query": "query{me{posts(pageSize:8,page:1){edges{node{title slug publishedAt}}}}}",
}
add(http_node("\U0001F4DC Hashnode Recent", "POST",
    "https://gql.hashnode.com/", _x, 400,
    headers=[("Authorization", CRED['HN_TOKEN']), ("Content-Type", "application/json")],
    json_body=json.dumps(HN_Q),
    timeout=10000))
step()
add(http_node("\U0001F4DC FB Recent", "GET",
    f"https://graph.facebook.com/v21.0/{CRED['FB_ID']}/posts?limit=8&fields=message,created_time&access_token={CRED['FB_TK']}",
    _x, 400, timeout=10000))
step()
add(http_node("\U0001F4DC IG Recent", "GET",
    f"https://graph.facebook.com/v21.0/{CRED['IG_ID']}/media?limit=8&fields=caption,timestamp&access_token={CRED['IG_TK']}",
    _x, 400, timeout=10000))

# ─── MERGE RECENT POSTS ───
step()
add(code_node("\U0001F4DA Merge Recent", """
const aggregate = $('\U0001F9F9 Aggregate').first().json;
const recent = { devto: [], hashnode: [], facebook: [], instagram: [] };
try { const d = $('\U0001F4DC Dev.to Recent').first().json; (Array.isArray(d) ? d : []).slice(0, 8).forEach(x => recent.devto.push((x.title || '').substring(0, 120))); } catch(e) {}
try { const d = $('\U0001F4DC Hashnode Recent').first().json; (d?.data?.me?.posts?.edges || []).slice(0, 8).forEach(x => recent.hashnode.push((x.node?.title || '').substring(0, 120))); } catch(e) {}
try { const d = $('\U0001F4DC FB Recent').first().json; (d?.data || []).slice(0, 8).forEach(x => recent.facebook.push((x.message || '').substring(0, 120).replace(/\\n/g, ' '))); } catch(e) {}
try { const d = $('\U0001F4DC IG Recent').first().json; (d?.data || []).slice(0, 8).forEach(x => recent.instagram.push((x.caption || '').substring(0, 120).replace(/\\n/g, ' '))); } catch(e) {}
const allRecent = [...recent.devto, ...recent.hashnode, ...recent.facebook, ...recent.instagram].filter(Boolean);
return [{ json: { ...aggregate, recent, recentTopicsCombined: allRecent.slice(0, 24) } }];
""", _x, 400))

# ─── RESEARCH BRAIN ───
step()
add(code_node("\U0001F4CB Research Prompt", gemini_request_js(
    "research",
    "'RECENT POSTS (AVOID DUPLICATING these topics):\\n' + ((input.recentTopicsCombined || []).map((t, i) => (i+1) + '. ' + t).join('\\n') || 'none yet') + '\\n\\nFRESH ARTICLES (pick ONE):\\n' + JSON.stringify((input.articles || []).slice(0, 20))",
    temp=0.4, max_tok=2500,
), _x, 400))
step()
add(gemini_node("\U0001F9E0 Research AI", _x, 400))
step()
add(code_node("\U0001F4CA Parse Story", PARSE_GEMINI + """
const recent = $('\U0001F4DA Merge Recent').first().json.recent || {};
if (data._failed) {
  return [{ json: {
    original_title: 'AI Developer Tools Update',
    angle: 'New AI coding tools changing workflows',
    key_facts: ['AI assistants widely adopted', 'Open-source models gaining ground', 'Productivity gains reported'],
    topic_tags: ['ai', 'coding'],
    image_prompt_blog: 'modern developer workspace with multiple monitors showing code, cinematic lighting',
    image_prompt_social: 'close-up of hands typing on mechanical keyboard with holographic code projection',
    image_prompt_square: 'bold iconographic AI chip glowing on dark background, high contrast',
    category: 'ai_coding', code_relevance: 'AI-powered coding tools',
    seo_keyword: 'AI coding tools',
    recent,
  }}];
}
return [{ json: { ...data, recent } }];
""", _x, 400))

# ─────────────────────────────────────────────────────────────
# IMAGE GENERATION (3 distinct images per cycle)
#   Imagen 4 rejects multi-instance calls, so 3 separate calls.
#   Chain is linear; each flow accumulates its URL onto the item.
# ─────────────────────────────────────────────────────────────

def add_image_flow(name, prompt_field, aspect, out_field, style, y):
    """Add 5 nodes for one image generation cycle."""
    prompt_node_name = f"\U0001F3A8 {name} Prompt"
    extract_node_name = f"\U0001F5BC\uFE0F Extract {name}"
    parse_story_name = "\U0001F4CA Parse Story"
    step()
    prompt_js = (
        f"const story = $('{parse_story_name}').first().json;\n"
        f"const prev = $input.first().json;\n"
        f"const prompt = '{style}' + 'Scene: ' + (story.{prompt_field} || 'tech news visual');\n"
        f"return [{{ json: {{ ...prev, imgBody: {{ instances: [{{ prompt }}], parameters: {{ sampleCount: 1, aspectRatio: '{aspect}' }} }} }} }}];\n"
    )
    add(code_node(prompt_node_name, prompt_js, _x, y))
    step()
    add(http_node(f"\U0001F4F8 Imagen {name}", "POST", IMAGEN_URL, _x, y,
        headers=[("Content-Type", "application/json")],
        json_body="={{ JSON.stringify($json.imgBody) }}", timeout=60000))
    step()
    extract_js = (
        f"const prev = $('{prompt_node_name}').first().json;\n"
        "const b64 = $input.first().json?.predictions?.[0]?.bytesBase64Encoded || '';\n"
        f"if (!b64) return [{{ json: {{ ...prev, {out_field}: null, uploadBody: JSON.stringify({{base64:''}}) }} }}];\n"
        "return [{ json: { ...prev, uploadBody: JSON.stringify({base64: b64}) } }];\n"
    )
    add(code_node(extract_node_name, extract_js, _x, y))
    step()
    add(http_node(f"\u2601\uFE0F Upload {name}", "POST",
        f"{HELPER_URL}/upload-image", _x, y,
        headers=[("Content-Type", "application/json")],
        json_body="={{ $json.uploadBody || '{\"base64\":\"\"}' }}",
        timeout=30000))
    step()
    record_js = (
        f"const prev = $('{extract_node_name}').first().json;\n"
        "const resp = $input.first().json;\n"
        "const clean = { ...prev };\n"
        "delete clean.uploadBody; delete clean.imgBody;\n"
        f"clean.{out_field} = resp?.url || null;\n"
        "return [{ json: clean }];\n"
    )
    add(code_node(f"\U0001F517 Record {name}", record_js, _x, y))

BLOG_STYLE = 'Photorealistic editorial tech cover illustration, dramatic studio lighting, high detail, 4K, absolutely no text no words no letters no typography. '
SOCIAL_STYLE = 'Cinematic close-up tech scene, bold colors, high contrast, dynamic angle, editorial magazine quality, absolutely no text no words no letters. '
SQUARE_STYLE = 'Bold iconographic tech illustration, vivid saturated color palette, centered composition, abstract graphic style, absolutely no text no words no letters no typography. '

add_image_flow("Blog", "image_prompt_blog", "16:9", "blogImgUrl", BLOG_STYLE, 400)
add_image_flow("Social", "image_prompt_social", "16:9", "socialImgUrl", SOCIAL_STYLE, 400)
add_image_flow("Square", "image_prompt_square", "1:1", "igImgUrl", SQUARE_STYLE, 400)

# Final merge + clean handoff node for platforms
step()
add(code_node("\U0001F517 Get Image URL", """
const prev = $input.first().json;
const clean = { ...prev };
delete clean.uploadBody; delete clean.imgBody;
// Backward-compat: imgUrl = blog image
clean.imgUrl = clean.blogImgUrl || null;
// Fallback: if any URL missing, reuse any available one so no platform misses an image
const any = clean.blogImgUrl || clean.socialImgUrl || clean.igImgUrl || null;
clean.blogImgUrl = clean.blogImgUrl || any;
clean.socialImgUrl = clean.socialImgUrl || any;
clean.igImgUrl = clean.igImgUrl || any;
return [{ json: clean }];
""", _x, 400))

# ─────────────────────────────────────────────────────────────
# DEV.TO (via helper-service proxy — bypasses n8n body mangling)
# ─────────────────────────────────────────────────────────────
step(250)
_devto_msg = (
    "'RECENT DEV.TO POSTS (do NOT repeat or closely echo these titles; write something genuinely different):\\n' "
    "+ ((input.recent?.devto || []).map((t,i)=>(i+1)+'. '+t).join('\\n') || 'none yet') "
    "+ '\\n\\nSTORY TO COVER (hard anchor — do not drift):\\n' "
    "+ 'Title: ' + (input.original_title || '') "
    "+ '\\nAngle: ' + (input.angle || '') "
    "+ '\\nKey facts: ' + JSON.stringify(input.key_facts || []) "
    "+ '\\nPrimary SEO keyword: ' + (input.seo_keyword || '') "
    "+ '\\nCategory: ' + (input.category || '') "
    "+ '\\n\\nWrite the Dev.to post following the system prompt rules. Title MUST include the primary SEO keyword in the first 40 characters.'"
)
add(code_node("\U0001F4DD Dev.to Prompt", gemini_request_js(
    "devto", _devto_msg,
    temp=0.7, max_tok=16384,
    input_node="\U0001F517 Get Image URL",
), _x, 200))
step()
add(gemini_node("\U0001F9E0 Dev.to Brain", _x, 200))
step()
DEVTO_KEY = CRED['DEVTO']
add(code_node("\U0001F4C4 Parse Dev.to", PARSE_GEMINI + f"""
const story = $('\U0001F4DD Dev.to Prompt').first().json;
if (data._failed) return [{{json: {{platform: 'devto', status: 'failed', error: data._error}}}}];
let title = (data.title || story.original_title || 'AI Tech Update').substring(0, 100);
// Style-clean title: kill em/en dashes, collapse spaces
title = title.replace(/[—–]/g, ',').replace(/\\s{2,}/g, ' ').trim();
let body = data.body || '';
body = body.replace(/^---[\\s\\S]*?---\\n?/, '');
body = body.replace(/\\{{%[\\s\\S]*?%\\}}/g, '');
// Style cleanup: em/en dashes → commas. Keep markdown **bold** since Dev.to renders it.
body = body.replace(/[—–]/g, ',');
if (body.length < 50) return [{{json: {{platform: 'devto', status: 'failed', error: 'Article body too short (' + body.length + ' chars)' }}}}];
const tags = (data.tags || ['ai', 'tech']).map(t => t.toLowerCase().replace(/[^a-z0-9]/g, '')).filter(Boolean).slice(0, 4);
const coverUrl = story.blogImgUrl || story.imgUrl || null;
if (coverUrl) body = '![Cover](' + coverUrl + ')\\n\\n' + body;
const articleBody = JSON.stringify({{article: {{title, body_markdown: body, published: true, tags}}}});
const proxyBody = JSON.stringify({{apiKey: '{DEVTO_KEY}', articleBody}});
return [{{json: {{platform: 'devto', title, imgUrl: coverUrl, proxyBody}}}}];
""", _x, 200))
step()
# POST to helper-service proxy (which forwards clean JSON to Dev.to)
add(http_node("\U0001F680 Post Dev.to", "POST",
    f"{HELPER_URL}/proxy/devto", _x, 200,
    headers=[("Content-Type", "application/json")],
    json_body="={{ $json.proxyBody || '{}' }}",
    timeout=30000))
step()
add(code_node("\u2705 Dev.to Result", """
const prev = $('\U0001F4C4 Parse Dev.to').first().json;
if (prev.status === 'failed') return [{ json: prev }];
const d = $input.first().json;
let err = null;
if (d.error) {
  // n8n wraps HTTP errors as { error: { message: "403 - \\"...\\"" }}
  const raw = typeof d.error === 'string' ? d.error : (d.error.message || JSON.stringify(d.error));
  // Try to extract a nested {"error":"...", "status":N} from the "STATUS - BODY" format
  const m = raw.match(/^\\d+ - "?(.+?)"?$/);
  let inner = m ? m[1] : raw;
  try { const p = JSON.parse(inner.replace(/\\\\"/g, '"')); err = p.error || p.message || JSON.stringify(p); } catch(e) { err = inner; }
} else if (d.message) {
  err = d.message;
}
return [{ json: { platform: 'devto', status: d.id ? 'published' : 'failed', url: d.url || d.canonical_url || '', title: prev.title || '', error: err, imgUrl: prev.imgUrl || null } }];
""", _x, 200))

# ─────────────────────────────────────────────────────────────
# HASHNODE
# ─────────────────────────────────────────────────────────────
step(250)
_hn_msg = (
    "'RECENT HASHNODE POSTS (DO NOT duplicate — your post must be materially different in topic AND slug):\\n' "
    "+ ((input.recent?.hashnode || []).map((t,i)=>(i+1)+'. '+t).join('\\n') || 'none yet') "
    "+ '\\n\\nSTORY TO COVER (hard anchor — stay on this exact story):\\n' "
    "+ 'Title: ' + (input.original_title || '') "
    "+ '\\nAngle: ' + (input.angle || '') "
    "+ '\\nKey facts: ' + JSON.stringify(input.key_facts || []) "
    "+ '\\nPrimary SEO keyword: ' + (input.seo_keyword || '') "
    "+ '\\nCategory: ' + (input.category || '') "
    "+ '\\n\\nWrite the Hashnode article. The slug field MUST derive from the news topic (e.g. the product/company/event name) and must NOT contain the word \"undefined\" unless the news is literally about JavaScript undefined behavior.'"
)
add(code_node("\U0001F4DD Hashnode Prompt", gemini_request_js(
    "hashnode", _hn_msg,
    temp=0.7, max_tok=16384,
    input_node="\U0001F517 Get Image URL",
), _x, 400))
step()
add(gemini_node("\U0001F9E0 Hashnode Brain", _x, 400))
step()
HN_PUB = CRED['HN_PUB']
add(code_node("\U0001F4C4 Parse Hashnode", PARSE_GEMINI + f"""
const story = $('\U0001F4DD Hashnode Prompt').first().json;
if (data._failed) return [{{json: {{platform: 'hashnode', status: 'failed', error: data._error}}}}];
let title = (data.title || story.original_title || 'AI Deep Dive').substring(0, 120);
title = title.replace(/[—–]/g, ',').replace(/\\s{2,}/g, ' ').trim();
const subtitle = (data.subtitle || '').replace(/[—–]/g, ',').substring(0, 160);
let body = data.body || '';
// Style cleanup: em/en dashes → commas (keep markdown bold since Hashnode renders it)
body = body.replace(/[—–]/g, ',');
if (body.length < 50) return [{{json: {{platform: 'hashnode', status: 'failed', error: 'Body too short'}}}}];
const tags = (data.tags || ['ai', 'tech']).map(t => ({{slug: t.toLowerCase().replace(/[^a-z0-9-]/g, ''), name: t.charAt(0).toUpperCase() + t.slice(1)}})).slice(0, 5);
// Use blog image (16:9 cover)
const coverUrl = story.blogImgUrl || story.imgUrl || null;
if (coverUrl) body = '![Cover](' + coverUrl + ')\\n\\n' + body;
// Derive slug: prefer model-provided slug, else derive from title (NOT from generic concepts)
let slug = (data.slug || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').substring(0, 80);
if (!slug) slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').substring(0, 80);
// Defensive: if slug starts with "undefined-" it means the model drifted; append a unique suffix from key_facts to force uniqueness
if (slug.startsWith('undefined') && !(story.original_title || '').toLowerCase().includes('undefined')) {{
  slug = 'news-' + Date.now().toString(36);
}}
const postBody = {{
  query: 'mutation PublishPost($i:PublishPostInput!){{publishPost(input:$i){{post{{id title url}}}}}}',
  variables: {{i: {{publicationId: '{HN_PUB}', title, contentMarkdown: body, tags, subtitle, slug}}}}
}};
return [{{json: {{platform: 'hashnode', title, imgUrl: coverUrl, postBody}}}}];
""", _x, 400))
step()
add(http_node("\U0001F680 Post Hashnode", "POST", "https://gql.hashnode.com/", _x, 400,
    headers=[("Authorization", CRED['HN_TOKEN']), ("Content-Type", "application/json")],
    json_body="={{ $json.postBody ? JSON.stringify($json.postBody) : '{\"query\":\"{ __typename }\"}' }}",
    timeout=30000))
step()
add(code_node("\u2705 Hashnode Result", """
const prev = $('\U0001F4C4 Parse Hashnode').first().json;
if (prev.status === 'failed') return [{ json: prev }];
const d = $input.first().json;
const p = d?.data?.publishPost?.post;
return [{ json: { platform: 'hashnode', status: p?.id ? 'published' : 'failed', url: p?.url || '', title: prev.title || '', error: d?.errors?.[0]?.message || null, imgUrl: prev.imgUrl || null } }];
""", _x, 400))

# ─────────────────────────────────────────────────────────────
# TWITTER (OAuth 1.0a + pure JS HMAC-SHA1)
# ─────────────────────────────────────────────────────────────
step(250)
_tw_msg = (
    "'STORY TO COVER (hard anchor — every tweet stays on this story):\\n' "
    "+ 'Title: ' + (input.original_title || '') "
    "+ '\\nAngle: ' + (input.angle || '') "
    "+ '\\nKey facts: ' + JSON.stringify(input.key_facts || []) "
    "+ '\\nSEO keyword: ' + (input.seo_keyword || '') "
    "+ '\\n\\nWrite the 5-tweet thread per the system rules. Tweet 1 MUST open with a concrete number or specific company/product name from the story.'"
)
add(code_node("\U0001F426 Twitter Prompt", gemini_request_js(
    "twitter", _tw_msg,
    temp=0.6, max_tok=2000,
    input_node="\U0001F517 Get Image URL",
), _x, 200))
step()
add(gemini_node("\U0001F9E0 Twitter Brain", _x, 200))
step()
TW = {k: CRED[k] for k in ["TW_CK", "TW_CS", "TW_AT", "TW_AS"]}

HMAC_SHA1_JS = r'''
function _sha1B(bytes) {
  var H0=0x67452301,H1=0xEFCDAB89,H2=0x98BADCFE,H3=0x10325476,H4=0xC3D2E1F0;
  var b=bytes.slice();
  var ml=b.length*8;
  b.push(0x80);
  while(b.length%64!==56)b.push(0);
  b.push(0,0,0,0,(ml>>>24)&0xff,(ml>>>16)&0xff,(ml>>>8)&0xff,ml&0xff);
  for(var ch=0;ch<b.length;ch+=64){
    var w=[];
    for(var j=0;j<16;j++)w[j]=(b[ch+j*4]<<24)|(b[ch+j*4+1]<<16)|(b[ch+j*4+2]<<8)|b[ch+j*4+3];
    for(var j=16;j<80;j++){var x=w[j-3]^w[j-8]^w[j-14]^w[j-16];w[j]=(x<<1)|(x>>>31)}
    var a=H0,b2=H1,c=H2,d=H3,e=H4;
    for(var j=0;j<80;j++){
      var f,k2;
      if(j<20){f=(b2&c)|((~b2)&d);k2=0x5A827999}else if(j<40){f=b2^c^d;k2=0x6ED9EBA1}else if(j<60){f=(b2&c)|(b2&d)|(c&d);k2=0x8F1BBCDC}else{f=b2^c^d;k2=0xCA62C1D6}
      var tmp=(((a<<5)|(a>>>27))+f+e+k2+w[j])>>>0;
      e=d;d=c;c=((b2<<30)|(b2>>>2))>>>0;b2=a;a=tmp;
    }
    H0=(H0+a)>>>0;H1=(H1+b2)>>>0;H2=(H2+c)>>>0;H3=(H3+d)>>>0;H4=(H4+e)>>>0;
  }
  var r=[];[H0,H1,H2,H3,H4].forEach(function(h){r.push((h>>>24)&0xff,(h>>>16)&0xff,(h>>>8)&0xff,h&0xff)});
  return r;
}
function _strToBytes(s){var r=[];for(var i=0;i<s.length;i++)r.push(s.charCodeAt(i)&0xff);return r}
function _hmacSha1(keyStr,msgStr){
  var kb=_strToBytes(keyStr);
  if(kb.length>64)kb=_sha1B(kb);
  while(kb.length<64)kb.push(0);
  var ip=[],op=[];
  for(var i=0;i<64;i++){ip.push(kb[i]^0x36);op.push(kb[i]^0x5c)}
  var inner=_sha1B(ip.concat(_strToBytes(msgStr)));
  return _sha1B(op.concat(inner));
}
function hmacB64(key,msg){
  var b=_hmacSha1(key,msg),c='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/',r='';
  for(var i=0;i<b.length;i+=3){var n=(b[i]<<16)|((b[i+1]||0)<<8)|(b[i+2]||0);var p=i+1>=b.length?2:(i+2>=b.length?1:0);r+=c[(n>>18)&63]+c[(n>>12)&63]+(p>=2?'=':c[(n>>6)&63])+(p>=1?'=':c[n&63])}
  return r;
}
'''

add(code_node("\U0001F4C4 Build Tweet", PARSE_GEMINI + HMAC_SHA1_JS + f"""
if (data._failed) return [{{json: {{platform: 'twitter', status: 'failed', error: data._error}}}}];
const thread = data.thread || [];
// Ensure every hashtag starts with '#' — the model often drops the sign
const hashtags = (data.hashtags || []).map(h => {{
  const clean = String(h).replace(/^#+/, '').replace(/\\s+/g, '');
  return clean ? '#' + clean : '';
}}).filter(Boolean);
let tweetText = (thread[0] || 'AI news update') + '\\n' + hashtags.slice(0, 3).join(' ');
// Style cleanup: em/en dashes → commas
tweetText = tweetText.replace(/[—–]/g, ',').replace(/\\*\\*(.+?)\\*\\*/g, '$1').replace(/\\*(.+?)\\*/g, '$1');
if (tweetText.length > 280) tweetText = tweetText.substring(0, 277) + '...';
const CK='{TW["TW_CK"]}',CS='{TW["TW_CS"]}',AT='{TW["TW_AT"]}',AS='{TW["TW_AS"]}';
const twUrl='https://api.twitter.com/2/tweets';
const ts=Math.floor(Date.now()/1000).toString();
const nc=ts+Math.random().toString(36).substring(2,10);
const oP={{oauth_consumer_key:CK,oauth_nonce:nc,oauth_signature_method:'HMAC-SHA1',oauth_timestamp:ts,oauth_token:AT,oauth_version:'1.0'}};
const pStr=Object.keys(oP).sort().map(k=>encodeURIComponent(k)+'='+encodeURIComponent(oP[k])).join('&');
const baseStr='POST&'+encodeURIComponent(twUrl)+'&'+encodeURIComponent(pStr);
oP.oauth_signature=hmacB64(encodeURIComponent(CS)+'&'+encodeURIComponent(AS),baseStr);
const auth='OAuth '+Object.keys(oP).sort().map(k=>encodeURIComponent(k)+'="'+encodeURIComponent(oP[k])+'"').join(', ');
const _twPrompt = $('\U0001F426 Twitter Prompt').first().json;
return [{{json: {{platform: 'twitter', tweetBody: {{text: tweetText}}, authHeader: auth, imgUrl: _twPrompt.socialImgUrl || _twPrompt.imgUrl || null}}}}];
""", _x, 200))
step()
tw = http_node("\U0001F680 Post Tweet", "POST", "https://api.twitter.com/2/tweets", _x, 200, timeout=15000)
tw["parameters"]["sendHeaders"] = True
tw["parameters"]["headerParameters"] = {"parameters": [
    {"name": "Authorization", "value": "={{ $json.authHeader || '' }}"},
    {"name": "Content-Type", "value": "application/json"},
]}
tw["parameters"]["sendBody"] = True
tw["parameters"]["specifyBody"] = "json"
tw["parameters"]["jsonBody"] = "={{ $json.tweetBody ? JSON.stringify($json.tweetBody) : '{\"text\":\"skip\"}' }}"
add(tw)
step()
# Fixed: capture real error from n8n error wrapper
add(code_node("\u2705 Twitter Result", """
const prev = $('\U0001F4C4 Build Tweet').first().json;
if (prev.status === 'failed') return [{ json: prev }];
const d = $input.first().json;
const tid = d?.data?.id;
let err = null;
if (!tid) {
  const raw = typeof d?.error === 'string' ? d.error : (d?.error?.message || '');
  // Strip "402 - " prefix and parse JSON body
  const m = raw.match(/^\\d+ - (.+)$/);
  let inner = m ? m[1] : raw;
  try {
    const p = JSON.parse(inner.replace(/\\\\"/g, '"'));
    if (p.title === 'CreditsDepleted') {
      err = 'X API credits exhausted (upgrade plan at developer.x.com or wait for reset)';
    } else {
      err = p.detail || p.title || p.error || JSON.stringify(p).substring(0, 150);
    }
  } catch(e) {
    err = d?.errors?.[0]?.message || d?.detail || d?.title || inner || 'Unknown error';
  }
}
return [{ json: { platform: 'twitter', status: tid ? 'published' : 'failed', url: tid ? 'https://x.com/i/web/status/' + tid : '', error: err, imgUrl: prev.imgUrl || null } }];
""", _x, 200))

# ─────────────────────────────────────────────────────────────
# FACEBOOK (with image support via /photos endpoint)
# ─────────────────────────────────────────────────────────────
step(250)
FB_ID, FB_TK = CRED['FB_ID'], CRED['FB_TK']
_fb_msg = (
    "'RECENT FACEBOOK POSTS (do NOT repeat these, pick a different angle):\\n' "
    "+ ((input.recent?.facebook || []).map((t,i)=>(i+1)+'. '+t).join('\\n') || 'none yet') "
    "+ '\\n\\nSTORY TO COVER (hard anchor):\\n' "
    "+ 'Title: ' + (input.original_title || '') "
    "+ '\\nAngle: ' + (input.angle || '') "
    "+ '\\nKey facts: ' + JSON.stringify(input.key_facts || []) "
    "+ '\\n\\nWrite a Facebook post per the system rules. Remember: ZERO em-dashes, ZERO asterisks, flowing human prose only.'"
)
add(code_node("\U0001F4D8 Facebook Prompt", gemini_request_js(
    "facebook", _fb_msg,
    temp=0.75, max_tok=1500,
    input_node="\U0001F517 Get Image URL",
), _x, 400))
step()
add(gemini_node("\U0001F9E0 Facebook Brain", _x, 400))
step()
_fb_parse_js = (
    PARSE_GEMINI
    + "if (data._failed) return [{ json: { platform: 'facebook', status: 'failed', error: data._error } }];\n"
    + "const fbPrompt = $('\U0001F4D8 Facebook Prompt').first().json;\n"
    + "const imgUrl = fbPrompt.socialImgUrl || fbPrompt.imgUrl || null;\n"
    + "// Post-hoc style cleanup: strip em-dashes, asterisk-bold, triple+ newlines\n"
    + "let bodyRaw = (data.body || '').replace(/[—–]/g, ',').replace(/\\*\\*(.+?)\\*\\*/g, '$1').replace(/\\*(.+?)\\*/g, '$1').replace(/\\n{3,}/g, '\\n\\n').trim();\n"
    + "// Normalise hashtags: ensure every tag starts with '#' and has no whitespace inside\n"
    + "const hashtags = (data.hashtags || []).map(h => { const c = String(h).replace(/^#+/, '').replace(/\\s+/g, ''); return c ? '#' + c : ''; }).filter(Boolean);\n"
    + "const msg = bodyRaw + '\\n\\n' + hashtags.join(' ');\n"
    + f"const base = 'https://graph.facebook.com/v21.0/{FB_ID}';\n"
    + f"const tk = '{FB_TK}';\n"
    + "let fbUrl;\n"
    + "if (imgUrl) {\n"
    + "  fbUrl = base + '/photos?caption=' + encodeURIComponent(msg) + '&url=' + encodeURIComponent(imgUrl) + '&access_token=' + encodeURIComponent(tk);\n"
    + "} else {\n"
    + "  fbUrl = base + '/feed?message=' + encodeURIComponent(msg) + '&access_token=' + encodeURIComponent(tk);\n"
    + "}\n"
    + "return [{ json: { platform: 'facebook', fbUrl, hasImage: !!imgUrl, imgUrl } }];\n"
)
add(code_node("\U0001F4C4 Parse Facebook", _fb_parse_js, _x, 400))
step()
add(http_node("\U0001F680 Post Facebook", "POST",
    "={{ $json.fbUrl || 'https://graph.facebook.com/v21.0/me' }}", _x, 400, timeout=15000))
step()
add(code_node("\u2705 Facebook Result", """
const prev = $('\U0001F4C4 Parse Facebook').first().json;
if (prev.status === 'failed') return [{ json: prev }];
const d = $input.first().json;
const ok = d.id || d.post_id;
return [{ json: { platform: 'facebook', status: ok ? 'published' : 'failed', post_id: d.id || d.post_id || '', hasImage: prev.hasImage || false, error: d.error?.message || null, imgUrl: prev.imgUrl || null } }];
""", _x, 400))

# ─────────────────────────────────────────────────────────────
# INSTAGRAM (images served from VM via nginx)
# ─────────────────────────────────────────────────────────────
step(250)
IG_ID, IG_TK = CRED['IG_ID'], CRED['IG_TK']
_ig_msg = (
    "'RECENT INSTAGRAM CAPTIONS (do NOT repeat these angles):\\n' "
    "+ ((input.recent?.instagram || []).map((t,i)=>(i+1)+'. '+t).join('\\n') || 'none yet') "
    "+ '\\n\\nSTORY TO COVER (hard anchor):\\n' "
    "+ 'Title: ' + (input.original_title || '') "
    "+ '\\nAngle: ' + (input.angle || '') "
    "+ '\\nKey facts: ' + JSON.stringify(input.key_facts || []) "
    "+ '\\nSEO keyword: ' + (input.seo_keyword || '') "
    "+ '\\n\\nWrite the Instagram caption per the system rules. First 125 chars are critical — front-load the concrete news fact + hook.'"
)
add(code_node("\U0001F4F8 Instagram Prompt", gemini_request_js(
    "instagram", _ig_msg,
    temp=0.75, max_tok=1500,
    input_node="\U0001F517 Get Image URL",
), _x, 200))
step()
add(gemini_node("\U0001F9E0 Instagram Brain", _x, 200))
step()
_ig_parse_js = (
    PARSE_GEMINI
    + "if (data._failed) return [{ json: { platform: 'instagram', status: 'failed', error: data._error } }];\n"
    + "const igPrompt = $('\U0001F4F8 Instagram Prompt').first().json;\n"
    + "// Post-hoc style cleanup for IG caption\n"
    + "let cap = (data.caption || '').replace(/[—–]/g, ',').replace(/\\*\\*(.+?)\\*\\*/g, '$1').replace(/\\*(.+?)\\*/g, '$1').trim();\n"
    + "// Normalise hashtags: ensure every tag starts with '#' and has no whitespace inside\n"
    + "const hashtags = (data.hashtags || []).map(h => { const c = String(h).replace(/^#+/, '').replace(/\\s+/g, ''); return c ? '#' + c : ''; }).filter(Boolean);\n"
    + "const caption = cap + '\\n\\n' + hashtags.join(' ');\n"
    + "const imgUrl = igPrompt.igImgUrl || igPrompt.imgUrl || null;\n"
    + "if (!imgUrl) return [{ json: { platform: 'instagram', status: 'failed', error: 'No image URL' } }];\n"
    + f"const igUrl = 'https://graph.facebook.com/v21.0/{IG_ID}/media?image_url=' + encodeURIComponent(imgUrl) + '&caption=' + encodeURIComponent(caption) + '&access_token=' + encodeURIComponent('{IG_TK}');\n"
    + "return [{ json: { platform: 'instagram', igUrl, imgUrl } }];\n"
)
add(code_node("\U0001F4C4 Parse Instagram", _ig_parse_js, _x, 200))
step()
add(http_node("\U0001F4E4 IG Create Media", "POST",
    "={{ $json.igUrl || 'https://graph.facebook.com/v21.0/me' }}", _x, 200, timeout=30000))
step()
add(wait_node("\u23F3 Wait", 10, _x, 200))
step()
add(code_node("\U0001F4CB IG Publish", f"""
const parseResult = $('\U0001F4C4 Parse Instagram').first().json;
if (parseResult.status === 'failed') return [{{json: parseResult}}];
const createD = $('\U0001F4E4 IG Create Media').first().json;
if (!createD.id) {{
  // Extract clean error from n8n's error wrapper
  const raw = typeof createD.error === 'string' ? createD.error : (createD.error?.message || JSON.stringify(createD));
  const m = raw.match(/^\\d+ - "?(.+?)"?$/);
  let inner = m ? m[1] : raw;
  let cleanErr = inner;
  try {{
    const p = JSON.parse(inner.replace(/\\\\"/g, '"'));
    cleanErr = p.error?.error_user_msg || p.error?.message || p.message || inner;
  }} catch(e) {{}}
  return [{{json: {{platform: 'instagram', status: 'failed', error: cleanErr.substring(0, 200)}}}}];
}}
const pubUrl = 'https://graph.facebook.com/v21.0/{IG_ID}/media_publish?creation_id=' + createD.id + '&access_token=' + encodeURIComponent('{IG_TK}');
return [{{json: {{platform: 'instagram', pubUrl}}}}];
""", _x, 200))
step()
add(http_node("\U0001F4E4 IG Publish", "POST",
    "={{ $json.pubUrl || 'https://graph.facebook.com/v21.0/me' }}", _x, 200, timeout=30000))
step()
add(code_node("\u2705 Instagram Result", """
const prev = $('\U0001F4CB IG Publish').first().json;
if (prev.status === 'failed') return [{ json: prev }];
const d = $input.first().json;
return [{ json: { platform: 'instagram', status: d.id ? 'published' : 'failed', media_id: d.id || '', error: d.error?.message || null } }];
""", _x, 200))

# ─────────────────────────────────────────────────────────────
# GITHUB — daily-notes auto-commit
#   Writes a short markdown summary of today's story to a public repo.
#   Skipped gracefully if GH_TOKEN/GH_OWNER/GH_REPO are not configured.
# ─────────────────────────────────────────────────────────────
step(250)
GH_TOKEN = CRED.get("GH_TOKEN", "")
GH_OWNER = CRED.get("GH_OWNER", "")
GH_REPO = CRED.get("GH_REPO", "")
add(code_node("\U0001F4DD GitHub Prompt", f"""
const story = $('\U0001F4CA Parse Story').first().json;
const gh = {{
  token: {json.dumps(GH_TOKEN)},
  owner: {json.dumps(GH_OWNER)},
  repo:  {json.dumps(GH_REPO)},
}};
if (!gh.token || !gh.owner || !gh.repo) {{
  return [{{ json: {{ platform: 'github', status: 'skipped', error: 'GH_TOKEN/GH_OWNER/GH_REPO not configured' }} }}];
}}

// Build a concise markdown note (front-matter + TL;DR + key facts + source links).
const title = (story.original_title || 'Tech news update').replace(/"/g, "'");
const angle = (story.angle || '').replace(/"/g, "'");
const facts = Array.isArray(story.key_facts) ? story.key_facts : [];
const seo = story.seo_keyword || '';
const cat = story.category || 'tech';

// Pull a short excerpt from the Dev.to body if available.
let excerpt = '';
try {{
  const dt = $('\U0001F4C4 Parse Dev.to').first().json;
  const pb = dt?.proxyBody ? JSON.parse(dt.proxyBody) : null;
  const ab = pb?.articleBody ? JSON.parse(pb.articleBody) : null;
  const body = ab?.article?.body_markdown || '';
  // strip cover image + drop first heading, keep first 2-3 paragraphs
  const stripped = body
    .replace(/^!\\[Cover\\]\\([^)]*\\)\\s*/m, '')
    .replace(/^#+\\s.*$/gm, '')
    .trim();
  excerpt = stripped.split(/\\n\\n/).slice(0, 3).join('\\n\\n').substring(0, 1500);
}} catch (e) {{ excerpt = ''; }}

// Source links from published result nodes.
const srcLinks = [];
try {{ const d = $('\u2705 Dev.to Result').first().json;    if (d?.url) srcLinks.push('- Dev.to: ' + d.url); }} catch(e) {{}}
try {{ const d = $('\u2705 Hashnode Result').first().json;  if (d?.url) srcLinks.push('- Hashnode: ' + d.url); }} catch(e) {{}}
try {{ const d = $('\u2705 Twitter Result').first().json;   if (d?.url) srcLinks.push('- X/Twitter: ' + d.url); }} catch(e) {{}}

const today = new Date().toISOString().slice(0, 10);
const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').substring(0, 60) || 'story';
const path = 'notes/' + today + '-' + slug + '.md';

const frontMatter = [
  '---',
  'date: ' + today,
  'title: "' + title + '"',
  'category: ' + cat,
  'seo_keyword: "' + seo + '"',
  'source: autopilot',
  '---',
  ''
].join('\\n');

const keyFactsMd = facts.length
  ? '## Key facts\\n\\n' + facts.map(f => '- ' + f).join('\\n') + '\\n\\n'
  : '';

const linksMd = srcLinks.length
  ? '## Where this was published\\n\\n' + srcLinks.join('\\n') + '\\n'
  : '';

const md =
  frontMatter +
  '# ' + title + '\\n\\n' +
  (angle ? '> ' + angle + '\\n\\n' : '') +
  keyFactsMd +
  (excerpt ? '## TL;DR\\n\\n' + excerpt + '\\n\\n' : '') +
  linksMd;

// GitHub Contents API needs base64 content. btoa in n8n Code nodes works on strings.
const b64 = Buffer.from(md, 'utf8').toString('base64');
const apiUrl = 'https://api.github.com/repos/' + gh.owner + '/' + gh.repo + '/contents/' + path;

const ghBody = {{
  message: 'notes: ' + today + ' — ' + title.substring(0, 60),
  content: b64,
  branch: 'main'
}};

return [{{ json: {{ platform: 'github', apiUrl, ghBody, token: gh.token, path }} }}];
""", _x, 500))
step()
gh_put = http_node("\U0001F680 Push to GitHub", "PUT",
    "={{ $json.apiUrl || 'https://api.github.com/' }}", _x, 500, timeout=20000)
gh_put["parameters"]["sendHeaders"] = True
gh_put["parameters"]["headerParameters"] = {"parameters": [
    {"name": "Authorization", "value": "=Bearer {{ $json.token || 'missing' }}"},
    {"name": "Accept",        "value": "application/vnd.github+json"},
    {"name": "X-GitHub-Api-Version", "value": "2022-11-28"},
    {"name": "User-Agent",    "value": "social-media-autopilot"},
]}
gh_put["parameters"]["sendBody"] = True
gh_put["parameters"]["specifyBody"] = "json"
gh_put["parameters"]["jsonBody"] = "={{ $json.ghBody ? JSON.stringify($json.ghBody) : '{}' }}"
add(gh_put)
step()
add(code_node("\u2705 GitHub Result", """
const prev = $('\U0001F4DD GitHub Prompt').first().json;
if (prev.status === 'skipped') return [{ json: prev }];
const d = $input.first().json;
const ok = !!(d?.content?.html_url || d?.commit?.sha);
let err = null;
if (!ok) {
  const raw = typeof d?.error === 'string' ? d.error : (d?.error?.message || d?.message || '');
  const m = raw.match(/^\\d+ - "?(.+?)"?$/);
  err = m ? m[1] : (raw || 'Unknown error');
  try { const p = JSON.parse(err.replace(/\\\\"/g, '"')); err = p.message || err; } catch(e) {}
}
return [{ json: {
  platform: 'github',
  status: ok ? 'published' : 'failed',
  url: d?.content?.html_url || '',
  path: prev.path || '',
  error: err
}}];
""", _x, 500))

# ─────────────────────────────────────────────────────────────
# COLLECT RESULTS + WHATSAPP REPORT
# ─────────────────────────────────────────────────────────────
step(250)
add(code_node("\U0001F4CA Collect", """
const story = $('\U0001F4CA Parse Story').first().json;
const platforms = {};
const resultNodes = ['\u2705 Dev.to Result', '\u2705 Hashnode Result', '\u2705 Twitter Result', '\u2705 Facebook Result', '\u2705 Instagram Result', '\u2705 GitHub Result'];
for (const nodeName of resultNodes) {
  try { const d = $(nodeName).first().json; platforms[d.platform || nodeName] = d; } catch(e) {}
}
return [{ json: { story: story.original_title || 'Tech News', platforms } }];
""", _x, 400))
step()
WA_NUM = CRED['WA_NUM']
add(code_node("\U0001F4CB Build Report", """
const data = $input.first().json;
const p = data.platforms || {};
const icon = { devto: '\U0001F4DD', hashnode: '\U0001F4DD', twitter: '\U0001F426', facebook: '\U0001F4D8', instagram: '\U0001F4F8', github: '\U0001F419' };
const pn = { devto: 'Dev.to', hashnode: 'Hashnode', twitter: 'X/Twitter', facebook: 'Facebook', instagram: 'Instagram', github: 'GitHub' };
const now = new Date().toLocaleString('en-US', { timeZone: 'Asia/Dhaka', dateStyle: 'medium', timeStyle: 'short' });
let msg = '\U0001F916 SOCIAL MEDIA AUTOPILOT v3\\n\U0001F4CA Cycle Report\\n\u23F0 ' + now + ' (BDT)\\n' + '\u2501'.repeat(28) + '\\n\\n\U0001F4F0 Story: ' + data.story + '\\n\\nPOSTS:\\n\\n';
let pub = 0;
for (const [k, d] of Object.entries(p)) {
  const ok = d.status === 'published'; if (ok) pub++;
  msg += (icon[k] || '\U0001F4CC') + ' ' + (pn[k] || k) + ': ' + (ok ? '\u2705' : '\u274C') + ' ' + d.status + '\\n';
  if (d.url) msg += '   \U0001F517 ' + d.url + '\\n';
  if (d.error) {
    const errStr = typeof d.error === 'string' ? d.error : (d.error.message || JSON.stringify(d.error));
    msg += '   \u26A0\uFE0F ' + errStr.substring(0, 180) + '\\n';
  }
  msg += '\\n';
}
msg += '\u2501'.repeat(28) + '\\n\U0001F4C8 Score: ' + pub + '/' + Object.keys(p).length + ' platforms\\n\U0001F504 Next cycle in 12 hours';
return [{ json: { waBody: { messaging_product: 'whatsapp', to: '""" + WA_NUM + """', type: 'text', text: { preview_url: true, body: msg } } } }];
""", _x, 400))
step()
add(http_node("\U0001F4F2 WhatsApp", "POST",
    f"https://graph.facebook.com/v21.0/{CRED['WA_PH']}/messages", _x, 400,
    headers=[("Authorization", f"Bearer {CRED['WA_TK']}"), ("Content-Type", "application/json")],
    json_body="={{ JSON.stringify($json.waBody) }}", timeout=15000))

# ─── ERROR HANDLER ───
nodes.append(code_node("\U0001F6A8 Error Msg", """
const now = new Date().toLocaleString('en-US', { timeZone: 'Asia/Dhaka' });
const err = JSON.stringify($input.first().json || {}).substring(0, 500);
return [{ json: { waBody: { messaging_product: 'whatsapp', to: '""" + WA_NUM + """', type: 'text', text: { body: '\U0001F6A8 WORKFLOW ERROR at ' + now + '\\n' + err } } } }];
""", _x + 200, 700))
nodes.append(http_node("\U0001F6A8 Send Alert", "POST",
    f"https://graph.facebook.com/v21.0/{CRED['WA_PH']}/messages", _x + 400, 700,
    headers=[("Authorization", f"Bearer {CRED['WA_TK']}"), ("Content-Type", "application/json")],
    json_body="={{ JSON.stringify($json.waBody) }}", timeout=15000))
connections["\U0001F6A8 Error Msg"] = {"main": [[{"node": "\U0001F6A8 Send Alert", "type": "main", "index": 0}]]}


# ============================================================
# DEPLOY
# ============================================================

workflow = {
    "name": "Social Media Autopilot v3 \u2014 Gemini AI",
    "nodes": nodes,
    "connections": connections,
    "settings": {
        "executionOrder": "v1",
        "saveManualExecutions": True,
        "callerPolicy": "workflowsFromSameOwner",
    },
}

hdr = {"X-N8N-API-KEY": N8N["key"], "Content-Type": "application/json"}
base = N8N["base"]

print("\nRemoving old workflows...")
r = requests.get(f"{base}/workflows", headers=hdr)
if r.ok:
    for wf in r.json().get("data", []):
        requests.post(f"{base}/workflows/{wf['id']}/deactivate", headers=hdr)
        requests.delete(f"{base}/workflows/{wf['id']}", headers=hdr)
        print(f"  \U0001F5D1\uFE0F  Deleted: {wf['name']}")

print("Importing workflow...")
r = requests.post(f"{base}/workflows", headers=hdr, json=workflow)
if r.status_code not in [200, 201]:
    print(f"  \u274C Import failed: {r.status_code}\n{r.text[:500]}")
    exit(1)
wf_id = r.json()["id"]
print(f"  \u2705 Imported: {wf_id}")

requests.put(f"{base}/workflows/{wf_id}", headers=hdr,
    json={"settings": {**workflow["settings"], "errorWorkflow": wf_id}})


# ============================================================
# DRY RUN TESTS
# ============================================================

print("\n" + "=" * 55)
print("  DRY RUN \u2014 Testing all connections")
print("=" * 55)
tests = []

# Gemini basic
try:
    r = requests.post(GEMINI_URL, json={
        "contents": [{"parts": [{"text": "Say OK"}]}],
        "generationConfig": {"maxOutputTokens": 10}
    }, timeout=15)
    tests.append(("Gemini Basic", bool(r.json().get("candidates")), "OK"))
except Exception as e:
    tests.append(("Gemini Basic", False, str(e)[:60]))

# Gemini responseSchema
try:
    r = requests.post(GEMINI_URL, json={
        "contents": [{"parts": [{"text": "Test"}]}],
        "generationConfig": {
            "maxOutputTokens": 100,
            "responseMimeType": "application/json",
            "responseSchema": {"type": "OBJECT", "properties": {"ok": {"type": "STRING"}}, "required": ["ok"]},
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }, timeout=20)
    t = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    tests.append(("Gemini Schema", isinstance(t, dict) and "ok" in t, "Valid JSON \u2713"))
except Exception as e:
    tests.append(("Gemini Schema", False, str(e)[:60]))

# Imagen
try:
    r = requests.post(IMAGEN_URL, json={
        "instances": [{"prompt": "blue cube"}], "parameters": {"sampleCount": 1}
    }, timeout=30)
    tests.append(("Imagen 4.0", bool(r.json().get("predictions")), "OK"))
except Exception as e:
    tests.append(("Imagen 4.0", False, str(e)[:60]))

# Helper service
try:
    r = requests.get(f"{HELPER_URL}/health", timeout=5)
    tests.append(("Helper Service", r.ok, r.json().get("status", "?")))
except Exception as e:
    tests.append(("Helper Service", False, str(e)[:60]))

# Dev.to
try:
    r = requests.get("https://dev.to/api/articles/me", headers={"api-key": CRED["DEVTO"]}, timeout=10)
    tests.append(("Dev.to", r.ok, f"{len(r.json())} articles"))
except Exception as e:
    tests.append(("Dev.to", False, str(e)[:60]))

# Hashnode
try:
    r = requests.post("https://gql.hashnode.com", json={"query": "{me{username}}"}, headers={"Authorization": CRED["HN_TOKEN"]}, timeout=10)
    tests.append(("Hashnode", r.ok, f"@{r.json()['data']['me']['username']}"))
except Exception as e:
    tests.append(("Hashnode", False, str(e)[:60]))

# Twitter
try:
    ts = str(int(time.time())); nonce = hashlib.md5(ts.encode()).hexdigest()
    url = "https://api.twitter.com/2/users/me"
    p = {"oauth_consumer_key": CRED["TW_CK"], "oauth_nonce": nonce, "oauth_signature_method": "HMAC-SHA1",
         "oauth_timestamp": ts, "oauth_token": CRED["TW_AT"], "oauth_version": "1.0"}
    bs = "&".join(f"{urllib.parse.quote(k, '~')}={urllib.parse.quote(p[k], '~')}" for k in sorted(p))
    sb = f"GET&{urllib.parse.quote(url, '~')}&{urllib.parse.quote(bs, '~')}"
    sig = base64.b64encode(hmac.new(
        f"{urllib.parse.quote(CRED['TW_CS'], '~')}&{urllib.parse.quote(CRED['TW_AS'], '~')}".encode(),
        sb.encode(), hashlib.sha1).digest()).decode()
    p["oauth_signature"] = sig
    auth = "OAuth " + ", ".join(f'{urllib.parse.quote(k, "~")}="{urllib.parse.quote(v, "~")}"' for k, v in sorted(p.items()))
    r = requests.get(url, headers={"Authorization": auth}, timeout=10)
    tests.append(("Twitter", r.ok, f"@{r.json()['data']['username']}"))
except Exception as e:
    tests.append(("Twitter", False, str(e)[:60]))

# Facebook
try:
    r = requests.get(f"https://graph.facebook.com/v21.0/{CRED['FB_ID']}", params={"fields": "name", "access_token": CRED["FB_TK"]}, timeout=10)
    tests.append(("Facebook", "name" in r.json(), r.json().get("name", "?")))
except Exception as e:
    tests.append(("Facebook", False, str(e)[:60]))

# Instagram
try:
    r = requests.get(f"https://graph.facebook.com/v21.0/{CRED['IG_ID']}", params={"fields": "username", "access_token": CRED["IG_TK"]}, timeout=10)
    tests.append(("Instagram", "username" in r.json(), f"@{r.json().get('username', '?')}"))
except Exception as e:
    tests.append(("Instagram", False, str(e)[:60]))

# WhatsApp
try:
    r = requests.post(f"https://graph.facebook.com/v21.0/{CRED['WA_PH']}/messages",
        headers={"Authorization": f"Bearer {CRED['WA_TK']}", "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": WA_NUM, "type": "text",
              "text": {"body": "\U0001F916 v3 Cloud Edition deployed! \u2601\uFE0F Running 24/7 on GCP"}},
        timeout=10)
    tests.append(("WhatsApp", r.ok, "Sent!"))
except Exception as e:
    tests.append(("WhatsApp", False, str(e)[:60]))

for name, ok, detail in tests:
    print(f"  {'\u2705' if ok else '\u274C'} {name}: {detail}")

passed = sum(1 for _, ok, _ in tests if ok)

# Activate
r = requests.post(f"{base}/workflows/{wf_id}/activate", headers=hdr)
print(f"\n{'=' * 55}")
print(f"  \u2705 ACTIVATED: http://{VM_PUBLIC_IP}/workflow/{wf_id}")
print(f"{'=' * 55}")
print(f"  \U0001F680 v3 Cloud Edition \u2014 {passed}/{len(tests)} tests passed")
print(f"  Images: http://{VM_PUBLIC_IP}/images/")
print(f"  Dev.to: via helper proxy (no n8n body bug)")
print(f"  Schedule: every 12 hours, 24/7")
