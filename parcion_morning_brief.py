#!/usr/bin/env python3
"""
The Parcion Morning Brief
Daily intelligence briefing for Parcion Private Wealth advisors.
Fetches news via curated RSS feeds, market data via Google Finance + FRED + CME FedWatch.
Synthesizes with Claude, sends via Gmail.
"""

import feedparser
import smtplib
import os
import urllib.parse
import re
import requests
import markdown
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date
import anthropic


# ─── Configuration ─────────────────────────────────────────────────────────────
GMAIL_ADDRESS      = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
WORK_EMAIL         = os.environ['WORK_EMAIL']
ANTHROPIC_API_KEY  = os.environ['ANTHROPIC_API_KEY']
FRED_API_KEY       = os.environ['FRED_API_KEY']

IS_MONDAY = datetime.now().weekday() == 0


# ─── News Sources ──────────────────────────────────────────────────────────────
DIRECT_FEEDS = {

    "notable_events": [
        "https://feeds.reuters.com/reuters/topNews",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    ],

    "tax_estate": [
        "https://taxpolicycenter.org/taxvox/feed",
        "https://www.journalofaccountancy.com/rss/all-content.xml",
        "https://www.kiplinger.com/feed/rss",
        "https://www.irs.gov/rss-feeds/irs-news-releases",
        "https://www.sec.gov/rss/news/pressreleases.rss",
        "https://www.govtrack.us/events/events.rss?feeds=misc:allvotes",
        "https://app.leg.wa.gov/RSS/BillSummary.aspx",
    ],

    "markets_investing": [
        "https://finance.yahoo.com/rss/topstories",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://www.morningstar.com/rss/rss.xml",
        "https://rpc.cfainstitute.org/feed",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.themiddlemarket.com/feed",
        "https://www.institutionalinvestor.com/rss/articles.aspx",
        "https://techcrunch.com/category/venture/feed/",
        "https://www.globest.com/feed/",
        "https://feeds.reuters.com/reuters/commoditiesNews",
        "https://www.kitco.com/rss/kitconews.rss",
        "https://warontherocks.com/feed/",
        "https://www.foreignaffairs.com/rss.xml",
    ],

    "ma_business": [
        "https://www.themiddlemarket.com/feed",
        "https://hbr.org/feed/topic/mergers-and-acquisitions",
        "https://pitchbook.com/rss/news",
        "https://axios.com/feeds/feed.rss",
    ],

    "family_office": [
        "https://www.thinkadvisor.com/feed/",
        "https://www.wealthmanagement.com/rss.xml",
        "https://www.investmentnews.com/feed",
        "https://citywire.com/ria/rss",
    ],
}

FALLBACK_QUERIES = {
    "notable_events":    "top news today business economy politics",
    "tax_estate":        "estate planning tax legislation IRS gift trust wealth transfer",
    "markets_investing": "stock market investing Fed rates private equity real estate commodities macro",
    "ma_business":       "mergers acquisitions business sale M&A private equity deal",
    "family_office":     "wealth management RIA family office fiduciary UHNW",
}

LEGISLATION_STATE_QUERIES = [
    "Texas tax legislation business owners 2026",
    "Washington state tax legislation wealth 2026",
    "Oregon tax legislation estate planning 2026",
    "California tax legislation business owners 2026",
    "Arizona tax legislation wealth planning 2026",
]


# ─── Market Data ───────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Yahoo Finance tickers: (display_name, yahoo_symbol)
MARKET_TICKERS = [
    ("S&P 500",       "^GSPC"),
    ("Nasdaq",        "^IXIC"),
    ("Dow Jones",     "^DJI"),
    ("VIX",           "^VIX"),
    ("10Y Treasury",  "^TNX"),
    ("2Y Treasury",   "^IRX"),
    ("Gold (spot)",   "GC=F"),
    ("WTI Crude",     "CL=F"),
    ("DXY (Dollar)",  "DX-Y.NYB"),
]

# FRED series: (display_name, series_id, format_fn, yoy_calc)
# yoy_calc=True means fetch 13 months and calculate % change vs year ago
def pct(v):   return f"{v:.1f}%"
def rate(v):  return f"{v:.2f}%"
def idx(v):   return f"{v:.1f}"

