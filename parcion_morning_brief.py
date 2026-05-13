#!/usr/bin/env python3
"""
The Parcion Morning Brief — v2
Daily intelligence briefing for Parcion Private Wealth advisors.
Fetches news via curated RSS feeds, market data via Yahoo Finance + FRED.
Synthesizes with Claude, sends via Gmail to Google Sheets subscriber list.
"""

import feedparser
import smtplib
import os
import urllib.parse
import re
import requests
import markdown
import json
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date
import anthropic


# ─── Configuration ─────────────────────────────────────────────────────────────
GMAIL_ADDRESS           = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD      = os.environ['GMAIL_APP_PASSWORD']
WORK_EMAIL              = os.environ['WORK_EMAIL']
ANTHROPIC_API_KEY       = os.environ['ANTHROPIC_API_KEY']
FRED_API_KEY            = os.environ['FRED_API_KEY']
GOOGLE_SHEET_ID         = os.environ.get('GOOGLE_SHEET_ID', '')
GOOGLE_SERVICE_ACCOUNT  = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
APPS_SCRIPT_URL         = os.environ.get('APPS_SCRIPT_URL', '')
GITHUB_TOKEN            = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO             = os.environ.get('GITHUB_REPOSITORY', '')  # e.g. zcleveland/morning-briefing
IS_DEV                  = os.environ.get('BRANCH_NAME', 'main') == 'dev'
IS_MONDAY               = datetime.now().weekday() == 0


# ─── Subscriber List ───────────────────────────────────────────────────────────
def get_subscribers():
    """
    Fetch active subscribers from Google Sheets.
    Falls back to WORK_EMAIL if Sheet is unavailable or we're on dev branch.
    """
    if IS_DEV:
        print(f"  → DEV mode: sending only to {WORK_EMAIL}")
        return [WORK_EMAIL]

    if not GOOGLE_SHEET_ID or not GOOGLE_SERVICE_ACCOUNT:
        print(f"  → No Sheet config: falling back to {WORK_EMAIL}")
        return [WORK_EMAIL]

    try:
        import google.oauth2.service_account as sa
        from googleapiclient.discovery import build

        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT)
        creds = sa.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        service = build("sheets", "v4", credentials=creds)
        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet1!A2:C"
        ).execute()
        rows = result.get("values", [])
        active = [r[0] for r in rows if len(r) >= 3 and r[2].lower() == "active"]
        print(f"  → {len(active)} active subscribers from Sheet")
        return active if active else [WORK_EMAIL]

    except Exception as e:
        print(f"  → Sheet fetch failed ({e}): falling back to {WORK_EMAIL}")
        return [WORK_EMAIL]


# ─── Weekly Note (But First...) ────────────────────────────────────────────────
def get_weekly_note():
    """
    Read weekly_note.html from the repo root.
    Returns (content, is_empty) tuple.
    Content is raw HTML if present, None if empty/placeholder.
    """
    try:
        note_path = os.path.join(os.path.dirname(__file__), 'weekly_note.html')
        if not os.path.exists(note_path):
            return None, True

        with open(note_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        # Check if it's just the placeholder comment
        stripped = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL).strip()
        if not stripped:
            return None, True

        return stripped, False

    except Exception as e:
        print(f"  → Could not read weekly_note.html: {e}")
        return None, True


