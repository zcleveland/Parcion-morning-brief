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

    article_text = ""
    for topic, articles in articles_by_topic.items():
        article_text += f"\n\n### {topic}\n"
        if not articles:
            article_text += "_No articles retrieved for this category._\n"
            continue
        for i, a in enumerate(articles, 1):
            article_text += f"{i}. **{a['title']}**\n   {a['summary']}\n\n"

    return f"""You are preparing a daily morning intelligence briefing for Zack Cleveland, a senior wealth advisor at Parcion Private Wealth. Zack serves ultra-high-net-worth families (typically $10M–$500M+ in investable assets). A significant portion of his clients and prospects are business owners who are actively considering or planning a sale of their business within the next 1–5 years. Zack holds a CFA charter and has deep expertise in portfolio management, estate planning, tax planning, and UHNW advisory. He needs to be the most informed person in the room.

Today is {today}.

Below are today's top articles across five key categories:
{article_text}

---

Produce a professional morning briefing using EXACTLY the following structure. Use clean markdown formatting throughout.

Every section that contains a summary, observation, or analysis must include TWO additional labeled lines after the main content:
- **Parcion Relevance:** How this specifically affects Parcion's client base — business owners, UHNW families, and pre-liquidity prospects. What should Zack be doing or saying as a result?
- **Plain English:** A single sentence that explains the core idea as if speaking to a smart but non-financial friend. No jargon.

# Morning Briefing — {today}

---

## Macro Themes & Notable Events
[Write 2–3 focused paragraphs. What are the dominant themes across today's news? What is the overall market, macro, and geopolitical environment? What crosscurrents or confluences stand out? Reference specific stories — do not be generic. What should a UHNW advisor be watching closely this week?]

**Parcion Relevance:** [How do today's macro themes specifically affect Parcion clients — portfolio positioning, deal timing, estate planning windows, client psychology? What's the one thing Zack should be bringing up in calls this week?]

**Plain English:** [One sentence. What is actually happening in the world right now, in terms anyone could understand?]

---

## Secular Trends Worth Watching
- [Trend 1: A longer-term structural trend visible in today's news — e.g., estate tax exemption trajectory, M&A cycle stage, rate regime implications, geopolitical realignment. Include the implication for UHNW clients.]
  - **Parcion Relevance:** [Specific impact on Parcion's client base or practice.]
  - **Plain English:** [One plain-language sentence.]

- [Trend 2: Same format.]
  - **Parcion Relevance:** [...]
  - **Plain English:** [...]

- [Trend 3: Same format.]
  - **Parcion Relevance:** [...]
  - **Plain English:** [...]

---

## Actionable Ideas for Your Practice This Week
- [Idea 1: A specific client conversation to initiate, tied directly to a story above. Name the type of client and what to say.]
- [Idea 2: A planning window, deadline, or opportunity to exploit. Be specific about timing and technique — e.g., GRAT, IDGT, installment sale, Roth conversion, etc.]
- [Idea 3: A prospect angle for a business-owner client — what question to ask, what scenario to model, what concern to surface.]
- [Idea 4: A research task or concept to get sharp on before a client meeting, based on today's news.]
- [Idea 5: A positioning or relationship move — a note to send, an article to forward, a topic to add to the next quarterly review agenda.]

---

## Today's Headlines by Category

### Markets & Investing

[List 4–5 stories in this format:]
**[Full Headline] — [Source]**
[2–3 sentence summary of what happened and why it matters to markets or the economy.]
**Parcion Relevance:** [One specific sentence: how does this affect a Parcion client's portfolio, planning, or psychology?]
**Plain English:** [One sentence a non-finance person would immediately understand.]
*Advisor angle:* [One specific sentence: what should Zack say or do as a result of this story?]

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
- Assume CFA-level financial literacy for the main analysis. No definitions, no hand-holding in the core content.
- The Plain English lines are the ONE exception — these must be genuinely simple. Write them as if texting a smart friend who doesn't follow markets.
- Be specific. Reference actual numbers, company names, and events where available.
- Every sentence must carry information. Zero filler phrases ("it is worth noting", "this highlights the importance of", etc.)
- Parcion Relevance must always be specific to UHNW families and business-owner clients — never generic retail investor advice.
- The actionable ideas must be directly tied to today's specific headlines — not generic best practices.
- Plain English lines should be one sentence, under 30 words, zero jargon."""


# ─── Call Claude API ───────────────────────────────────────────────────────────
def synthesize_with_claude(articles_by_topic):
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt  = build_prompt(articles_by_topic)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ─── Format & Send Email ───────────────────────────────────────────────────────
def send_email(briefing_md):
    today   = datetime.now().strftime('%B %d, %Y')
    subject = f"Morning Briefing — {today}"

    # Convert markdown to HTML
    html_body = markdown.markdown(briefing_md, extensions=['extra'])

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Georgia, serif;
    max-width: 680px; margin: 0 auto; padding: 24px 28px;
    color: #1a1a1a; line-height: 1.7; font-size: 15px; background: #ffffff;
  }}
  h1 {{
    color: #0d2b4e; font-size: 22px; margin-bottom: 4px;
    border-bottom: 3px solid #0d2b4e; padding-bottom: 12px;
  }}
  h2 {{
    color: #0d2b4e; font-size: 16px; font-weight: 700;
    margin-top: 32px; margin-bottom: 8px;
    border-left: 4px solid #0d2b4e; padding-left: 10px;
  }}
  h3 {{
    color: #2c5282; font-size: 13px; font-weight: 700;
    margin-top: 20px; margin-bottom: 6px;
    text-transform: uppercase; letter-spacing: 0.06em;
  }}
  p {{ margin: 6px 0 12px 0; }}
  ul {{ margin: 6px 0 14px 0; padding-left: 20px; }}
  li {{ margin-bottom: 8px; }}
  strong {{ color: #0d2b4e; }}
  em {{ color: #555; font-style: italic; }}
  hr {{ border: none; border-top: 1px solid #dde3ea; margin: 26px 0; }}
  .footer {{
    margin-top: 40px; font-size: 11px; color: #aaa;
    border-top: 1px solid #eee; padding-top: 14px;
  }}
</style>
</head>
<body>
{html_body}
<div class="footer">
  Parcion Private Wealth &nbsp;·&nbsp; Morning Briefing &nbsp;·&nbsp; {today}<br>
  Sources: Curated RSS Feeds &nbsp;·&nbsp; Synthesized by Claude Sonnet
</div>
</body>
</html>"""

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
    print(f"  Morning Briefing · {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'─' * 54}")

    print("\n[1/3] Fetching news articles...")
    articles = compile_articles()
    total    = sum(len(v) for v in articles.values())
    print(f"      {total} articles across {len(articles)} categories")

    print("\n[2/3] Synthesizing with Claude...")
    briefing = synthesize_with_claude(articles)
    print(f"      Briefing ready ({len(briefing):,} characters)")

    print("\n[3/3] Sending email...")
    send_email(briefing)

    print("\n  All done.\n")


if __name__ == "__main__":
    main()