# (display_name, series_id, format_fn, units, historical_context)
# units: "pc1" = percent change from year ago, "lin" = raw level
# historical_context: short benchmark so readers know if reading is hot/cold/normal
FRED_SERIES = [
    ("CPI YoY",          "CPIAUCSL",          pct,  "pc1",  "Target: 2.0%"),
    ("Core CPI YoY",     "CPILFESL",          pct,  "pc1",  "Target: 2.0%"),
    ("PCE YoY",          "PCEPI",             pct,  "pc1",  "Fed target: 2.0%"),
    ("Core PCE YoY",     "PCEPILFE",          pct,  "pc1",  "Fed target: 2.0%"),
    ("Unemployment",     "UNRATE",            pct,  "lin",  "Avg (2015-19): 4.4%"),
    ("GDP Growth",       "A191RL1Q225SBEA",   pct,  "lin",  "Long-run avg: ~2.5%"),
    ("UMich Sentiment",  "UMCSENT",           idx,  "lin",  "Avg (2015-19): 96.5"),
    ("Fed Funds Rate",   "FEDFUNDS",          rate, "lin",  None),  # benchmark set dynamically
]


def get_yahoo_session():
    """Get a valid Yahoo Finance session with cookies."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        # Hit the main page to get cookies
        session.get("https://finance.yahoo.com", timeout=10)
    except Exception:
        pass
    return session

_yahoo_session = None

def fetch_yahoo_quote(symbol):
    """Fetch current price and YTD change using Yahoo Finance v8 chart API."""
    global _yahoo_session
    if _yahoo_session is None:
        _yahoo_session = get_yahoo_session()
    try:
        # Use v8 chart endpoint with 1y range to get YTD data
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        params = {
            "interval":  "1d",
            "range":     "1y",
            "includePrePost": "false",
        }
        r = _yahoo_session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data   = r.json()
        result = data["chart"]["result"][0]
        meta   = result["meta"]

        current_price = (
            meta.get("regularMarketPrice") or
            meta.get("previousClose") or
            result["indicators"]["quote"][0]["close"][-1]
        )

        closes     = result["indicators"]["quote"][0]["close"]
        timestamps = result["timestamp"]

        # Find first non-null close in current calendar year
        current_year = datetime.now().year
        jan_close = None
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            dt = datetime.utcfromtimestamp(ts)
            if dt.year == current_year:
                jan_close = close
                break

        ytd_pct = None
        if jan_close and jan_close != 0 and current_price:
            ytd_pct = ((current_price - jan_close) / jan_close) * 100

        return current_price, ytd_pct

    except Exception as e:
        print(f"    ✗ Yahoo quote failed ({symbol}): {e}")
        return None, None


def format_market_value(name, price):
    """Format price based on asset type."""
    if price is None:
        return "N/A"
    if "Treasury" in name or "VIX" in name or "DXY" in name:
        return f"{price:.2f}"
    if "Gold" in name:
        return f"${price:,.2f}"
    if "Crude" in name:
        return f"${price:.2f}"
    if price > 1000:
        return f"{price:,.2f}"
    return f"{price:.2f}"


def fetch_market_data():
    """Fetch YTD market data for all tickers."""
    print("  → Fetching market data (Yahoo Finance)...")
    results = []
    for name, symbol in MARKET_TICKERS:
        price, ytd = fetch_yahoo_quote(symbol)
        results.append({
            "name":  name,
            "value": format_market_value(name, price),
            "ytd":   ytd,
        })
        print(f"    {'✓' if price else '✗'} {name}: {format_market_value(name, price)}")
    return results


def fetch_fred_series(series_id, units="lin"):
    """Fetch the most recent observation for a FRED series."""
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id":       series_id,
            "api_key":         FRED_API_KEY,
            "file_type":       "json",
            "sort_order":      "desc",
            "limit":           "2",
            "observation_end": date.today().isoformat(),
            "units":           units,
        }
        r = requests.get(url, params=params, timeout=10)
        obs = r.json()["observations"]
        for o in obs:
            if o["value"] not in (".", ""):
                val   = float(o["value"])
                dt    = datetime.strptime(o["date"], "%Y-%m-%d")
                as_of = dt.strftime("%b %Y") if dt.day == 1 else dt.strftime("%b %d, %Y")
                return val, as_of
        return None, None
    except Exception as e:
        print(f"    ✗ FRED {series_id}: {e}")
        return None, None


def fetch_fed_15yr_avg():
    """Fetch 15-year average of Fed Funds Rate from FRED."""
    try:
        fifteen_yrs_ago = (datetime.now().replace(year=datetime.now().year - 15)).strftime("%Y-%m-%d")
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id":        "FEDFUNDS",
            "api_key":          FRED_API_KEY,
            "file_type":        "json",
            "observation_start": fifteen_yrs_ago,
            "observation_end":  date.today().isoformat(),
            "units":            "lin",
        }
        r   = requests.get(url, params=params, timeout=10)
        obs = r.json()["observations"]
        vals = [float(o["value"]) for o in obs if o["value"] not in (".", "")]
        if vals:
            avg = sum(vals) / len(vals)
            return f"15yr avg: {avg:.1f}%"
        return None
    except Exception:
        return None


def fetch_economic_data():
    """Fetch all FRED economic indicators."""
    print("  → Fetching economic data (FRED)...")

    # Get 15-year Fed Funds average for benchmark
    fed_15yr = fetch_fed_15yr_avg()

    results = []
    for name, series_id, fmt, units, context in FRED_SERIES:
        val, as_of = fetch_fred_series(series_id, units=units)
        formatted  = fmt(val) if val is not None else "N/A"
        # Override Fed Funds benchmark with dynamic 15yr average
        if name == "Fed Funds Rate" and fed_15yr:
            context = fed_15yr
        results.append({
            "name":    name,
            "value":   formatted,
            "as_of":   as_of or "N/A",
            "context": context or "",
        })
        print(f"    {'✓' if val else '✗'} {name}: {formatted}")
    return results





def fetch_fed_expectations():
    """
    Returns next FOMC meeting date and year-end implied Fed Funds rate
    derived from 6-month T-bill (DTB6) as a year-end proxy.
    No probability tiles — avoids the logic contradiction.
    """
    print("  → Fetching Fed expectations (FRED)...")
    try:
        curr_val, _ = fetch_fred_series("FEDFUNDS")
        ye_tbill, _ = fetch_fred_series("DTB6")

        if curr_val is None:
            raise ValueError("No current Fed Funds rate")

        # Next FOMC meeting
        today = datetime.now()
        fomc_2026 = [
            datetime(2026, 1, 29), datetime(2026, 3, 19), datetime(2026, 4, 29),
            datetime(2026, 5, 7),  datetime(2026, 6, 18), datetime(2026, 7, 30),
            datetime(2026, 9, 17), datetime(2026, 10, 29), datetime(2026, 12, 10),
        ]
        next_meeting = next((d for d in fomc_2026 if d > today), None)
        meeting_str  = next_meeting.strftime("%B %d, %Y") if next_meeting else "Next Meeting"

        # Year-end implied rate from 6M T-bill
        if ye_tbill:
            yr_end = f"{ye_tbill:.2f}%"
            diff   = ye_tbill - curr_val
            if diff <= -0.375:
                direction = f"{abs(round(diff/0.25)):.0f} cuts implied"
            elif diff >= 0.375:
                direction = f"{round(diff/0.25):.0f} hikes implied"
            elif abs(diff) < 0.125:
                direction = "Hold implied"
            elif diff < 0:
                direction = "1 cut implied"
            else:
                direction = "1 hike implied"
        else:
            yr_end    = f"{curr_val:.2f}%"
            direction = "Hold implied"

        return {
            "meeting":   meeting_str,
            "yr_end":    yr_end,
            "direction": direction,
            "curr_rate": f"{curr_val:.2f}%",
            "source":    "FRED / 6M T-Bill",
        }

    except Exception as e:
        print(f"    ✗ Fed expectations failed: {e}")
        return None


# ─── News Helpers ──────────────────────────────────────────────────────────────
def strip_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()


def fetch_from_feed(url, max_articles=5):
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_articles]:
            title   = strip_html(entry.get('title', 'No title'))
            summary = strip_html(entry.get('summary', entry.get('description', '')))
            summary = summary[:400] if summary else 'No summary available.'
            link    = entry.get('link', '')
            if title and link:
                articles.append({"title": title, "summary": summary, "link": link})
        return articles
    except Exception as e:
        print(f"    ✗ Feed failed ({url[:60]}): {e}")
        return []


def fetch_news_google(query, max_articles=5):
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    return fetch_from_feed(url, max_articles)


def compile_articles():
    all_articles = {}

    for topic, feeds in DIRECT_FEEDS.items():
        print(f"  → {topic}")
        articles = []
        seen = set()

        for feed_url in feeds:
            results = fetch_from_feed(feed_url, max_articles=3)
            for a in results:
                if a['title'] not in seen:
                    seen.add(a['title'])
                    articles.append(a)

        if len(articles) < 2 and topic in FALLBACK_QUERIES:
            print(f"    ↩ Falling back to Google News")
            fallback = fetch_news_google(FALLBACK_QUERIES[topic])
            for a in fallback:
                if a['title'] not in seen:
                    seen.add(a['title'])
                    articles.append(a)

        all_articles[topic] = articles[:8]

    # Supplement tax_estate with state legislation queries
    print("  → tax_estate (state legislation supplements)")
    tax_seen = set(a['title'] for a in all_articles.get('tax_estate', []))
    for q in LEGISLATION_STATE_QUERIES:
        results = fetch_news_google(q, max_articles=2)
        for a in results:
            if a['title'] not in tax_seen:
                tax_seen.add(a['title'])
                all_articles['tax_estate'].append(a)
    all_articles['tax_estate'] = all_articles['tax_estate'][:8]

    return all_articles


# ─── Build Claude Prompt ───────────────────────────────────────────────────────
def build_prompt(articles_by_topic):
    today = datetime.now().strftime('%A, %B %d, %Y')

    article_text = ""
    counter = 1
    for topic, articles in articles_by_topic.items():
        article_text += f"\n\n### SOURCE: {topic.upper().replace('_', ' ')}\n"
        if not articles:
            article_text += "_No articles retrieved._\n"
            continue
        for a in articles:
            article_text += (
                f"[ART-{counter:03d}] {a['title']}\n"
                f"   {a['summary']}\n"
                f"   URL: {a['link']}\n\n"
            )
            counter += 1

    monday_section = ""
    if IS_MONDAY:
        monday_section = """