def clear_weekly_note():
    """
    After a successful send, reset weekly_note.html to the placeholder.
    Uses GitHub API to commit the change.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("  → No GitHub token — weekly note not auto-cleared")
        return

    placeholder = '<!-- WEEKLY NOTE: Add content here to include in next send. Delete content to skip. -->\n'

    try:
        # Get current file SHA (required for update)
        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json'
        }
        url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/weekly_note.html'
        r = requests.get(url, headers=headers)
        sha = r.json().get('sha', '')

        # Determine branch
        branch = 'dev' if IS_DEV else 'main'

        # Commit the cleared file
        payload = {
            'message': 'auto: clear weekly note after send',
            'content': base64.b64encode(placeholder.encode()).decode(),
            'sha': sha,
            'branch': branch
        }
        requests.put(url, headers=headers, json=payload)
        print("  → Weekly note cleared")

    except Exception as e:
        print(f"  → Could not clear weekly note: {e}")


# ─── News Sources ──────────────────────────────────────────────────────────────
DIRECT_FEEDS = {

    "markets_macro": [
        "https://feeds.reuters.com/reuters/topNews",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/commoditiesNews",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://finance.yahoo.com/rss/topstories",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://www.morningstar.com/rss/rss.xml",
        "https://rpc.cfainstitute.org/feed",
        "https://www.themiddlemarket.com/feed",
        "https://www.institutionalinvestor.com/rss/articles.aspx",
        "https://techcrunch.com/category/venture/feed/",
        "https://www.globest.com/feed/",
        "https://www.kitco.com/rss/kitconews.rss",
        "https://warontherocks.com/feed/",
        "https://www.foreignaffairs.com/rss.xml",
        "https://pitchbook.com/rss/news",
        "https://hbr.org/feed/topic/mergers-and-acquisitions",
        "https://axios.com/feeds/feed.rss",
    ],

    "family_office": [
        "https://taxpolicycenter.org/taxvox/feed",
        "https://www.journalofaccountancy.com/rss/all-content.xml",
        "https://www.kiplinger.com/feed/rss",
        "https://www.irs.gov/rss-feeds/irs-news-releases",
        "https://www.sec.gov/rss/news/pressreleases.rss",
        "https://www.govtrack.us/events/events.rss?feeds=misc:allvotes",
        "https://app.leg.wa.gov/RSS/BillSummary.aspx",
        "https://www.thinkadvisor.com/feed/",
        "https://www.wealthmanagement.com/rss.xml",
        "https://www.investmentnews.com/feed",
        "https://citywire.com/ria/rss",
    ],
}

FALLBACK_QUERIES = {
    "markets_macro":  "stock market economy Fed rates M&A private equity geopolitics business",
    "family_office":  "estate planning tax legislation wealth management family office fiduciary RIA",
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

def pct(v):   return f"{v:.1f}%"
def rate(v):  return f"{v:.2f}%"
def idx(v):   return f"{v:.1f}"

FRED_SERIES = [
    ("CPI YoY",          "CPIAUCSL",          pct,  "pc1",  "Target: 2.0%"),
    ("Core CPI YoY",     "CPILFESL",          pct,  "pc1",  "Target: 2.0%"),
    ("PCE YoY",          "PCEPI",             pct,  "pc1",  "Fed target: 2.0%"),
    ("Core PCE YoY",     "PCEPILFE",          pct,  "pc1",  "Fed target: 2.0%"),
    ("Unemployment",     "UNRATE",            pct,  "lin",  "Avg (2015-19): 4.4%"),
    ("GDP Growth",       "A191RL1Q225SBEA",   pct,  "lin",  "Long-run avg: ~2.5%"),
    ("UMich Sentiment",  "UMCSENT",           idx,  "lin",  "Avg (2015-19): 96.5"),
    ("Fed Funds Rate",   "FEDFUNDS",          rate, "lin",  None),
]


def get_yahoo_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get("https://finance.yahoo.com", timeout=10)
    except Exception:
        pass
    return session

_yahoo_session = None

def fetch_yahoo_quote(symbol):
    global _yahoo_session
    if _yahoo_session is None:
        _yahoo_session = get_yahoo_session()
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        params = {"interval": "1d", "range": "1y", "includePrePost": "false"}
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
    print("  → Fetching market data (Yahoo Finance)...")
    results = []
    for name, symbol in MARKET_TICKERS:
        price, ytd = fetch_yahoo_quote(symbol)
        results.append({"name": name, "value": format_market_value(name, price), "ytd": ytd})
        print(f"    {'✓' if price else '✗'} {name}: {format_market_value(name, price)}")
    return results


def fetch_fred_series(series_id, units="lin"):
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id":       series_id,
            "api_key":         FRED_API_KEY,
            "file_type":       "json",
            "sort_order":      "desc",
            "limit":           "6",
            "observation_end": date.today().isoformat(),
            "units":           units,
        }
        r   = requests.get(url, params=params, timeout=10)
        obs = r.json()["observations"]
        for o in obs:
            if o["value"] not in (".", "", "N/A"):
                val   = float(o["value"])
                dt    = datetime.strptime(o["date"], "%Y-%m-%d")
                as_of = dt.strftime("%b %Y") if dt.day == 1 else dt.strftime("%b %d, %Y")
                return val, as_of
        return None, None
    except Exception as e:
        print(f"    ✗ FRED {series_id}: {e}")
        return None, None


def fetch_fed_15yr_avg():
    try:
        fifteen_yrs_ago = (datetime.now().replace(year=datetime.now().year - 15)).strftime("%Y-%m-%d")
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id":         "FEDFUNDS",
            "api_key":           FRED_API_KEY,
            "file_type":         "json",
            "observation_start": fifteen_yrs_ago,
            "observation_end":   date.today().isoformat(),
            "units":             "lin",
        }
        r    = requests.get(url, params=params, timeout=10)
        obs  = r.json()["observations"]
        vals = [float(o["value"]) for o in obs if o["value"] not in (".", "")]
        if vals:
            avg = sum(vals) / len(vals)
            return f"15yr avg: {avg:.1f}%"
        return None
    except Exception:
        return None


def fetch_economic_data():
    print("  → Fetching economic data (FRED)...")
    fed_15yr = fetch_fed_15yr_avg()
    results  = []
    for name, series_id, fmt, units, context in FRED_SERIES:
        val, as_of = fetch_fred_series(series_id, units=units)
        formatted  = fmt(val) if val is not None else "N/A"
        if name == "Fed Funds Rate" and fed_15yr:
            context = fed_15yr
        results.append({"name": name, "value": formatted, "as_of": as_of or "N/A", "context": context or ""})
        print(f"    {'✓' if val else '✗'} {name}: {formatted}")
    return results


def fetch_fed_expectations():
    print("  → Fetching Fed expectations (FRED)...")
    try:
        curr_val, _ = fetch_fred_series("FEDFUNDS")
        ye_tbill, _ = fetch_fred_series("DTB6")
        if curr_val is None:
            raise ValueError("No current Fed Funds rate")
        today = datetime.now()
        fomc_2026 = [
            datetime(2026, 1, 29), datetime(2026, 3, 19), datetime(2026, 4, 29),
            datetime(2026, 5, 7),  datetime(2026, 6, 18), datetime(2026, 7, 30),
            datetime(2026, 9, 17), datetime(2026, 10, 29), datetime(2026, 12, 10),
        ]
        next_meeting = next((d for d in fomc_2026 if d > today), None)
        meeting_str  = next_meeting.strftime("%B %d, %Y") if next_meeting else "Next Meeting"
        if ye_tbill:
            yr_end    = f"{ye_tbill:.2f}%"
            diff      = ye_tbill - curr_val
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
        feed     = feedparser.parse(url)
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
    url     = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    return fetch_from_feed(url, max_articles)


def compile_articles():
    all_articles = {}

    for topic, feeds in DIRECT_FEEDS.items():
        print(f"  → {topic}")
        articles = []
        seen     = set()
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
        all_articles[topic] = articles[:10]

    # Supplement family_office with state legislation
    print("  → family_office (state legislation supplements)")
    fo_seen = set(a['title'] for a in all_articles.get('family_office', []))
    for q in LEGISLATION_STATE_QUERIES:
        results = fetch_news_google(q, max_articles=2)
        for a in results:
            if a['title'] not in fo_seen:
                fo_seen.add(a['title'])
                all_articles['family_office'].append(a)
    all_articles['family_office'] = all_articles['family_office'][:10]

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

    week_ahead_section = ""
    if IS_MONDAY:
        week_ahead_section = """
