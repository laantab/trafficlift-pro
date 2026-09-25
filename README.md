# TrafficLift Pro

> Autonomous AI Traffic Engine for Online Entrepreneurs.  
> Paste a product URL → get Pinterest SEO, TikTok scripts, and paid ad copy in seconds.

![Dashboard preview](https://img.shields.io/badge/status-v1.0.0--beta-indigo?style=flat-square)
![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square)
![FastAPI](https://img.shields.io/badge/fastapi-0.115-green?style=flat-square)

---

## What It Does

| Mode | What You Get |
|------|-------------|
| **Organic ($0)** | Pinterest SEO package (board title, pin title, optimized description, keywords) + TikTok/Reels/Shorts video script (Hook → Problem → Solution → CTA) |
| **Paid Ad Scale** | Meta Ads + TikTok Ads copy (headlines, primary text, CTAs) with audience suggestions and creative specs |

## Architecture

```
trafficlift-pro/
├── trafficlift_pro.py     # FastAPI app — entry point
├── backend/
│   ├── scrape.py          # URL scraper + metadata extraction
│   ├── generate.py        # AI content generation engine
│   ├── db.py              # SQLite campaign store (path via CAMPAIGNS_DB_PATH)
│   └── campaigns.db       # local dev DB (auto-created; not committed)
├── index.html             # Frontend dashboard (single-file, mobile-first)
├── requirements.txt
├── runtime.txt            # pins Python 3.11.9 for Render
├── Procfile               # web process: uvicorn trafficlift_pro:app …
├── render.yaml            # one-click Render Blueprint
├── .env.example
├── .gitignore
└── README.md
```

## Quick Start (local)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. (Optional) Add your OpenAI key for AI-powered generation

```bash
cp .env.example .env
# edit .env and set OPENAI_API_KEY=sk-your-key-here
```

> Without an OpenAI key the app runs in **Template Fallback Mode** — still fully functional with keyword-driven generation.

### 3. Start the backend server

```bash
python trafficlift_pro.py
# Server starts at http://localhost:8000
# API docs at http://localhost:8000/docs
```

### 4. Open the dashboard

```bash
# Easiest: open index.html directly in your browser.
# Then click the new "API" gear in the header and point it at
# http://localhost:8000 (or leave blank if you serve both from the same origin).
```

Or serve them together:

```bash
python -m http.server 3000 --directory .
# Then open http://localhost:3000  (the API is still at :8000)
```

---

## ☁️ Deploy to Render + GitHub

### One-time: push the project to GitHub

```bash
cd trafficlift-pro
git init
git add .
git commit -m "feat: ready for Render deploy"
gh repo create trafficlift-pro --public --source=. --remote=origin --push
```

(If you don't use `gh`, create the repo in the GitHub UI and then `git remote add origin …; git push -u origin main`.)

The repo root already includes:

- `Procfile` — `web: uvicorn trafficlift_pro:app --host 0.0.0.0 --port $PORT …`
- `render.yaml` — Render Blueprint with persistent disk + `RELOAD=false`
- `runtime.txt` — pins Python 3.11.9
- `.gitignore` — excludes `.env`, `*.db`, `__pycache__/`, plus your local-only files

### Two ways to deploy on Render

**Option A — Blueprint (recommended, one click):**

1. Render dashboard → **New** → **Blueprint**.
2. Connect the GitHub repo. Render will read `render.yaml` and provision:
   - Web service `trafficlift-pro` (Python, plan `starter`, persistent 1 GB disk at `/var/data`)
   - Environment variables with `OPENAI_API_KEY` & `MINIMAX_API_KEY` as `sync: false` so you set them in the UI.
3. Add your API keys under **Environment → Environment Variables** if you have them.
4. Click **Apply**. First deploy takes ~3 min; subsequent deploys ~45 s.

**Option B — Manual:**

1. Render dashboard → **New** → **Web Service** → connect repo.
2. Set **Runtime**: `Python 3`.
3. Set **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`.
4. Set **Start Command**: `uvicorn trafficlift_pro:app --host 0.0.0.0 --port $PORT --proxy-headers`.
5. Add a **Disk** at `/var/data`, 1 GB.
6. Add env vars: `CAMPAIGNS_DB_PATH=/var/data/campaigns.db`, `RELOAD=false`, plus `OPENAI_API_KEY` / `MINIMAX_API_KEY` if you have them.
7. **Save** → deploys in ~3 min.

### Pointing the mobile dashboard at the cloud URL

After Render gives you a URL like `https://trafficlift-pro.onrender.com`, there are three ways to wire up `index.html`:

1. **Hard-code in the file** (best for a real deploy):

   ```html
   <meta name="api-base" content="https://trafficlift-pro.onrender.com">
   ```

   Place this inside the `<head>` of `index.html` (already supported).

2. **URL query string** (good for one-shot testing on your phone):

   ```
   https://your-hosting.com/index.html?api=https://trafficlift-pro.onrender.com
   ```

   The value is saved to `localStorage` and the `?api=` is stripped from the URL.

3. **In-app Settings gear** (best for end users): open the page, tap the **API** gear in the header, paste the Render URL, tap **Save & Test**.

After saving, the header pill flips from "Offline" to "Online · openai/minimax/template_fallback" within a second.

### Where to host the frontend

The frontend is one HTML file — any static host works:

- **Same domain as the API** (cleanest, no CORS) — set up a reverse proxy route `/api/v1/* → http://localhost:8000/api/v1/*` on Render and leave the `<meta name="api-base">` blank.
- **GitHub Pages / Netlify / Cloudflare Pages / Vercel** — copy `index.html`, fill in the `<meta name="api-base">` with your Render URL, deploy.

For the simplest path on Render itself: add a second web service of type "Static Site" pointing at the same repo with `publishPath=.`.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | OpenAI API key (optional) |
| `OPENAI_MODEL` | `gpt-4o` | Model to use |
| `MINIMAX_API_KEY` | — | MiniMax API key (optional, video/avatar gen) |
| `MINIMAX_GROUP_ID` | — | MiniMax Group ID |
| `HOST` | `0.0.0.0` | Server bind host |
| `PORT` | `8000` | Server bind port (Render sets this automatically) |
| `RELOAD` | `true` | Auto-reload on code change — set `false` in prod |
| `LOG_LEVEL` | `info` | Logging verbosity |
| `CAMPAIGNS_DB_PATH` | `backend/campaigns.db` | Override SQLite path (Render persistent disk uses `/var/data/campaigns.db`) |

---

## Tech Stack

- **Backend**: FastAPI (Python 3.11+), Pydantic v2, requests, BeautifulSoup4, lxml
- **Frontend**: Single-file HTML5, Tailwind CSS (CDN), Lucide Icons (CDN)
- **AI**: OpenAI GPT-4o (optional) / keyword-template engine (built-in)
- **Storage**: SQLAlchemy + SQLite (local) / persistent disk on Render

---

## License

MIT — free to use, remix, and ship.

---

## API Reference

### `POST /api/v1/traffic/generate`

**Request body:**

```json
{
  "input_url": "https://shop.example.com/products/awesome-gadget",
  "mode": "organic",
  "target_channels": ["pinterest", "tiktok_organic"],
  "daily_budget": 25.0
}
```

**`mode`** — `organic` | `paid`

**`target_channels`** (organic):
- `pinterest` — Pinterest SEO package
- `tiktok_organic` — Short-form video script

**`target_channels`** (paid):
- `meta_ads` — Meta (Facebook/Instagram) ad copy
- `tiktok_ads` — TikTok Ads copy

**Response:**

```json
{
  "success": true,
  "input_url": "https://shop.example.com/products/awesome-gadget",
  "mode": "organic",
  "scraped_product": {
    "title": "...",
    "description": "...",
    "primary_image": "...",
    "price": "$29.99",
    "raw_keywords": ["..."]
  },
  "compiled_package": {
    "pinterest_seo_engine": { ... },
    "short_form_video_blueprint": { ... }
  },
  "meta": {
    "ai_mode": "template_fallback",
    "processing_time_ms": 847
  }
}
```

### `GET /api/v1/health`

Returns service status and whether OpenAI is connected.

---

## Features

### URL Scraping
- Open Graph + Twitter Card metadata
- Schema.org JSON-LD (Shopify, WooCommerce, Amazon)
- Fallback to HTML `<meta>` tags and page text
- Automatic keyword extraction from title + body
- Image gallery detection

### Organic Traffic Mode
- **Pinterest**: SEO-optimized board title, pin headline, keyword-rich description, recommended hashtags, 2:3 vertical spec
- **TikTok/Reels**: Pattern-interrupt hook, relatable problem block, solution reveal, strong CTA, hashtags

### Paid Ad Mode
- **Meta Ads**: 2 headline variations, primary text, description, CTA label
- **TikTok Ads**: Trendy hook-style headlines, comment-driving primary text, platform-native CTAs
- Audience targeting suggestions
- Campaign type recommendations
- Creative dimension specs per placement

### AI Modes
- **OpenAI** (GPT-4o): Creative, brand-consistent copy when `OPENAI_API_KEY` is set
- **Template Fallback**: Intelligent keyword-driven generation when no key is present

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | OpenAI API key (optional) |
| `OPENAI_MODEL` | `gpt-4o` | Model to use |
| `HOST` | `0.0.0.0` | Server bind host |
| `PORT` | `8000` | Server bind port |
| `RELOAD` | `true` | Auto-reload on code changes (dev only) |
| `LOG_LEVEL` | `info` | Logging verbosity |

---

## Tech Stack

- **Backend**: FastAPI (Python 3.11+), Pydantic v2, requests, BeautifulSoup4, lxml
- **Frontend**: Single-file HTML5, Tailwind CSS (CDN), Lucide Icons (CDN)
- **AI**: OpenAI GPT-4o (optional) / keyword-template engine (built-in)

---

## License

MIT — free to use, remix, and ship.