---

## Growing Your Network

3-4 short, punchy ideas for relationship-building this week. Audience is family office advisors — relationship builders, not product salespeople. COIs include CPAs, estate attorneys, M&A lawyers, and investment bankers. Ideas should feel natural and human, not scripted. If a creative AI-assisted outreach angle is genuinely useful, include it.

- [Idea 1]
- [Idea 2]
- [Idea 3]
- [Idea 4 if truly warranted]

"""

    return f"""You are the editorial intelligence behind "The Parcion Morning Brief" — a daily internal briefing for the 8 advisors at Parcion Private Wealth, a nationally recognized independent private family office serving business owners and UHNW families through pre-liquidity, liquidity, and post-liquidity wealth events.

Today is {today}.

AUDIENCE: Experienced wealth advisors. Assume strong financial literacy. Accessible enough for a newer advisor.

GOAL: A tight, high-signal briefing readable in 5-7 minutes over coffee. Target 700 words of editorial content maximum. Every section earns its place. When in doubt, cut it entirely.

DEDUPLICATION RULE: If two or more articles cover the same story or angle, pick the single strongest one and drop the rest entirely. Do not cover the same topic from multiple angles across the brief.

CATEGORIZATION RULE: Place each article in the single most relevant section. A Federal Reserve article goes in Markets & Economic. A Powell speech goes in Markets & Economic. An IRS notice goes in Tax & Estate Planning. Do not let general financial news bleed into wrong sections.