---

## Week Ahead

Monday only. A clean forward-looking block: key economic data releases, Fed speakers, earnings reports of note, and any scheduled legislative votes or regulatory decisions relevant to markets or wealth planning. 4-6 items max. Format as a simple list — date, event, one-sentence context. Skip any item that isn't genuinely market-moving or advisor-relevant.

**[Day, Date]** — [Event]: [One sentence on what to watch for and why it matters.]

"""

    return f"""You are the editorial intelligence behind "The Parcion Morning Brief" — a daily internal briefing for the 8 advisors at Parcion Private Wealth, a nationally recognized independent private family office serving business owners and UHNW families through pre-liquidity, liquidity, and post-liquidity wealth events.

Today is {today}.

AUDIENCE: Experienced wealth advisors. Assume strong financial literacy. Accessible enough for a newer advisor. These advisors read this at 6am over coffee or on their phone before client calls.

GOAL: A tight, high-signal briefing readable in 5-7 minutes. Every section earns its place. When in doubt, cut it entirely. Target 800 words of editorial content maximum.

VOICE (non-negotiable):
- The imaginary writer is a hybrid of a Bloomberg terminal analyst and a New York Times journalist. Bloomberg precision and market fluency. NYT narrative clarity and readability. The result: analytically sharp, clearly written, never jargon-heavy.
- CIO memo tone. Calm, confident, precise, human.
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

