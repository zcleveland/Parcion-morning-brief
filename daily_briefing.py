#!/usr/bin/env python3
"""
Daily Morning Briefing — Parcion Private Wealth
Fetches news via curated RSS feeds (with Google News fallback), synthesizes with Claude, sends via Gmail.
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


# ─── Configuration (loaded from GitHub Secrets) ───────────────────────────────
GMAIL_ADDRESS      = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
WORK_EMAIL         = os.environ['WORK_EMAIL']
ANTHROPIC_API_KEY  = os.environ['ANTHROPIC_API_KEY']


# ─── News Sources ──────────────────────────────────────────────────────────────
DIRECT_FEEDS = {
    "Markets & Investing": [
        "https://finance.yahoo.com/rss/topstories",
        "https://seekingalpha.com/feed.xml",
        "https://www.morningstar.com/rss/rss.xml",
        "https://rpc.cfainstitute.org/feed",
    ],
    "Geopolitics & Macro": [
        "https://warontherocks.com/feed/",
        "https://www.geopoliticalmonitor.com/feed/",
        "https://geopoliticalfutures.com/feed/",
        "https://www.foreignaffairs.com/rss.xml",
        "https://www.criticalthreats.org/feed",
        "https://www.aei.org/feed/",
    ],
    "Estate & Tax Planning": [
        "https://taxpolicycenter.org/taxvox/feed",
        "https://www.journalofaccountancy.com/rss/all-content.xml",
        "https://www.kiplinger.com/feed/rss",
        "https://www.irs.gov/rss-feeds/irs-news-releases",
    ],
    "Selling a Business / M&A": [
        "https://www.themiddlemarket.com/feed",
        "https://hbr.org/feed/topic/mergers-and-acquisitions",
        "https://pitchbook.com/rss/news",
    ],
    "Wealth Management & Family Office": [
        "https://www.thinkadvisor.com/feed/",
        "https://www.wealthmanagement.com/rss.xml",
        "https://www.investmentnews.com/feed",
        "https://www.advisorhub.com/feed/",
        "https://citywire.com/ria/rss",
    ],
}

# Google News fallback queries (used if direct feeds return < 3 articles)
FALLBACK_QUERIES = {
    "Markets & Investing":
        "stock market investing Federal Reserve interest rates S&P 500",
    "Geopolitics & Macro":
        "geopolitics trade tariffs global economy inflation currency",
    "Estate & Tax Planning":
        "estate planning tax planning gift tax wealth transfer trust",
    "Selling a Business / M&A":
        "selling business M&A private equity acquisition EBITDA deal",
    "Wealth Management & Family Office":
        "wealth management family office ultra high net worth fiduciary",
}


# ─── Helpers ───────────────────────────────────────────────────────────────────
def strip_html(text):
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text).strip()


def fetch_from_feed(url, max_articles=5):
    """Fetch articles from a direct RSS feed URL."""
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_articles]:
            title   = strip_html(entry.get('title', 'No title'))
            summary = strip_html(entry.get('summary', entry.get('description', '')))
            summary = summary[:450] if summary else 'No summary available.'
            link    = entry.get('link', '')
            articles.append({"title": title, "summary": summary, "link": link})
        return articles
    except Exception as e:
        print(f"    ✗ Feed failed ({url}): {e}")
        return []


def fetch_news_google(query, max_articles=6):
    """Fallback: fetch from Google News RSS."""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    return fetch_from_feed(url, max_articles)


# ─── Compile All Articles ──────────────────────────────────────────────────────
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
            if results:
                print(f"    ✓ {len(results)} articles from {feed_url}")

        # Fallback to Google News if we didn't get enough
        if len(articles) < 3:
            print(f"    ↩ Falling back to Google News for {topic}")
            fallback = fetch_news_google(FALLBACK_QUERIES[topic])
            for a in fallback:
                if a['title'] not in seen:
                    seen.add(a['title'])
                    articles.append(a)

        all_articles[topic] = articles[:8]  # cap at 8 per category
    return all_articles


# ─── Build Claude Prompt ───────────────────────────────────────────────────────
def build_prompt(articles_by_topic):
    today = datetime.now().strftime('%A, %B %d, %Y')

    # Build article text AND a URL reference map Claude can cite
    article_text = ""
    url_map = {}  # "ARTICLE-01" -> {title, link}
    counter = 1

    for topic, articles in articles_by_topic.items():
        article_text += f"\n\n### {topic}\n"
        if not articles:
            article_text += "_No articles retrieved for this category._\n"
            continue
        for a in articles:
            ref_id = f"ARTICLE-{counter:02d}"
            url_map[ref_id] = {"title": a['title'], "link": a['link']}
            article_text += f"[{ref_id}] **{a['title']}**\n   {a['summary']}\n   URL: {a['link']}\n\n"
            counter += 1

    return f"""You are preparing a daily morning intelligence briefing for Zack Cleveland, a senior wealth advisor at Parcion Private Wealth. Zack serves ultra-high-net-worth clients — primarily individuals and families with $20M+ in investable assets, most of whom have already sold a business or are actively planning to within the next 1–5 years. These are sophisticated, busy people who expect their advisor to bring them ideas, not just information. Zack holds a CFA charter and has deep expertise in portfolio management, estate planning, tax planning, and UHNW advisory. He needs to be the most informed person in the room.

Today is {today}.

Below are today's top articles across five key categories. Each article has a reference ID (e.g. ARTICLE-01) and a URL. When you reference an article anywhere in the briefing, you MUST include its URL as a markdown hyperlink in the format: [Article Title](URL). Never reference an article by name without linking it.