VOICE (non-negotiable):
- CIO memo tone. Calm, confident, analytically precise.
- Never refer to any advisor by name. Use "advisors may want to consider..." or "worth raising with clients who..."
- No investment recommendations. Observations on asset classes, macro, and sectors only. No single stock or fund picks.
- No alarming language: never "crash," "collapse," or "unprecedented." Anchor uncertainty in historical context.
- Approved framing for guarded optimism: "constructive but cautious."
- Bifurcated economy shorthand: "the K economy."
- Use "wealth event" or "liquidity event" — not just "transaction."
- Use "families" or "business owners" — not "high-net-worth individuals" or "HNWIs."
- No filler phrases whatsoever.
- No em dashes. Use commas or short sentence breaks instead.
- Oxford comma always.
- Links appear ONCE per article. Format: [Link](URL) using the word "Link" as anchor text.
- No guaranteed outcomes language.

FORMAT RULES:
- Do NOT include a title or date at the top — the email header handles this.
- No Summary field anywhere. Only Punchline and Relevance.
- Punchline: 2 sentences max. Hard cap.
- Relevance: 2 short sentences max. Hard cap.
- Blank line between Punchline and Relevance within an item.
- Separate each item within a section with a --- divider.
- Separate each Conversation Starter with a --- divider.
- Conversation Starters: each sub-bullet on its own line with a blank line between each field.

