# Bark.com AI Lead Agent — PoC

A proof-of-concept autonomous agent that discovers, scores, and crafts personalised
pitches for high-value service leads on Bark.com.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        BarkAgent Orchestrator                   │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐  ┌─────────────┐  │
│  │  BarkScraper     │   │  AIEvaluator     │  │  Output     │  │
│  │  (Playwright)    │──▶│  (Claude API)    │─▶│  JSON + Log │  │
│  │                  │   │                  │  │             │  │
│  │  • Login         │   │  • score_lead()  │  │  • Console  │  │
│  │  • Navigate      │   │  • gen_pitch()   │  │  • File     │  │
│  │  • Scrape cards  │   │  • ICP matching  │  │  • Dashboard│  │
│  └──────────────────┘   └──────────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Files

| File | Purpose |
|---|---|
| `bark_agent.py` | Main Python agent (Playwright + Claude API) |
| `dashboard.html` | Interactive browser dashboard (open directly) |
| `.env.example` | Environment variable template |
| `requirements.txt` | Python dependencies |

---

## Quick Start

### 1 — Dashboard (no install required)
Open `dashboard.html` in any browser.
- Works in **Demo Mode** without an API key (heuristic scoring).
- Add your Anthropic API key to enable real Claude scoring + pitch generation.

### 2 — Python Agent

```bash
# Install dependencies
pip install playwright anthropic python-dotenv rich
playwright install chromium

# Configure
cp .env.example .env
# Edit .env with your credentials

# Run in demo mode (no real Bark.com login)
python bark_agent.py

# Run against live Bark.com
# Set demo_mode=False in main() and provide real credentials
```

---

## Bot-Detection Mitigations

| Technique | Implementation |
|---|---|
| Randomised delays | `human_delay(min_ms, max_ms)` — uniform random sleep |
| Character-by-character typing | `human_type()` — 60–180ms per keystroke with micro-pauses |
| Curved mouse movement | `human_mouse_move()` — multi-waypoint path with jitter |
| Natural scrolling | `random_scroll()` — smooth scroll to random page depth |
| Browser fingerprint | `AutomationControlled` flag removed; real UA string |
| Viewport + locale | 1366×768, `en-US`, `America/New_York` timezone |

> **Note**: Bot detection is an arms race. These mitigations are representative
> starting points. Production use should add residential proxies, Canvas/WebGL
> fingerprint spoofing, and CAPTCHA-solving services.

---

## ICP Scoring

The LLM receives a detailed Ideal Customer Profile and returns structured JSON:

```json
{
  "score": 0.93,
  "budget_numeric": 25000,
  "tags": ["React/Next.js", "E-Commerce", "Headless CMS", "High Budget"],
  "rationale": "Strong fit: $25k budget, specific Next.js + Contentful stack, ..."
}
```

Leads scoring ≥ 0.8 (configurable) trigger pitch generation.

---

## Pitch Generation Rules

1. **3 paragraphs**: Hook → Capabilities → CTA
2. **≥ 2 specific details** from the lead description must be referenced
3. **180–240 words** — concise and impactful
4. Tone: confident, warm, consultative

---

## Ethical & Legal Notice

This PoC is for educational purposes. Before deploying against any live website:
- Review the site's **Terms of Service** and **robots.txt**
- Ensure compliance with applicable data protection laws (GDPR, CCPA, etc.)
- Obtain proper authorisation for automated access
- Respect rate limits and server resources