ARTICLE FORMAT (critical — new in v2):
Each article gets ONE paragraph, 2-3 sentences maximum. No labels (no "Punchline:" or "Relevance:"). Write it like a smart colleague summarizing the story over coffee: lead with what happened, include the market or planning implication naturally in the same breath. Only include an explicit advisor talking point if there is a genuine, non-forced connection. If the story is important but the advisor angle isn't obvious, a clean factual summary is better than a forced relevance.

Example of the correct format:
The Federal Reserve held rates steady but revised its dot plot downward for 2026, signaling fewer cuts than markets had priced in. Equity futures sold off on the news before recovering by close — worth watching whether the move sticks heading into the week. Clients with variable-rate exposure or near-term liquidity needs will likely ask about timing. ([Link](URL))

DEDUPLICATION RULE: If two or more articles cover the same story, pick the single strongest one and drop the rest entirely.

CATEGORIZATION RULE: Markets & Macro gets all market, economic, geopolitical, M&A, private equity, and business news. Family Office gets all tax, estate, legislative, regulatory, wealth planning, and RIA industry news. When in doubt, use topic relevance not feed source.

SUB-SECTION RULE: Within Markets & Macro, if there is strong M&A or private markets content, introduce it under a brief sub-header (e.g., "M&A and Private Markets") using a simple bold label — not a full section header. Same for Family Office: if there is strong tax/estate legislative content, introduce it under a sub-header (e.g., "Tax and Estate"). Only add sub-headers when there are 2+ items in that sub-topic. If only one item, fold it into the main section flow without a sub-header.

---

Here are today's source articles by feed category:
{article_text}

---

Produce the briefing using EXACTLY this structure. Do NOT include a title or date — the email header handles this. Skip any section with no strong material.

---

## Inspirational Quote

One quote. Attributed to a real person, a literary source, or anonymous. Can be stoic philosophy, leadership wisdom, business insight, or life perspective. Should feel like something worth sitting with for a moment before the day starts. Format exactly:

> "[Quote text]"
> — [Attribution]

---

## The Numbers

Do NOT generate this section. It is injected automatically from live market data.

---
{week_ahead_section}

## Markets & Macro