---

Here are today's source articles by feed category. Use topic relevance — not feed source — to decide which brief section each article belongs in:
{article_text}

---

Produce the briefing using EXACTLY this structure. Skip any section with no strong material.

---

## Notable Events

5 items. Format for each: bold lead-in of 3-5 words, then a dash, then one informative sentence that tells the reader what happened and why it matters without needing to click. Link at the end.

**Bold Lead-In —** Full informative sentence explaining what happened and why it matters. ([Link](URL))

[Repeat for all 5 items]

---

## Tax and Estate Planning

Includes legislation updates, IRS guidance, SEC rules, state and federal tax law changes, estate planning strategy, and trust-related news. 1-3 items max. Skip if nothing strong today.

For each item:

**[Headline]** ([Link](URL))

**Punchline:** [2 sentences max.]

**Relevance:** [2 short sentences max. Why a family office advisor serving business owners should care.]

---

## Markets and Economic

All investing and macro content: equity markets, fixed income, Fed policy, private equity, private credit, venture capital, real estate, commodities, precious metals, geopolitics with economic implications. 1-3 items max. Skip if nothing strong today. Do NOT include tax, estate, M&A, or family office content here.

For each item:

**[Headline]** ([Link](URL))

**Punchline:** [2 sentences max.]

**Relevance:** [2 short sentences max.]

---

## M&A and Business

Business sales, mergers, acquisitions, deal market conditions, exit planning news, private equity deal flow. 1-2 items max. Skip if nothing strong today.

For each item:

**[Headline]** ([Link](URL))

**Punchline:** [2 sentences max.]

**Relevance:** [2 short sentences max.]

---

## Family Office

Wealth management industry news, RIA trends, family office operations, fiduciary topics, advisor practice management. 1-2 items max. Skip if nothing strong today.

For each item:

**[Headline]** ([Link](URL))

**Punchline:** [2 sentences max.]

**Relevance:** [2 short sentences max.]

---

## Conversation Starters

Only include if today's news produced 2-3 genuinely strong, timely angles. If the material isn't there, omit entirely. Do not force this section.

Separate each conversation starter with a --- divider.

---

**[Topic — 4 words max]**

**Angle:** [One natural sentence an advisor could say to open the conversation.]