{article_text}

---

Produce a professional morning briefing using EXACTLY the following structure. Use clean markdown formatting throughout.

Every section that contains a summary, observation, or analysis must include TWO additional labeled lines after the main content:
- **Parcion Relevance:** How this specifically affects Parcion's client base — post-liquidity and pre-liquidity business owners, UHNW families. What should Zack be doing or saying as a result?
- **Plain English:** A single sentence that explains the core idea as if speaking to a smart but non-financial friend. No jargon.

# Morning Briefing — {today}

---

## Macro Themes & Notable Events
[Write 2–3 focused paragraphs. What are the dominant themes across today's news? What is the overall market, macro, and geopolitical environment? Reference specific stories and link them inline. What should a UHNW advisor be watching closely this week?]

**Parcion Relevance:** [How do today's macro themes affect Parcion clients — portfolio positioning, deal timing, estate planning windows, client psychology? What's the one thing Zack should raise on calls this week?]

**Plain English:** [One sentence. What is actually happening in the world right now, in terms anyone could understand?]

---

## Secular Trends Worth Watching
- [Trend 1: A longer-term structural trend visible in today's news. Link any relevant articles inline. Include the implication for UHNW clients.]
  - **Parcion Relevance:** [Specific impact on Parcion's client base or practice.]
  - **Plain English:** [One plain-language sentence.]

- [Trend 2: Same format.]
  - **Parcion Relevance:** [...]
  - **Plain English:** [...]

- [Trend 3: Same format.]
  - **Parcion Relevance:** [...]
  - **Plain English:** [...]

---

## Client Conversation Starters
This section is the most important in the briefing. Zack's clients are $20M+ individuals — most are former business owners sitting on significant liquidity, or owners still running their business who are thinking about an eventual exit. They are not passive investors. They want their advisor to bring them sharp, timely ideas and ask them questions nobody else is asking.

Provide 6–8 specific conversation starters, each tied directly to a story from today's news. For each one:

**[Conversation topic — 5 words or less]**
- **The hook:** One sentence Zack could actually say to open the conversation — natural, not salesy.
- **Why now:** What in today's news makes this timely? Link the relevant article.
- **Who to call:** What type of client is this most relevant for? (e.g. "post-liquidity client reinvesting proceeds", "business owner in manufacturing with $30M+ EBITDA", "client with large unrealized gains in a concentrated position")
- **Where it goes:** If the client engages, what's the planning idea, product, or next step? Be specific — name the technique, structure, or vehicle (e.g. GRAT, IDGT, QOZ, CRT, installment sale, Roth conversion, separately managed account, private credit allocation, etc.)
- **Parcion Relevance:** One sentence on why this matters specifically to Parcion's practice and client base.
- **Plain English:** One sentence version of the whole idea, zero jargon.

---

## Articles Worth Forwarding to Clients
Identify 3–5 articles from today's feed that are genuinely worth forwarding to a client or prospect. For each:

**[Article Title](URL)**
- **Send to:** What type of client — be specific about their situation, industry, or life stage.
- **Why it's worth sending:** One sentence on what makes this relevant or timely for that client.
- **Suggested note:** A 1–2 sentence email or text Zack could send alongside the article. Conversational, not formal.

---

## Actionable Ideas for Your Practice This Week
- [Idea 1: A planning window, deadline, or opportunity to exploit. Name the technique and why now.]
- [Idea 2: A prospect angle for a business-owner client — what question to ask, what scenario to model.]
- [Idea 3: A research task or concept to get sharp on before a client meeting, based on today's news.]
- [Idea 4: A positioning or relationship move — a note to send, an event to reference, a topic for the next quarterly review.]
- [Idea 5: Any other timely idea directly tied to today's headlines.]

---

## Today's Headlines by Category

### Markets & Investing

[List 4–5 stories in this format:]
**[Full Headline](URL) — [Source]**
[2–3 sentence summary of what happened and why it matters.]
**Parcion Relevance:** [One sentence: how does this affect a Parcion client's portfolio, planning, or psychology?]
**Plain English:** [One sentence anyone would understand.]
*Advisor angle:* [One sentence: what should Zack say or do?]

### Geopolitics & Macro

[Same format, 4–5 stories.]

### Estate & Tax Planning

[Same format, 4–5 stories.]

### Selling a Business / M&A

[Same format, 4–5 stories.]

### Wealth Management & Family Office

[Same format, 4–5 stories.]

---

Non-negotiable rules:
- Every article reference anywhere in the briefing MUST be a working markdown hyperlink using the URL provided. No exceptions.
- Assume CFA-level financial literacy for the main analysis. No definitions, no hand-holding in the core content.
- The Plain English lines are the ONE exception — genuinely simple, like texting a smart friend who doesn't follow markets.
- Be specific. Reference actual numbers, company names, and events where available.
- Zero filler phrases ("it is worth noting", "this highlights the importance of", etc.)
- Parcion Relevance must always be specific to post-liquidity or pre-liquidity business owners and UHNW families — never generic retail investor advice.
- The Client Conversation Starters must be tied to today's specific headlines — not generic best practices. The goal is that Zack could pick up the phone and use these today.
- Plain English lines: one sentence, under 30 words, zero jargon.
- Suggested forwarding notes must sound like Zack wrote them — brief, warm, direct. Not like a newsletter."""


# ─── Call Claude API ───────────────────────────────────────────────────────────
def synthesize_with_claude(articles_by_topic):
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt  = build_prompt(articles_by_topic)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ─── Format & Send Email ───────────────────