All market, economic, geopolitical, M&A, and business news. 3-5 items. This is the core of the brief. Use sub-headers (bold labels only, not ## headers) for M&A and Private Markets content if 2+ items exist there. Skip if nothing strong.

For each item, the format is:

**[Headline as written by a journalist, not a label]** ([Link](URL))

[Single paragraph, 2-3 sentences. Bloomberg precision, NYT clarity. Talking point only if genuinely there.]

Separate items with ---

---

## Family Office

Tax, estate, legislative, regulatory, wealth planning, and RIA industry news. 2-3 items. Use sub-headers (bold labels only) for Tax and Estate content if 2+ items exist there. Skip if nothing strong.

Same format as Markets & Macro.

---

## Conversation Starters

Only include if today's news produced 2 genuinely strong, timely angles for client conversations. If the material is not there, omit this section entirely. Do not force it.

For each starter:

**[Topic — 4 words max]**

**Angle:** [One sentence an advisor could naturally say to open the conversation.]

**Why now:** [What in today's news makes this timely.] ([Link](URL))

**Who:** [Specific client type and situation.]

**Where it goes:** [Named planning technique: GRAT, IDGT, SLAT, DAF, installment sale, Roth conversion, private credit allocation, etc.]

Separate starters with ---

---"""


# ─── Claude API Call ───────────────────────────────────────────────────────────
def synthesize_with_claude(articles_by_topic):
    client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt    = build_prompt(articles_by_topic)
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


# ─── Market Data HTML ──────────────────────────────────────────────────────────
def build_market_html(market_data, econ_data, fed_data):

    def ytd_cell(ytd):
        if ytd is None:
            return '<td style="padding:6px 10px;text-align:right;color:#888;font-size:12px;">N/A</td>'
        color = "#2a7a3b" if ytd >= 0 else "#c0392b"
        sign  = "+" if ytd >= 0 else ""
        return f'<td style="padding:6px 10px;text-align:right;color:{color};font-weight:700;font-size:13px;">{sign}{ytd:.1f}%</td>'

    market_rows = ""
    for i, m in enumerate(market_data):
        bg = "#ffffff" if i % 2 == 0 else "#f7f7f4"
        market_rows += f"""
        <tr style="background:{bg};">
          <td style="padding:6px 10px;color:#1a1a1a;font-size:13px;">{m['name']}</td>
          <td style="padding:6px 10px;text-align:right;color:#1a1a1a;font-size:13px;">{m['value']}</td>
          {ytd_cell(m['ytd'])}
        </tr>"""

    econ_rows = ""
    for i, e in enumerate(econ_data):
        bg   = "#ffffff" if i % 2 == 0 else "#f7f7f4"
        bold = "font-weight:700;" if "Fed" in e['name'] else ""
        econ_rows += f"""
        <tr style="background:{bg};">
          <td style="padding:6px 8px;color:#1a1a1a;font-size:12px;">{e['name']}</td>
          <td style="padding:6px 8px;text-align:right;color:#1a1a1a;font-size:12px;{bold}">{e['value']}</td>
          <td style="padding:6px 8px;text-align:right;color:#888;font-size:10px;">{e['as_of']}</td>
          <td style="padding:6px 8px;text-align:right;color:#7a8f96;font-size:10px;font-style:italic;">{e.get('context','')}</td>
        </tr>"""

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
    <div style="font-family:'Century Gothic',Arial,sans-serif;font-size:10px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#00141C;border-bottom:1px solid #B99A38;padding-bottom:6px;margin-bottom:16px;">The Numbers</div>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="420" valign="top" style="padding-right:16px;min-width:280px;">
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
        <td width="16" style="min-width:0;"></td>
        <td width="420" valign="top" style="min-width:280px;">
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


# ─── But First... / Quote HTML Block ──────────────────────────────────────────
def build_but_first_html(note_content, note_is_empty):
    """Build the But First... or Inspirational Quote block."""

    if not note_is_empty and note_content:
        # But First... — personal note from Zack
        return f"""
  <div style="background:#f7f7f4;border-left:3px solid #B99A38;padding:20px 24px;margin-bottom:28px;">
    <div style="font-family:'Century Gothic',Arial,sans-serif;font-size:9px;font-weight:700;letter-spacing:0.20em;text-transform:uppercase;color:#B99A38;margin-bottom:12px;">But First...</div>
    <div style="font-family:'Palatino Linotype',Palatino,'Book Antiqua',Georgia,serif;font-size:15px;line-height:1.75;color:#1a1a1a;">
      {note_content}
    </div>
  </div>"""
    else:
        # Inspirational Quote — placeholder, Claude fills this in via the prompt
        # The quote comes through in the briefing markdown and is styled by CSS
        return ""


# ─── Email Builder ─────────────────────────────────────────────────────────────
def build_html(briefing_md, market_html, but_first_html, today_str, recipient_email):
    """Build full HTML email."""

    # Extract the inspirational quote from briefing if present, inject styled version
    # The markdown ## Inspirational Quote section is handled by CSS blockquote styling
    html_body = markdown.markdown(briefing_md, extensions=['extra'])

    # Build unsubscribe URL
    unsubscribe_url = ""
    if APPS_SCRIPT_URL:
        encoded_email   = urllib.parse.quote(recipient_email)
        unsubscribe_url = f"{APPS_SCRIPT_URL}?action=unsubscribe&email={encoded_email}"

    unsubscribe_link = ""
    if unsubscribe_url:
        unsubscribe_link = f'&nbsp;·&nbsp; <a href="{unsubscribe_url}" style="color:#B99A38;text-decoration:none;">Unsubscribe</a>'

    dev_banner = ""
    if IS_DEV:
        dev_banner = """
    <div style="background:#c0392b;color:#fff;text-align:center;padding:8px;font-family:'Century Gothic',Arial,sans-serif;font-size:11px;letter-spacing:0.10em;text-transform:uppercase;">
      ⚠ DEV BUILD — Internal Test Only
    </div>"""

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
  .content {{
    padding: 32px 40px 40px;
  }}
  h1, h2 {{
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
  strong {{ color: #00141C; font-weight: 700; }}
  em {{ color: #555; font-style: italic; }}
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
  blockquote {{
    border-left: 3px solid #B99A38;
    margin: 4px 0 20px 0;
    padding: 10px 0 10px 18px;
    color: #2a2a2a;
    font-style: italic;
    font-size: 16px;
    line-height: 1.7;
  }}
  blockquote p {{
    margin: 0;
    color: #2a2a2a;
    font-size: 16px;
  }}
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
  .footer a {{ color: #B99A38; border-bottom: none; text-decoration: none; }}
  @media only screen and (max-width: 620px) {{
    body {{ padding: 0 !important; }}
    .wrapper {{ width: 100% !important; max-width: 100% !important; }}
    .header {{ padding: 24px 20px 20px !important; }}
    .header-title {{ font-size: 24px !important; }}
    .content {{ padding: 20px 20px 28px !important; }}
    .footer {{ padding: 16px 20px 20px !important; }}
    h1, h2 {{ font-size: 12px !important; }}
  }}
</style>
</head>
<body>
  <div class="wrapper">
    {dev_banner}
    <div class="header">
      <span class="header-brand">Parcion Private Wealth</span>
      <span class="header-title">The Morning Brief</span>
      <span class="header-rule"></span>
      <span class="header-date">{today_str}</span>
    </div>
    <div class="content">
      {but_first_html}
      {market_html}
      {html_body}
    </div>
    <div class="footer">
      <p>Parcion Private Wealth &nbsp;·&nbsp; Internal Use Only &nbsp;·&nbsp;
         <a href="https://www.parcionpw.com">parcionpw.com</a>{unsubscribe_link}</p>
      <div class="disclaimer">This briefing is prepared for internal use by Parcion Private Wealth advisors only and should not be forwarded to clients, prospects, or any third party. Content is generated with the assistance of AI and aggregated from third-party sources. All data, statistics, and market information should be independently verified before use in client communications or advisory discussions. This material does not constitute investment advice.</div>
    </div>
  </div>
</body>
</html>"""


def send_email(briefing_md, market_html, but_first_html, subscribers):
    today_str = datetime.now().strftime('%A, %B %d, %Y')
    subject   = f"The Parcion Morning Brief — {datetime.now().strftime('%B %d, %Y')}"
    if IS_DEV:
        subject = f"[DEV] {subject}"

    print(f"  → Sending to {len(subscribers)} recipient(s)...")

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

        for recipient in subscribers:
            html = build_html(briefing_md, market_html, but_first_html, today_str, recipient)
            msg  = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = GMAIL_ADDRESS
            msg['To']      = recipient
            msg.attach(MIMEText(briefing_md, 'plain', 'utf-8'))
            msg.attach(MIMEText(html,        'html',  'utf-8'))
            server.sendmail(GMAIL_ADDRESS, recipient, msg.as_string())
            print(f"    ✓ Sent to {recipient}")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'─' * 54}")
    print(f"  The Parcion Morning Brief {'[DEV]' if IS_DEV else ''}")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  {'Monday edition' if IS_MONDAY else datetime.now().strftime('%A')}")
    print(f"{'─' * 54}")

    print("\n[1/5] Reading weekly note...")
    note_content, note_is_empty = get_weekly_note()
    if not note_is_empty:
        print("      'But First...' note found — will include")
    else:
        print("      No note — inspirational quote will run")

    print("\n[2/5] Fetching market & economic data...")
    market_data = fetch_market_data()
    econ_data   = fetch_economic_data()
    fed_data    = fetch_fed_expectations()
    market_html = build_market_html(market_data, econ_data, fed_data)
    print("      Market data ready")

    print("\n[3/5] Fetching news articles...")
    articles = compile_articles()
    total    = sum(len(v) for v in articles.values())
    print(f"      {total} articles across {len(articles)} categories")

    print("\n[4/5] Synthesizing with Claude...")
    briefing      = synthesize_with_claude(articles)
    but_first_html = build_but_first_html(note_content, note_is_empty)
    print(f"      Briefing ready ({len(briefing):,} characters)")

    print("\n[5/5] Sending email...")
    subscribers = get_subscribers()
    send_email(briefing, market_html, but_first_html, subscribers)

    # Clear the weekly note after successful send
    if not note_is_empty:
        print("\n  Clearing weekly note...")
        clear_weekly_note()

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
