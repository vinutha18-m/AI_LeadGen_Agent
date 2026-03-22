"""
Bark.com AI Lead Agent — PoC
============================
Automates discovery, scoring, and pitch generation for Bark.com leads.

Dependencies:
    pip install playwright anthropic python-dotenv rich
    playwright install chromium
"""

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import os

load_dotenv()

# ── Rich console for pretty output ────────────────────────────────────────────
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

console = Console()


# ══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Lead:
    id: str
    title: str
    description: str
    budget: str
    location: str
    posted_at: str
    url: str
    raw_html: str = ""

    # Set by AI evaluation
    score: float = 0.0
    score_rationale: str = ""
    budget_numeric: float = 0.0
    tags: list = None
    pitch: Optional[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


# ══════════════════════════════════════════════════════════════════════════════
# IDEAL CUSTOMER PROFILE  (edit to match your niche)
# ══════════════════════════════════════════════════════════════════════════════

IDEAL_CUSTOMER_PROFILE = """
You are evaluating Bark.com service leads for a premium web-development agency.

IDEAL CUSTOMER PROFILE:
- Project type  : Web development, SaaS platforms, e-commerce, mobile apps, API integrations
- Budget        : $2,000+ (hard minimum); $5,000–$50,000 is ideal
- Location      : Any English-speaking country (US, UK, CA, AU, NZ, IE)
- Indicators    : Mentions "launch", "MVP", "startup", "e-commerce", "rebuild", "redesign",
                  specific tech stacks (React, Next.js, Shopify, WooCommerce, Django, etc.)
- Red flags     : "cheap", "student", "simple one-page", <$500, unclear scope, no budget

SCORING RUBRIC (0.0 → 1.0):
  1.0  : Dream lead — high budget, clear scope, modern tech, urgent timeline
  0.9  : Excellent — good budget, specific requirements, quality project
  0.8  : Strong     — meets most criteria, worth pursuing
  0.6  : Average    — some signals but notable concerns
  0.4  : Below par  — budget or scope issues
  0.2  : Weak       — poor fit; significant red flags
  0.0  : Skip       — off-topic, spam, or zero budget
"""


# ══════════════════════════════════════════════════════════════════════════════
# HUMAN-LIKE BROWSER BEHAVIOUR HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def human_delay(min_ms: int = 800, max_ms: int = 2800) -> None:
    """Randomised sleep to mimic human reading/thinking time."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def human_type(page, selector: str, text: str) -> None:
    """Type character-by-character with variable inter-key delays."""
    await page.click(selector)
    await human_delay(200, 600)
    for char in text:
        await page.type(selector, char, delay=random.randint(60, 180))
        if random.random() < 0.05:          # occasional micro-pause
            await human_delay(300, 700)


async def human_mouse_move(page, x: int, y: int) -> None:
    """Curved mouse movement via intermediate waypoints."""
    current = await page.evaluate("() => ({x: window.innerWidth/2, y: window.innerHeight/2})")
    steps = random.randint(8, 20)
    for i in range(steps):
        t = (i + 1) / steps
        ix = current["x"] + (x - current["x"]) * t + random.randint(-15, 15)
        iy = current["y"] + (y - current["y"]) * t + random.randint(-15, 15)
        await page.mouse.move(ix, iy)
        await asyncio.sleep(random.uniform(0.01, 0.04))


async def random_scroll(page) -> None:
    """Simulate natural reading scroll."""
    height = await page.evaluate("() => document.body.scrollHeight")
    scroll_to = random.randint(200, min(height, 1200))
    await page.evaluate(f"window.scrollTo({{top: {scroll_to}, behavior: 'smooth'}})")
    await human_delay(600, 1400)


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPER
# ══════════════════════════════════════════════════════════════════════════════

class BarkScraper:
    """
    Playwright-based scraper for Bark.com.
    Handles login, navigation, and lead extraction.
    """

    LOGIN_URL    = "https://www.bark.com/en/us/login/"
    DASHBOARD_URL = "https://www.bark.com/en/us/professionals/dashboard/"
    REQUESTS_URL  = "https://www.bark.com/en/us/professionals/requests/"

    def __init__(self, email: str, password: str, headless: bool = False):
        self.email    = email
        self.password = password
        self.headless = headless
        self.browser  = None
        self.page     = None

    # ── Setup ──────────────────────────────────────────────────────────────

    async def start(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = await self.browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        # Inject stealth JS — hide webdriver flag
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)
        self.page = await ctx.new_page()
        console.log("[scraper] Browser started")

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    # ── Authentication ─────────────────────────────────────────────────────

    async def login(self) -> bool:
        console.log(f"[scraper] Navigating to login …")
        await self.page.goto(self.LOGIN_URL, wait_until="networkidle")
        await human_delay(1000, 2000)
        await random_scroll(self.page)

        # Accept cookies if banner appears
        try:
            cookie_btn = self.page.locator("button:has-text('Accept'), button:has-text('Accept All')")
            if await cookie_btn.count() > 0:
                await cookie_btn.first.click()
                await human_delay(500, 1000)
        except Exception:
            pass

        # Fill credentials
        await human_type(self.page, 'input[name="email"], input[type="email"]', self.email)
        await human_delay(400, 900)
        await human_type(self.page, 'input[name="password"], input[type="password"]', self.password)
        await human_delay(600, 1200)

        # Move mouse to submit button naturally, then click
        submit = self.page.locator('button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")')
        box = await submit.first.bounding_box()
        if box:
            await human_mouse_move(self.page, int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2))
        await submit.first.click()

        # Wait for redirect
        try:
            await self.page.wait_for_url("**/dashboard/**", timeout=15_000)
            console.log("[scraper] ✓ Login successful")
            return True
        except Exception:
            console.log("[scraper] ✗ Login failed — check credentials")
            return False

    # ── Lead Discovery ─────────────────────────────────────────────────────

    async def fetch_leads(self, max_leads: int = 20) -> list[Lead]:
        """Navigate to buyer requests and scrape lead cards."""
        console.log("[scraper] Navigating to buyer requests …")
        await self.page.goto(self.REQUESTS_URL, wait_until="networkidle")
        await human_delay(1500, 3000)
        await random_scroll(self.page)

        # Scroll to trigger lazy-load
        for _ in range(3):
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await human_delay(1200, 2200)

        leads = await self._parse_lead_cards(max_leads)
        console.log(f"[scraper] Found {len(leads)} lead(s)")
        return leads

    async def _parse_lead_cards(self, max_leads: int) -> list[Lead]:
        """
        Extract lead data from the DOM.
        Selectors are illustrative — update to match current Bark.com markup.
        """
        leads = []
        cards = await self.page.query_selector_all(
            ".request-card, .bark-request, [data-testid='request-item'], .lead-card"
        )

        for idx, card in enumerate(cards[:max_leads]):
            try:
                title = await self._text(card, "h2, h3, .request-title, .lead-title") or f"Lead #{idx+1}"
                desc  = await self._text(card, ".request-description, .lead-description, p") or ""
                budget = await self._text(card, ".budget, .price, [data-budget]") or "Not specified"
                loc   = await self._text(card, ".location, .lead-location, [data-location]") or "Unknown"
                href  = await card.get_attribute("data-href") or await self._href(card, "a") or ""
                post  = await self._text(card, ".posted-date, .time, time") or datetime.now().isoformat()
                uid   = re.sub(r"[^a-z0-9]", "_", title.lower())[:32] + f"_{idx}"

                leads.append(Lead(
                    id=uid, title=title, description=desc,
                    budget=budget, location=loc,
                    posted_at=post, url=href,
                ))
            except Exception as e:
                console.log(f"[scraper] Card parse error: {e}")

        return leads

    @staticmethod
    async def _text(parent, selector: str) -> Optional[str]:
        el = await parent.query_selector(selector)
        return (await el.inner_text()).strip() if el else None

    @staticmethod
    async def _href(parent, selector: str) -> Optional[str]:
        el = await parent.query_selector(selector)
        return await el.get_attribute("href") if el else None


# ══════════════════════════════════════════════════════════════════════════════
# AI EVALUATOR  (Anthropic Claude)
# ══════════════════════════════════════════════════════════════════════════════

class AIEvaluator:
    """Uses Claude to score leads and generate pitches."""

    def __init__(self, api_key: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model  = "anthropic/claude-opus-4.5"

    # ── Lead Scoring ───────────────────────────────────────────────────────

    async def score_lead(self, lead: Lead) -> Lead:
        prompt = f"""
{IDEAL_CUSTOMER_PROFILE}

---
LEAD TO EVALUATE:
Title       : {lead.title}
Description : {lead.description}
Budget      : {lead.budget}
Location    : {lead.location}
---

Return ONLY a valid JSON object (no markdown fences) with these exact keys:
{{
  "score": <float 0.0–1.0>,
  "budget_numeric": <estimated USD number or 0>,
  "tags": [<up to 5 short tag strings>],
  "rationale": "<2–3 sentence explanation of the score>"
}}
"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip accidental markdown fences
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip("` \n")

        try:
            data = json.loads(raw)
            lead.score          = float(data.get("score", 0.0))
            lead.budget_numeric = float(data.get("budget_numeric", 0))
            lead.tags           = data.get("tags", [])
            lead.score_rationale = data.get("rationale", "")
        except json.JSONDecodeError:
            console.log(f"[ai] JSON parse error for lead '{lead.title}': {raw[:120]}")

        return lead

    # ── Pitch Generation ───────────────────────────────────────────────────

    async def generate_pitch(self, lead: Lead) -> str:
        prompt = f"""
You are a senior business development rep at a premium web-development agency.

Write a personalized 3-paragraph pitch for the following Bark.com lead.

RULES:
1. Reference at LEAST two specific details from the lead description to prove you read it.
2. Paragraph 1 — Hook + immediate empathy with their stated problem/goal.
3. Paragraph 2 — Our relevant capabilities + a concrete result/case study teaser.
4. Paragraph 3 — Clear, low-friction next step (15-min call, free audit, etc.).
5. Tone: confident, warm, consultative — NOT salesy or generic.
6. Length: 180–240 words total.

LEAD:
Title       : {lead.title}
Description : {lead.description}
Budget      : {lead.budget}
Location    : {lead.location}
Score       : {lead.score:.2f}
Tags        : {', '.join(lead.tags)}

Write ONLY the pitch text. No preamble, no labels.
"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


# ══════════════════════════════════════════════════════════════════════════════
# DEMO MODE  (no real Bark.com login required for PoC testing)
# ══════════════════════════════════════════════════════════════════════════════

DEMO_LEADS = [
    Lead(
        id="lead_001", title="E-Commerce Platform Rebuild — Shopify to Custom",
        description=(
            "We run a mid-size outdoor gear brand doing $3M/yr. Our Shopify store has "
            "hit its ceiling — we need a custom React/Next.js storefront with headless "
            "CMS (Contentful), custom checkout, and Stripe integration. Timeline is "
            "3 months. Budget approved up to $25,000. We need someone who's done "
            "this before and can show comparable work."
        ),
        budget="$25,000", location="Denver, CO", posted_at="2 hours ago",
        url="https://bark.com/request/001",
    ),
    Lead(
        id="lead_002", title="Logo Design for Bakery",
        description=(
            "Small family bakery looking for a simple logo. Nothing fancy. "
            "We have a tight budget — ideally under $150."
        ),
        budget="$150", location="Springfield, IL", posted_at="5 hours ago",
        url="https://bark.com/request/002",
    ),
    Lead(
        id="lead_003", title="SaaS MVP — AI-Powered HR Onboarding Tool",
        description=(
            "Pre-seed startup building an AI-driven employee onboarding platform. "
            "Need a full-stack engineer (Python/FastAPI backend, React frontend). "
            "Core features: automated document collection, e-sign, Slack integration, "
            "and an LLM-powered Q&A bot. Seed round closes in 6 weeks; we need MVP "
            "ready to demo to investors. Budget is $18,000–$30,000."
        ),
        budget="$18,000–$30,000", location="San Francisco, CA", posted_at="1 hour ago",
        url="https://bark.com/request/003",
    ),
    Lead(
        id="lead_004", title="WordPress Blog Setup",
        description="Need someone to install WordPress and pick a theme. Very basic.",
        budget="$200", location="Austin, TX", posted_at="3 hours ago",
        url="https://bark.com/request/004",
    ),
    Lead(
        id="lead_005", title="Multi-Vendor Marketplace — Real Estate Tech",
        description=(
            "PropTech startup building a marketplace connecting landlords with "
            "verified contractors. Needs: user auth (OAuth2), listing/search with "
            "Algolia, escrow payment flow (Stripe Connect), mobile-responsive. "
            "We have Figma designs ready. Budget: $40,000. Start ASAP."
        ),
        budget="$40,000", location="New York, NY", posted_at="30 min ago",
        url="https://bark.com/request/005",
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

class BarkAgent:
    """Top-level orchestrator tying scraper + AI evaluator together."""

    SCORE_THRESHOLD = 0.8

    def __init__(
        self,
        email: str,
        password: str,
        anthropic_api_key: str,
        demo_mode: bool = True,
        headless: bool = False,
    ):
        self.email    = email
        self.password = password
        self.demo_mode = demo_mode
        self.scraper  = BarkScraper(email, password, headless)
        self.ai       = AIEvaluator(anthropic_api_key)
        self.results  : list[Lead] = []

    async def run(self, max_leads: int = 10) -> list[Lead]:
        console.rule("[bold cyan]Bark.com AI Agent")

        # 1 — Acquire leads
        if self.demo_mode:
            console.log("[agent] Demo mode — using synthetic leads")
            leads = DEMO_LEADS[:max_leads]
        else:
            await self.scraper.start()
            try:
                ok = await self.scraper.login()
                if not ok:
                    return []
                leads = await self.scraper.fetch_leads(max_leads)
            finally:
                await self.scraper.stop()

        # 2 — Score each lead
        console.log(f"[agent] Scoring {len(leads)} leads {'with AI' if not self.demo_mode else 'synthetically'} …")
        scored = []
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as prog:
            task = prog.add_task("Evaluating …", total=len(leads))
            for lead in leads:
                prog.update(task, description=f"Scoring: {lead.title[:50]}")
                if self.demo_mode:
                    lead.score = round(random.uniform(0.3, 0.9), 2)
                    lead.budget_numeric = 5000
                    lead.tags = ["synthetic", "demo"]
                    lead.rationale = "Synthetic score for demo purposes."
                else:
                    lead = await self.ai.score_lead(lead)
                scored.append(lead)
                prog.advance(task)
                await asyncio.sleep(0.3)   # rate-limit courtesy

        # 3 — Generate pitches for high-scoring leads
        hot_leads = [l for l in scored if l.score >= self.SCORE_THRESHOLD]
        console.log(f"[agent] Generating pitches for {len(hot_leads)} hot lead(s) (score ≥ {self.SCORE_THRESHOLD})")
        for lead in hot_leads:
            if self.demo_mode:
                lead.pitch = "Thank you for your interest! In demo mode, we'd prepare a tailored pitch highlighting our expertise in web development, including successful projects with similar scopes and budgets. Let's schedule a quick 15-minute call to discuss your needs."
            else:
                lead.pitch = await self.ai.generate_pitch(lead)
            await asyncio.sleep(0.3)

        # Sort by score descending
        scored.sort(key=lambda l: l.score, reverse=True)
        self.results = scored

        # 4 — Display summary
        self._print_summary(scored)
        self._save_results(scored)
        return scored

    # ── Console Output ─────────────────────────────────────────────────────

    def _print_summary(self, leads: list[Lead]) -> None:
        table = Table(title="Lead Evaluation Results", show_lines=True)
        table.add_column("Score", style="bold", width=7)
        table.add_column("Title", style="cyan", width=42)
        table.add_column("Budget", width=18)
        table.add_column("Location", width=18)
        table.add_column("Tags", width=30)

        for l in leads:
            color = "green" if l.score >= 0.8 else ("yellow" if l.score >= 0.5 else "red")
            table.add_row(
                f"[{color}]{l.score:.2f}[/{color}]",
                l.title[:42],
                l.budget[:18],
                l.location[:18],
                ", ".join(l.tags[:3]),
            )
        console.print(table)

        for l in leads:
            if l.pitch:
                console.print(Panel(
                    l.pitch,
                    title=f"[bold green]✦ PITCH — {l.title[:60]}[/bold green]  [dim]score={l.score:.2f}[/dim]",
                    border_style="green",
                    padding=(1, 2),
                ))

    # ── Persistence ────────────────────────────────────────────────────────

    def _save_results(self, leads: list[Lead]) -> None:
        out_dir = Path("bark_agent_output")
        out_dir.mkdir(exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"results_{ts}.json"

        payload = [asdict(l) for l in leads]
        out.write_text(json.dumps(payload, indent=2))
        console.log(f"[agent] Results saved → {out}")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    agent = BarkAgent(
        email    = os.getenv("BARK_EMAIL", ""),
        password = os.getenv("BARK_PASSWORD", ""),
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", ""),
        demo_mode = True,       # ← set False to run against real Bark.com
        headless  = False,
    )
    await agent.run(max_leads=5)


if __name__ == "__main__":
    asyncio.run(main())