**Why now:** [What in today's news makes this timely.] ([Link](URL))

**Who:** [Specific client type and situation.]

**Where it goes:** [Named technique or next step: GRAT, IDGT, SLAT, DAF, installment sale, private credit allocation, etc.]

---

[Repeat for each additional starter, always separated by ---]

---
{monday_section}
## Optional Reads

1-2 only. Substantive long-form reads — white papers, research, opinion pieces. Free access only. Up to 3 years old if still relevant. Skip if nothing qualifies.

**[Title]** ([Link](URL))
[2 sentences max: what it covers and why worth the time.]

---"""


# ─── Claude API Call ───────────────────────────────────────────────────────────
def synthesize_with_claude(articles_by_topic):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = build_prompt(articles_by_topic)
    print(f"      Prompt: {len(prompt):,} chars")

    full_text = ""
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            full_text += text

    return full_text


# ─── Market Data HTML Block ────────────────────────────────────────────────────
def build_market_html(market_data, econ_data, fed_data):
    """Build the full Markets & Economic Snapshot HTML block."""

    def ytd_cell(ytd):
        if ytd is None:
            return '<td style="padding:6px 10px;text-align:right;color:#888;font-size:12px;">N/A</td>'
        color = "#2a7a3b" if ytd >= 0 else "#c0392b"
        sign  = "+" if ytd >= 0 else ""
        return f'<td style="padding:6px 10px;text-align:right;color:{color};font-weight:700;font-size:13px;">{sign}{ytd:.1f}%</td>'

    # Market rows
    market_rows = ""
    for i, m in enumerate(market_data):
        bg = "#ffffff" if i % 2 == 0 else "#f7f7f4"
        market_rows += f"""
        <tr style="background:{bg};">
          <td style="padding:6px 10px;color:#1a1a1a;font-size:13px;">{m['name']}</td>
          <td style="padding:6px 10px;text-align:right;color:#1a1a1a;font-size:13px;">{m['value']}</td>
          {ytd_cell(m['ytd'])}
        </tr>"""

    # Economic rows
    econ_rows = ""
    for i, e in enumerate(econ_data):
        bg = "#ffffff" if i % 2 == 0 else "#f7f7f4"
        bold = "font-weight:700;" if "Fed" in e['name'] else ""
        econ_rows += f"""
        <tr style="background:{bg};">
          <td style="padding:6px 8px;color:#1a1a1a;font-size:12px;">{e['name']}</td>
          <td style="padding:6px 8px;text-align:right;color:#1a1a1a;font-size:12px;{bold}">{e['value']}</td>
          <td style="padding:6px 8px;text-align:right;color:#888;font-size:10px;">{e['as_of']}</td>
          <td style="padding:6px 8px;text-align:right;color:#7a8f96;font-size:10px;font-style:italic;">{e.get('context','')}</td>
        </tr>"""

    # Fed expectations block
    if fed_data:
        meeting   = fed_data.get("meeting",   "Next Meeting")
        yr_end    = fed_data.get("yr_end",    "N/A")
        direction = fed_data.get("direction", "")
        curr_rate = fed_data.get("curr_rate", "N/A")
        source    = fed_data.get("source",    "FRED")

        fed_block = f"""
      <div style="border-top:1px solid #e8e5dd;padding-top:14px;margin-top:12px;">
        <div style="font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#B99A38;margin-bottom:10px;">Fed Policy Outlook</div>
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
          <tr style="background:#f7f7f4;">
            <td style="padding:8px 10px;font-family:'Century Gothic',Arial,sans-serif;font-size:10px;color:#555;">Next Meeting</td>
            <td style="padding:8px 10px;text-align:right;font-family:'Century Gothic',Arial,sans-serif;font-size:11px;font-weight:700;color:#00141C;">{meeting}</td>
          </tr>
          <tr style="background:#ffffff;">
            <td style="padding:8px 10px;font-family:'Century Gothic',Arial,sans-serif;font-size:10px;color:#555;">Current Rate</td>
            <td style="padding:8px 10px;text-align:right;font-family:'Century Gothic',Arial,sans-serif;font-size:11px;font-weight:700;color:#00141C;">{curr_rate}</td>
          </tr>
          <tr style="background:#f7f7f4;">
            <td style="padding:8px 10px;font-family:'Century Gothic',Arial,sans-serif;font-size:10px;color:#555;">Year-End Implied (6M T-Bill)</td>
            <td style="padding:8px 10px;text-align:right;font-family:'Century Gothic',Arial,sans-serif;font-size:11px;font-weight:700;color:#00141C;">{yr_end} &nbsp;<span style="font-size:10px;color:#B99A38;font-weight:400;">{direction}</span></td>
          </tr>
        </table>
        <div style="font-size:10px;color:#999;margin-top:6px;font-style:italic;font-family:'Century Gothic',Arial,sans-serif;">Source: {source}. Year-end rate implied by 6-month Treasury bill yield.</div>
      </div>"""
    else:
        fed_block = ""

    return f"""
  <div style="margin-bottom:28px;">
    <div style="font-family:'Century Gothic',Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#00141C;border-bottom:1px solid #B99A38;padding-bottom:6px;margin-bottom:16px;">Markets &amp; Economic Snapshot</div>

    <!-- Outlook-safe two-column table layout -->
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="table-layout:fixed;">
      <tr>
        <td width="49%" valign="top" style="padding-right:12px;">
          <div style="font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#B99A38;margin-bottom:8px;">Markets — YTD Change</div>
          <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
            <thead>
              <tr style="background:#00141C;">
                <th style="text-align:left;padding:6px 8px;font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#B99A38;">Index / Asset</th>
                <th style="text-align:right;padding:6px 8px;font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#B99A38;">Value</th>
                <th style="text-align:right;padding:6px 8px;font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#B99A38;">YTD</th>
              </tr>
            </thead>
            <tbody>{market_rows}</tbody>
          </table>
          <div style="font-size:10px;color:#999;margin-top:6px;font-style:italic;font-family:'Century Gothic',Arial,sans-serif;">Source: Yahoo Finance. Prior close.</div>
        </td>
        <td width="2%"></td>
        <td width="49%" valign="top" style="padding-left:12px;">
          <div style="font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#B99A38;margin-bottom:8px;">Economic Readings — Latest</div>
          <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
            <thead>
              <tr style="background:#00141C;">
                <th style="text-align:left;padding:6px 8px;font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#B99A38;">Indicator</th>
                <th style="text-align:right;padding:6px 8px;font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#B99A38;">Reading</th>
                <th style="text-align:right;padding:6px 8px;font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#B99A38;">As of</th>
                <th style="text-align:right;padding:6px 8px;font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#B99A38;">Benchmark</th>
              </tr>
            </thead>
            <tbody>{econ_rows}</tbody>
          </table>
          <div style="font-size:10px;color:#999;margin-top:6px;font-style:italic;font-family:'Century Gothic',Arial,sans-serif;">Source: FRED (St. Louis Fed). Most recent release.</div>
        </td>
      </tr>
    </table>
    {fed_block}
  </div>"""


# ─── Email Builder ─────────────────────────────────────────────────────────────
def build_html(briefing_md, market_html, today_str):
    html_body = markdown.markdown(briefing_md, extensions=['extra'])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background-color: #f4f4f0;
    font-family: 'Palatino Linotype', Palatino, 'Book Antiqua', Georgia, serif;
    color: #1a1a1a;
    font-size: 15px;
    line-height: 1.75;
    padding: 32px 16px;
  }}

  .wrapper {{
    max-width: 900px;
    margin: 0 auto;
    background: #ffffff;
    border-top: 4px solid #B99A38;
  }}

  /* ── Header ── */
  .header {{
    background-color: #00141C;
    padding: 32px 40px 28px;
  }}

  .header-brand {{
    font-family: 'Century Gothic', 'Gill Sans', Arial, sans-serif;
    font-size: 11px;
    font-weight: 400;
    letter-spacing: 0.30em;
    color: #B99A38;
    text-transform: uppercase;
    display: block;
    margin-bottom: 6px;
  }}

  .header-title {{
    font-family: 'Palatino Linotype', Palatino, 'Book Antiqua', Georgia, serif;
    font-size: 32px;
    font-weight: 400;
    color: #f0ece2;
    letter-spacing: 0.04em;
    font-style: italic;
    line-height: 1.1;
    display: block;
  }}

  .header-rule {{
    width: 48px;
    height: 1px;
    background: #B99A38;
    margin: 14px 0 12px;
    display: block;
  }}

  .header-date {{
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 10px;
    letter-spacing: 0.14em;
    color: #4a6a74;
    text-transform: uppercase;
    display: block;
  }}

  /* ── Content ── */
  .content {{
    padding: 32px 40px 40px;
  }}

  h1 {{
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #00141C;
    margin-top: 36px;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #B99A38;
  }}

  h2 {{
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #00141C;
    margin-top: 36px;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #B99A38;
  }}

  h3 {{
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: none;
    color: #00141C;
    margin-top: 0;
    margin-bottom: 10px;
  }}

  p {{
    margin-bottom: 8px;
    margin-top: 0;
    color: #1a1a1a;
    font-size: 15px;
    line-height: 1.75;
  }}

  ul, ol {{
    margin: 8px 0 14px 0;
    padding-left: 20px;
  }}

  li {{
    margin-bottom: 10px;
    color: #1a1a1a;
    font-size: 15px;
    line-height: 1.75;
  }}

  strong {{
    color: #00141C;
    font-weight: 700;
  }}

  em {{
    color: #555;
    font-style: italic;
  }}

  a {{
    color: #1E6685;
    text-decoration: none;
    border-bottom: 1px solid #c5dde8;
  }}

  hr {{
    border: none;
    border-top: 1px solid #e8e5dd;
    margin: 22px 0;
  }}

  /* Article/starter topic header — bold lead-in, no gold bar */
  p > strong:only-child {{
    display: block;
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 13px;
    font-weight: 700;
    color: #00141C;
    margin-top: 0;
    margin-bottom: 2px;
    padding-top: 0;
    border-top: none;
    letter-spacing: 0.02em;
  }}

  blockquote {{
    border-left: 3px solid #B99A38;
    margin: 12px 0;
    padding: 6px 0 6px 14px;
    color: #444;
    font-style: italic;
  }}

  /* ── Footer ── */
  .footer {{
    background-color: #00141C;
    padding: 18px 40px 22px;
  }}

  .footer p {{
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 10px;
    letter-spacing: 0.10em;
    color: #4a6470;
    text-transform: uppercase;
    margin: 0 0 10px 0;
  }}

  .footer .disclaimer {{
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 10px;
    letter-spacing: 0.01em;
    color: #3a5460;
    text-transform: none;
    line-height: 1.6;
    margin: 0;
    border-top: 1px solid #0d2d3a;
    padding-top: 10px;
  }}

  .footer a {{
    color: #B99A38;
    border-bottom: none;
    text-decoration: none;
  }}
</style>
</head>
<body>
  <div class="wrapper">

    <div class="header">
      <span class="header-brand">Parcion Private Wealth</span>
      <span class="header-title">The Morning Brief</span>
      <span class="header-rule"></span>
      <span class="header-date">{today_str}</span>
    </div>

    <div class="content">
      {market_html}
      {html_body}
    </div>

    <div class="footer">
      <p>Parcion Private Wealth &nbsp;·&nbsp; Internal Use Only &nbsp;·&nbsp;
         <a href="https://www.parcionpw.com">parcionpw.com</a></p>
      <div class="disclaimer">This briefing is prepared for internal use by Parcion Private Wealth advisors only and should not be forwarded to clients, prospects, or any third party. Content is generated with the assistance of AI and aggregated from third-party sources. All data, statistics, and market information should be independently verified before use in client communications or advisory discussions. This material does not constitute investment advice.</div>
    </div>

  </div>
</body>
</html>"""


def send_email(briefing_md, market_html):
    today_str = datetime.now().strftime('%A, %B %d, %Y')
    subject   = f"The Parcion Morning Brief — {datetime.now().strftime('%B %d, %Y')}"
    html      = build_html(briefing_md, market_html, today_str)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = GMAIL_ADDRESS
    msg['To']      = WORK_EMAIL

    msg.attach(MIMEText(briefing_md, 'plain', 'utf-8'))
    msg.attach(MIMEText(html,        'html',  'utf-8'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, WORK_EMAIL, msg.as_string())

    print(f"  ✓ Email sent to {WORK_EMAIL}")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'─' * 54}")
    print(f"  The Parcion Morning Brief")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  {'Monday edition' if IS_MONDAY else datetime.now().strftime('%A')}")
    print(f"{'─' * 54}")

    print("\n[1/4] Fetching market & economic data...")
    market_data = fetch_market_data()
    econ_data   = fetch_economic_data()
    fed_data    = fetch_fed_expectations()
    market_html = build_market_html(market_data, econ_data, fed_data)
    print("      Market data ready")

    print("\n[2/4] Fetching news articles...")
    articles = compile_articles()
    total = sum(len(v) for v in articles.values())
    print(f"      {total} articles across {len(articles)} categories")

    print("\n[3/4] Synthesizing with Claude...")
    briefing = synthesize_with_claude(articles)
    print(f"      Briefing ready ({len(briefing):,} characters)")

    print("\n[4/4] Sending email...")
    send_email(briefing, market_html)

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
