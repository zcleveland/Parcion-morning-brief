#!/usr/bin/env python3
"""
The Parcion Morning Brief
Daily intelligence briefing for Parcion Private Wealth advisors.
Fetches news via curated RSS feeds, synthesizes with Claude, sends via Gmail.
"""

import feedparser
import smtplib
import os
import urllib.parse
import re
import markdown
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import anthropic


# ─── Configuration ─────────────────────────────────────────────────────────────
GMAIL_ADDRESS      = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
WORK_EMAIL         = os.environ['WORK_EMAIL']   # single recipient during testing
ANTHROPIC_API_KEY  = os.environ['ANTHROPIC_API_KEY']


# ─── News Sources ──────────────────────────────────────────────────────────────
DIRECT_FEEDS = {

    "notable_events": [
        "https://feeds.reuters.com/reuters/topNews",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://www.wsj.com/xml/rss/3_7085.xml",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    ],

    "geopolitics": [
        "https://warontherocks.com/feed/",
        "https://www.foreignaffairs.com/rss.xml",
        "https://www.cfr.org/rss/rss.xml",
        "https://www.aei.org/feed/",
        "https://geopoliticalfutures.com/feed/",
    ],

    "developed_markets": [
        "https://finance.yahoo.com/rss/topstories",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://www.morningstar.com/rss/rss.xml",
        "https://rpc.cfainstitute.org/feed",
    ],

    "international_markets": [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.cfr.org/rss/rss.xml",
    ],

    "private_equity": [
        "https://www.themiddlemarket.com/feed",
        "https://axios.com/feeds/feed.rss",
        "https://pitchbook.com/rss/news",
    ],

    "private_credit": [
        "https://www.institutionalinvestor.com/rss/articles.aspx",
        "https://feeds.reuters.com/reuters/companyNews",
        "https://www.themiddlemarket.com/feed",
    ],

    "venture_capital": [
        "https://techcrunch.com/category/venture/feed/",
        "https://news.crunchbase.com/feed/",
        "https://venturebeat.com/feed/",
    ],

    "real_estate_pe": [
        "https://www.globest.com/feed/",
        "https://therealdeal.com/feed/",
        "https://www.connectcre.com/feed/",
    ],

    "commodities": [
        "https://feeds.reuters.com/reuters/commoditiesNews",
        "https://www.nasdaq.com/feed/rssoutbound?category=Commodities",
    ],

    "precious_metals": [
        "https://www.kitco.com/rss/kitconews.rss",
        "https://www.mining.com/feed/",
    ],

    "estate_tax": [
        "https://taxpolicycenter.org/taxvox/feed",
        "https://www.journalofaccountancy.com/rss/all-content.xml",
        "https://www.kiplinger.com/feed/rss",
        "https://www.irs.gov/rss-feeds/irs-news-releases",
    ],

    "ma_business_sale": [
        "https://www.themiddlemarket.com/feed",
        "https://hbr.org/feed/topic/mergers-and-acquisitions",
        "https://pitchbook.com/rss/news",
        "https://axios.com/feeds/feed.rss",
    ],

    "wealth_management": [
        "https://www.thinkadvisor.com/feed/",
        "https://www.wealthmanagement.com/rss.xml",
        "https://www.investmentnews.com/feed",
        "https://citywire.com/ria/rss",
    ],

    "legislation": [
        "https://www.irs.gov/rss-feeds/irs-news-releases",
        "https://www.sec.gov/rss/news/pressreleases.rss",
        "https://www.govtrack.us/events/events.rss?feeds=misc:allvotes",
        # State feeds
        "https://app.leg.wa.gov/RSS/BillSummary.aspx",          # Washington
        "https://leginfo.legislature.ca.gov/faces/billSearchClient.xhtml",  # CA (fallback to GNews)
    ],
}

FALLBACK_QUERIES = {
    "notable_events":      "top news today business economy",
    "geopolitics":         "geopolitics trade policy global economy",
    "developed_markets":   "stock market S&P 500 Federal Reserve interest rates",
    "international_markets": "international markets global economy foreign currency",
    "private_equity":      "private equity buyout deal LBO acquisition",
    "private_credit":      "private credit direct lending leveraged loans",
    "venture_capital":     "venture capital startup funding VC",
    "real_estate_pe":      "commercial real estate private equity CRE",
    "commodities":         "commodities oil natural gas futures",
    "precious_metals":     "gold silver precious metals prices",
    "estate_tax":          "estate planning tax planning gift tax wealth transfer",
    "ma_business_sale":    "mergers acquisitions business sale M&A deal",
    "wealth_management":   "wealth management RIA family office fiduciary",
    "legislation":         "tax legislation IRS SEC regulation estate planning law",
}

LEGISLATION_STATE_QUERIES = [
    "Texas tax legislation business owners 2025",
    "Washington state tax legislation wealth 2025",
    "Oregon tax legislation estate planning 2025",
    "California tax legislation business owners 2025",
    "Arizona tax legislation wealth planning 2025",
    "federal tax legislation estate IRS 2025",
]


# ─── Helpers ───────────────────────────────────────────────────────────────────
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

        all_articles[topic] = articles[:6]

    # Extra legislation pass: state-specific Google News queries
    print("  → legislation (state + federal supplements)")
    leg_seen = set(a['title'] for a in all_articles.get('legislation', []))
    for q in LEGISLATION_STATE_QUERIES:
        results = fetch_news_google(q, max_articles=2)
        for a in results:
            if a['title'] not in leg_seen:
                leg_seen.add(a['title'])
                all_articles['legislation'].append(a)
    all_articles['legislation'] = all_articles['legislation'][:8]

    return all_articles


# ─── Build Claude Prompt ───────────────────────────────────────────────────────
def build_prompt(articles_by_topic):
    today = datetime.now().strftime('%A, %B %d, %Y')

    # Build article reference block
    article_text = ""
    counter = 1
    for topic, articles in articles_by_topic.items():
        article_text += f"\n\n### SOURCE CATEGORY: {topic.upper().replace('_', ' ')}\n"
        if not articles:
            article_text += "_No articles retrieved._\n"
            continue
        for a in articles:
            article_text += (
                f"[ART-{counter:03d}] {a['title']}\n"
                f"   Summary: {a['summary']}\n"
                f"   URL: {a['link']}\n\n"
            )
            counter += 1

    return f"""You are the editorial intelligence behind "The Parcion Morning Brief" — a daily internal briefing sent to the 8 advisors at Parcion Private Wealth, a nationally recognized independent private family office serving business owners and UHNW families through pre-liquidity, liquidity, and post-liquidity events.

Today is {today}.

AUDIENCE: Experienced wealth advisors. Assume strong financial literacy. Do not define basic terms. Do write clearly enough that a newer advisor can follow along.

GOAL: A focused, high-signal briefing readable in 5-7 minutes over coffee. Every section should earn its place. Skip any section or subsection entirely if there is no fresh, relevant content — do not pad or invent.

VOICE RULES (non-negotiable):
- Tone: calm, confident, analytically precise. Think CIO memo, not newsletter.
- Do NOT refer to "Zack" or any individual by name. Use "advisors may want to consider..." or "this is worth raising with clients who..."
- No investment recommendations. Observations on asset classes, macro conditions, and sectors only.
- No specific stock or fund picks.
- No language implying guaranteed outcomes.
- No alarming language: never use "crash," "collapse," or "unprecedented."
- Anchor volatility or uncertainty in historical context when relevant.
- Approved framing for guarded optimism: "constructive but cautious."
- When market conditions are bifurcated: "the K economy" is acceptable shorthand.
- Use "wealth event" or "liquidity event" — not just "transaction."
- Use "families" or "business owners" — not "high-net-worth individuals" or "HNWIs."
- No filler phrases: "it is worth noting," "this highlights the importance of," "in today's complex landscape," etc.
- No em dashes. Use commas or short sentence breaks instead.
- Oxford comma always.
- Links appear ONCE per article. Do not repeat the same link in multiple sections.
- Every article link must be formatted as markdown: [Link](URL) — use the word "Link" as the anchor text, not the full URL.

CONTENT RULES:
- Parcion relevance = specific to pre/post-liquidity business owners and UHNW families. Never generic retail investor framing.
- Conversation starters must be genuinely timely and tied to today's news. Only include ones that would be natural to act on today. If nothing qualifies, omit the section.
- Optional Reads should feel substantive, not like filler. Free access only. Can be up to 3 years old if still relevant.

---

Here are today's source articles by category:

{article_text}

---

Now produce the briefing using EXACTLY this structure. Use clean markdown. Do not include section headers that have no content.

---

# The Parcion Morning Brief
### {today}

---

## Notable Events

Five of the most significant news items or events from today. Each is one to two punchy sentences with a [Link](URL) at the end. CIO tone. Analytical, not alarmist. Focus on what matters for business owners, investors, and families with significant wealth.

- [Event 1 — one to two sentences.] ([Link](URL))
- [Event 2] ([Link](URL))
- [Event 3] ([Link](URL))
- [Event 4] ([Link](URL))
- [Event 5] ([Link](URL))

---

## Sector Review

Only include subsections where there is fresh, genuinely relevant content. Skip any subsection with no strong material today — do not pad.

For each subsection, list 1-3 items. Each item follows this exact format:

**Headline text** ([Link](URL))
**Punchline:** One to two sentences max. The TL;DR.
**Summary:** A brief paragraph. More depth than the punchline, but concise. Consider total email length.
**Relevance:** One to two sentences on why advisors at a family office serving business owners should be reading this.

---

### Geopolitics

[Items if available]

### Developed Markets

[Items if available]

### International Markets

[Items if available]

### Private Equity

[Items if available]

### Private Credit

[Items if available]

### Venture Capital

[Items if available]

### Real Estate

[Items if available]

### Commodities

[Items if available]

### Precious Metals

[Items if available]

### Estate and Tax Planning

[Items if available]

### M&A and Business Sales

[Items if available]

### Wealth Management and Family Office

[Items if available]

---

## Legislation Updates

Recent or newly issued legislation, regulatory guidance, or enforcement updates relevant to investing, estate planning, or taxes from a UHNW or private business owner perspective. Cover federal and the following states where relevant: Texas, Washington, Oregon, California, Arizona.

If nothing material has surfaced today, omit this section entirely.

For each item:
**[Jurisdiction — Topic]** ([Link](URL))
One to two sentences on what changed or was proposed, and why it matters for advisors working with business owners or UHNW families.

---

## Conversation Starters

Only include this section if today's news surfaced 2-3 genuinely strong, timely angles worth raising with a client or prospect. Do not force this. If the material is not there, omit the section.

For each:
**[Topic — 4 words or less]**
- **The angle:** One sentence an advisor could naturally say to open the conversation. Direct, not salesy.
- **Why now:** What in today's news makes this timely. Include [Link](URL).
- **Who it fits:** What type of client or prospect — be specific about their situation.
- **Where it goes:** The planning idea, technique, or next step if they engage. Name it specifically.

---

## Growing Your Network

3-4 quick bulleted ideas for advisors to grow their book through COI relationships and prospect outreach. COIs include CPAs, estate attorneys, M&A lawyers, and investment bankers — people adjacent to large business owners and UHNW families. If a creative AI-assisted outreach idea surfaces, include it.

- [Idea 1]
- [Idea 2]
- [Idea 3]
- [Idea 4 if warranted]

---

## Optional Reads

2-3 longer-form reads — white papers, research reports, or substantive opinion pieces — for advisors who want to go deeper. Free access only. Can be up to 3 years old if still relevant to today's themes.

**[Title]** ([Link](URL))
One sentence on what it covers and why it is worth the time.

---"""


# ─── Call Claude API (with streaming to avoid timeout) ─────────────────────────
def synthesize_with_claude(articles_by_topic):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = build_prompt(articles_by_topic)

    print(f"      Prompt length: {len(prompt):,} characters")

    full_text = ""
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            full_text += text

    return full_text


# ─── Format & Send Email ───────────────────────────────────────────────────────
def build_html(briefing_md, today_str):
    html_body = markdown.markdown(briefing_md, extensions=['extra'])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  /* ── Reset ── */
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background-color: #f4f4f0;
    font-family: 'Palatino Linotype', Palatino, 'Book Antiqua', Georgia, serif;
    color: #1a1a1a;
    font-size: 15px;
    line-height: 1.75;
    padding: 32px 16px;
  }}

  /* ── Outer wrapper ── */
  .wrapper {{
    max-width: 660px;
    margin: 0 auto;
    background: #ffffff;
    border-top: 4px solid #B99A38;
  }}

  /* ── Header ── */
  .header {{
    background-color: #00141C;
    padding: 28px 36px 24px;
    text-align: left;
  }}

  .header-wordmark {{
    font-family: 'Century Gothic', 'Gill Sans', 'Trebuchet MS', Arial, sans-serif;
    font-size: 22px;
    font-weight: 300;
    letter-spacing: 0.22em;
    color: #B99A38;
    text-transform: uppercase;
    display: block;
  }}

  .header-sub {{
    font-family: 'Century Gothic', 'Gill Sans', Arial, sans-serif;
    font-size: 10px;
    letter-spacing: 0.18em;
    color: #7a8f96;
    text-transform: uppercase;
    margin-top: 4px;
    display: block;
  }}

  .header-date {{
    font-family: 'Palatino Linotype', Palatino, Georgia, serif;
    font-size: 12px;
    color: #4a6470;
    margin-top: 10px;
    display: block;
    font-style: italic;
  }}

  /* ── Content ── */
  .content {{
    padding: 32px 36px 40px;
  }}

  /* ── Typography ── */
  h1 {{
    font-family: 'Century Gothic', 'Gill Sans', Arial, sans-serif;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #00141C;
    margin-top: 36px;
    margin-bottom: 14px;
    padding-bottom: 6px;
    border-bottom: 1px solid #B99A38;
  }}

  h2 {{
    font-family: 'Century Gothic', 'Gill Sans', Arial, sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #00141C;
    margin-top: 28px;
    margin-bottom: 10px;
    padding-bottom: 4px;
    border-bottom: 1px solid #e0ddd5;
  }}

  h3 {{
    font-family: 'Century Gothic', 'Gill Sans', Arial, sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: #B99A38;
    margin-top: 22px;
    margin-bottom: 8px;
  }}

  p {{
    margin-bottom: 12px;
    color: #1a1a1a;
  }}

  ul, ol {{
    margin: 8px 0 14px 0;
    padding-left: 20px;
  }}

  li {{
    margin-bottom: 9px;
    color: #1a1a1a;
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

  a:hover {{
    color: #B99A38;
    border-bottom-color: #B99A38;
  }}

  hr {{
    border: none;
    border-top: 1px solid #e8e5dd;
    margin: 28px 0;
  }}

  /* ── Article items ── */
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
    padding: 18px 36px;
    text-align: left;
  }}

  .footer p {{
    font-family: 'Century Gothic', Arial, sans-serif;
    font-size: 10px;
    letter-spacing: 0.10em;
    color: #4a6470;
    text-transform: uppercase;
    margin: 0;
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
      <span class="header-wordmark">Parcion</span>
      <span class="header-sub">Private Wealth &nbsp;·&nbsp; Morning Brief</span>
      <span class="header-date">{today_str}</span>
    </div>

    <div class="content">
      {html_body}
    </div>

    <div class="footer">
      <p>Parcion Private Wealth &nbsp;·&nbsp; Internal Use Only &nbsp;·&nbsp;
         <a href="https://www.parcionpw.com">parcionpw.com</a></p>
    </div>

  </div>
</body>
</html>"""


def send_email(briefing_md):
    today_str = datetime.now().strftime('%A, %B %d, %Y')
    subject   = f"The Parcion Morning Brief — {datetime.now().strftime('%B %d, %Y')}"

    html = build_html(briefing_md, today_str)

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
    print(f"{'─' * 54}")

    print("\n[1/3] Fetching news articles...")
    articles = compile_articles()
    total = sum(len(v) for v in articles.values())
    print(f"      {total} articles across {len(articles)} categories")

    print("\n[2/3] Synthesizing with Claude...")
    briefing = synthesize_with_claude(articles)
    print(f"      Briefing ready ({len(briefing):,} characters)")

    print("\n[3/3] Sending email...")
    send_email(briefing)

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
