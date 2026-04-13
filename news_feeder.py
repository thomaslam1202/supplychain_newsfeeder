import feedparser
import os
import re
import imaplib
import email
import time
from email.header import decode_header
from datetime import datetime
from dotenv import load_dotenv
from groq import RateLimitError

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GMAIL_ADDRESS = 'supplychaindigest.daily@gmail.com'
GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')

# ── Now supports multiple trusted senders ──────────────────────────────────
TRUSTED_SENDERS = [
    'chunting.lam@kautex.com','joshua.baker@kautex.com','adrian.jimenez@kautex.com','Tony.Kizzee@kautex.com'
]
#only trigger if sender in trusted_senders with subjet = 'send' and has new url in email body


supplyChianDive_rss_url = 'https://www.supplychaindive.com/feeds/news/'
 
recipients = [
    'chunting.lam@kautex.com',
    'joshua.baker@kautex.com',
    'quinn.labay@kautex.com',
    'adrian.jimenez@kautex.com',
]
 
from newspaper import Article, Config
from groq import Groq
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
 
client = Groq(api_key=GROQ_API_KEY)

 
_TRACKING_PARAMS = {
    "msockid", "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referer",
    "_hsenc", "_hsmi", "hsctaTracking", "mkt_tok", "yclid",
}
 
NEWSPAPER_CONFIG = Config()
NEWSPAPER_CONFIG.browser_user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
NEWSPAPER_CONFIG.request_timeout = 15
 
 
def clean_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
    clean_query = urlencode(filtered, doseq=True)
    cleaned = urlunparse(parsed._replace(query=clean_query))
    if cleaned != url:
        print(f"  🧹 Stripped tracking params: {url} → {cleaned}")
    return cleaned
 
 
def get_article_text(url):
    url = clean_url(url)
 
    # Attempt 1: newspaper3k
    try:
        article = Article(url, config=NEWSPAPER_CONFIG)
        article.download()
        article.parse()
        if article.text:
            return article
        print(f"  ⚠️ newspaper3k returned empty body for {url} — trying fallback")
    except Exception as e:
        print(f"  ⚠️ newspaper3k failed for {url}: {e} — trying fallback")
 
    # Attempt 2: requests + BeautifulSoup (handles 401/403 paywalled/strict sites)
    try:
        import requests
        from bs4 import BeautifulSoup
 
        headers = {
            "User-Agent": NEWSPAPER_CONFIG.browser_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
 
        class _ArticleMock:
            pass
 
        mock = _ArticleMock()
 
        # Title
        og_title = soup.find("meta", property="og:title")
        mock.title = (
            (og_title["content"] if og_title else None)
            or (soup.find("h1").get_text(strip=True) if soup.find("h1") else url)
        )
 
        # Published date — resolved later by extract_publish_date()
        mock.publish_date = None
        mock._soup = soup  # stash for date extraction
 
        # og:image / twitter:image
        og_img = (
            soup.find("meta", property="og:image")
            or soup.find("meta", attrs={"name": "twitter:image"})
        )
        mock.meta_img  = og_img["content"] if og_img else ""
        mock.meta_data = {}
        mock.top_image = mock.meta_img
 
        # Body text
        article_tag = (
            soup.find("article")
            or soup.find("div", class_=lambda c: c and "article-body" in c.lower())
            or soup.find("div", class_=lambda c: c and "story-body"   in c.lower())
            or soup.find("main")
            or soup.body
        )
        paragraphs = article_tag.find_all("p") if article_tag else soup.find_all("p")
        mock.text = " ".join(p.get_text(separator=" ", strip=True) for p in paragraphs)
 
        if not mock.text:
            print(f"  ⚠️ Fallback also returned empty body for {url}")
            return None
 
        print(f"  ✅ Fetched via requests+bs4 fallback: {url}")
        return mock
 
    except Exception as e:
        print(f"  ⚠️ Fallback also failed for {url}: {e}")
        return None
 
 
def clean_article_text(text, max_chars=6000):
    text = text.replace("\n", " ")
    text = " ".join(text.split())
    return text[:max_chars]
 
 
_AD_IMAGE_PATTERNS = [
    "doubleclick", "googlesyndication", "adservice", "adnxs", "moatads",
    "scorecardresearch", "pixel.", "/pixel?", "beacon.", "tracking.",
    "taboola", "outbrain", "revcontent", "mgid", "sharethrough",
    "placeholder", "blank.gif", "spacer.gif", "1x1", "transparent.png",
    "/ads/", "/ad/", "advertisement",
]
_BAD_EXTENSIONS = (".svg", ".gif")
 
 
def _is_valid_image_url(img_url: str) -> bool:
    if not img_url:
        return False
    lower = img_url.lower()
    if any(lower.endswith(ext) for ext in _BAD_EXTENSIONS):
        return False
    if any(pattern in lower for pattern in _AD_IMAGE_PATTERNS):
        return False
    return True
 
 
def extract_best_image(article_obj, rss_image: str = "") -> str:
    candidates = []
    if article_obj:
        meta_img = getattr(article_obj, "meta_img", "") or ""
        if meta_img:
            candidates.append(("og/twitter meta", meta_img))
        meta_data = getattr(article_obj, "meta_data", {})
        og_image = (
            meta_data.get("og", {}).get("image", "")
            or meta_data.get("twitter", {}).get("image", "")
        )
        if og_image and isinstance(og_image, str):
            candidates.append(("og meta_data", og_image))
    if rss_image:
        candidates.append(("rss feed", rss_image))
    if article_obj:
        top_img = getattr(article_obj, "top_image", "") or ""
        if top_img:
            candidates.append(("top_image heuristic", top_img))
 
    for source, url in candidates:
        if _is_valid_image_url(url):
            print(f"  🖼️  Image selected from [{source}]: {url}")
            return url
 
    print("  🖼️  No valid image found — article will render without one.")
    return ""
 
 
_SOURCE_NAMES = {
    "supplychaindive.com":  "Supply Chain Dive",
    "foxnews.com":          "Fox News",
    "wsj.com":              "Wall Street Journal",
    "bloomberg.com":        "Bloomberg",
    "reuters.com":          "Reuters",
    "politico.com":         "Politico",
    "cnbc.com":             "CNBC",
    "ft.com":               "Financial Times",
    "nytimes.com":          "The New York Times",
    "washingtonpost.com":   "The Washington Post",
    "thehill.com":          "The Hill",
    "fortune.com":          "Fortune",
    "forbes.com":           "Forbes",
    "bbc.com":              "BBC News",
    "bbc.co.uk":            "BBC News",
    "businessinsider.com":  "Business Insider",
    "logisticsmgmt.com":    "Logistics Management",
    "dcvelocity.com":       "DC Velocity",
    "freightwaves.com":     "FreightWaves",
    "joc.com":              "Journal of Commerce",
    "apnews.com":           "Associated Press",
    "axios.com":            "Axios",
}
 
 
def get_source_info(url: str) -> tuple:
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ("Unknown Source", "")
    name = _SOURCE_NAMES.get(domain)
    if not name:
        stem = domain.split(".")[0]
        name = " ".join(w.capitalize() for w in re.split(r"[-_]", stem))
    favicon = f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
    return (name, favicon)
 
 
def summarize_with_llama(article_text, retries =3):
    for attemp in range(retries):
        try: completion = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": """You are a professional Supply Chain Analyst.
                        Summarize the article into 3 bullet points.
                        
                        RULES:
                        1. Output ONLY the bullet points.
                        2. NO introduction (do not say "Here are the points").
                        3. NO markdown bolding (do not use **).
                        4. Start each point with a relevant Emoji icon based on the topic.
                        5. Focus on business impact, risks, and trends
                        6. Format the [Topic Title] and [Key Figures/Words] using <b> tags
                        
                        Example format:
                        📦 <b>Market Volatility</b>: Spot rates have surged by <b>52%</b> this quarter.
                        ⚠️ <b>Labor Risks</b>: Negotiations could impact <b>20,000 workers</b>.
                        📈 <b>Strategic Trends</b>: Shippers are moving toward <b>short-term contracts</b>."""},
                                    {"role": "user", "content": article_text}
                                ],
                                temperature=0.5,
                                max_tokens=1000
                            )
                return completion.choices[0].message.content
        except RateLimitError:
            print("Reached rate limit - waiting 60s")
            time.sleep(60)
    return print("Summary unavailable")
 
def _try_parse(raw: str):
    """Parse a raw date string into a datetime, or return None."""
    if not raw:
        return None
    try:
        from dateutil import parser as dp
        return dp.parse(raw.strip(), ignoretz=True)
    except Exception:
        return None
 
 
def extract_publish_date(article_obj, url: str = "") -> str:
    from dateutil import parser as dp
    import json, re as _re
 
    dt = None
 
    # ── 1. newspaper3k ────────────────────────────────────────────────────
    native = getattr(article_obj, "publish_date", None)
    if native:
        return native.strftime("%B %d, %Y")
 
    soup = None
    stashed = getattr(article_obj, "_soup", None)
    if stashed is not None:
        soup = stashed
    else:
        raw_html = getattr(article_obj, "html", "") or ""
        if raw_html:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(raw_html, "html.parser")
            except Exception:
                pass
 
    if soup:
        # ── 2. JSON-LD ────────────────────────────────────────────────────
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if "@graph" in item:
                        items += item["@graph"]
                    for key in ("datePublished", "dateCreated", "dateModified"):
                        val = item.get(key)
                        if val:
                            dt = _try_parse(val)
                            if dt:
                                return dt.strftime("%B %d, %Y")
            except Exception:
                pass
 
        # ── 3. Meta tags ────────────────────────────────────────────────────
        meta_selectors = [
            {"property": "article:published_time"},
            {"property": "og:article:published_time"},
            {"name": "publish_date"},
            {"name": "publishdate"},
            {"name": "publication_date"},
            {"name": "date"},
            {"name": "DC.date"},
            {"name": "DC.Date"},
            {"itemprop": "datePublished"},
            {"itemprop": "dateCreated"},
        ]
        for sel in meta_selectors:
            tag = soup.find("meta", attrs=sel)
            if tag:
                raw = tag.get("content") or tag.get("datetime") or ""
                dt = _try_parse(raw)
                if dt:
                    return dt.strftime("%B %d, %Y")
 
        for tag in soup.find_all(attrs={"itemprop": ["datePublished", "dateCreated"]}):
            raw = tag.get("content") or tag.get("datetime") or tag.get_text(strip=True)
            dt = _try_parse(raw)
            if dt:
                return dt.strftime("%B %d, %Y")
 
        # ── 4. <time> elements ────────────────────────────────────────────
        for time_tag in soup.find_all("time"):
            raw = time_tag.get("datetime") or time_tag.get_text(strip=True)
            dt = _try_parse(raw)
            if dt:
                return dt.strftime("%B %d, %Y")
 
    # ── 5. URL path pattern ───────────────────────────────────────────────
    patterns = [
        r'/(\d{4})/(\d{1,2})/(\d{1,2})/',
        r'[/-](\d{4})-(\d{2})-(\d{2})(?:[^0-9]|$)',
        r'(\d{4})(\d{2})(\d{2})',
    ]
    for pat in patterns:
        m = _re.search(pat, url)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                    from datetime import datetime as _dt
                    return _dt(y, mo, d).strftime("%B %d, %Y")
            except Exception:
                pass
 
    return "Unknown date"

def generate_email_html(articles_with_summaries):
    html = (
        "<div style=\"font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;"
        "max-width:600px;margin:auto;background-color:#f4f7f6;padding:20px;\">"
        "<h2 style=\"color:#2c3e50;text-align:center;\">Today's Articles</h2>"
    )
 
    for art in articles_with_summaries:
        summary_text             = art['summary'].replace('*', '•').replace('\n', '<br>')
        title                    = art['title']
        date                     = art['date']
        image                    = art['image']
        link                     = art['link']
        source_name, favicon_url = get_source_info(link)
 
        img_tag = (
            f'<img src="{image}" style="width:100%;height:200px;object-fit:cover;" alt="">'
            if image else ''
        )
 
        if favicon_url:
            source_block = (
                f'<img src="{favicon_url}" width="14" height="14" '
                f'style="border-radius:3px;vertical-align:middle;margin-right:5px;" alt="">'
                f'{source_name}'
            )
        else:
            source_block = source_name
 
        html += (
            '<div style="background-color:white;border-radius:8px;overflow:hidden;'
            'margin-bottom:25px;box-shadow:0 4px 6px rgba(0,0,0,0.1);border:1px solid #ddd;">'
            + img_tag
            + '<div style="padding:20px;">'
            + f'<p style="font-size:11px;color:#95a5a6;margin:0 0 8px 0;'
              f'text-transform:uppercase;letter-spacing:0.6px;">{source_block}</p>'
            + f'<h3 style="margin-top:0;color:#2980b9;">{title}</h3>'
            + f'<p style="font-size:12px;color:#7f8c8d;margin-bottom:15px;">Published: {date}</p>'
            + f'<div style="font-size:14px;line-height:1.6;color:#34495e;">{summary_text}</div>'
            + '<div style="margin-top:20px;">'
            + f'<a href="{link}" style="background-color:#2980b9;color:white;padding:10px 20px;'
              f'text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;">'
              f'Read Full Article</a>'
            + '</div></div></div>'
        )
 
    html += "</div>"
    return html
 
 
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
 
 
def send_gmail_newsletter(html_content, recipient_list, subject=None):
    today_date = date.today()
    sender_email = GMAIL_ADDRESS
    app_password = GMAIL_APP_PASSWORD
    subject = subject or f"[{today_date}] Daily Supply Chain Feed"
 
    msg = MIMEMultipart()
    msg['From'] = f"DO NOT REPLY <{sender_email}>"
    msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html'))
 
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipient_list, msg.as_string())
        print("🚀 Gmail Newsletter sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send via Gmail: {e}")
 
 
# ─────────────────────────────────────────────
# DAILY RSS FLOW
# ─────────────────────────────────────────────
 
def run_daily_rss_feed():
    print("📰 Running daily RSS feed...")
 
    feed = feedparser.parse(supplyChianDive_rss_url)
    selected_articles = []
 
    for entry in feed.entries:
        date_str = getattr(entry, 'published', 'No date provided')
        selected_articles.append({
            "title": entry.title,
            "link":  entry.link,
            "date":  date_str,
            "image": entry.get('media_content', [{}])[0].get('url', ''),
        })
        if len(selected_articles) >= 5:
            break
 
    processed_articles = []
    for item in selected_articles:
        parsed = get_article_text(item["link"])
        if not parsed:
            continue
        cleaned_text = clean_article_text(parsed.text)
        img_url = extract_best_image(parsed, rss_image=item['image'])
        processed_articles.append({
            "title": item["title"],
            "link":  item["link"],
            "text":  cleaned_text,
            "date":  item["date"],
            "image": img_url,
        })
 
    final_result = []
    for article in processed_articles:
        article['summary'] = summarize_with_llama(article["text"])
        final_result.append(article)
 
    email_body = generate_email_html(final_result)
 
    with open("preview.html", "w", encoding="utf-8") as f:
        f.write(email_body)
 
    send_gmail_newsletter(email_body, recipients)
 
 
# ─────────────────────────────────────────────
# ON-DEMAND EMAIL TRIGGER FLOW
# ─────────────────────────────────────────────
 
_SIGNATURE_DOMAIN_BLOCKLIST = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "youtube.com", "tiktok.com",
    "maps.google.com", "goo.gl", "bit.ly", "t.co",
}
 
 
def _is_article_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    domain = parsed.netloc.lower().lstrip("www.")
    if any(domain == b or domain.endswith("." + b) for b in _SIGNATURE_DOMAIN_BLOCKLIST):
        return False
    path_segments = [s for s in parsed.path.strip("/").split("/") if len(s) >= 3]
    return len(path_segments) >= 2
 
 
def extract_urls_from_text(text: str):
    raw_urls = re.findall(r'https?://[^\s<>"\')\]]+', text)
    article_urls = [u for u in raw_urls if _is_article_url(u)]
    if not article_urls:
        print("  ⚠️  No article-like URLs found after filtering. Raw URLs were:")
        for u in raw_urls:
            print(f"      {u}")
    return article_urls
 
 
def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type        = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            if content_type == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                body += part.get_payload(decode=True).decode(charset, errors="replace")
            elif content_type == "text/html" and not body:
                charset = part.get_content_charset() or "utf-8"
                body += part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="replace")
    return body


def fetch_all_trigger_urls() -> list[str]:
    """
    Connects to Gmail once, fetches ALL unseen emails from any TRUSTED_SENDER,
    collects every article URL across all of them (deduped), marks them all as
    read, then returns the flat list of unique URLs.
    """
    if not TRUSTED_SENDERS:
        print("⚠️ TRUSTED_SENDERS is empty — skipping inbox check.")
        return []

    all_urls: list[str] = []
    seen_urls: set[str] = set()

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("inbox")

        for sender in TRUSTED_SENDERS:
            search_criteria = f'(UNSEEN FROM "{sender}" SUBJECT "send")'
            status, data = mail.search(None, search_criteria)

            if status != "OK" or not data[0]:
                print(f"📭 No trigger emails from {sender}.")
                continue

            email_ids = data[0].split()
            print(f"📬 Found {len(email_ids)} trigger email(s) from {sender}.")

            for eid in email_ids:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                raw_email   = msg_data[0][1]
                msg         = email.message_from_bytes(raw_email)

                body = get_email_body(msg)
                urls = extract_urls_from_text(body)

                new_urls = [u for u in urls if u not in seen_urls]
                if new_urls:
                    print(f"  🔗 {len(new_urls)} new URL(s) extracted from email {eid.decode()}.")
                    for u in new_urls:
                        seen_urls.add(u)
                        all_urls.append(u)
                else:
                    print(f"  ⚠️ No new URLs in email {eid.decode()} — skipping.")

                # Mark as read regardless
                mail.store(eid, "+FLAGS", "\\Seen")
    finally:
        try:
            mail.logout()
        except 
            pass
    except Exception as e:
        print(f"❌ IMAP error: {e}")

    return all_urls


def process_url_batch(urls: list[str]):
    """Fetch, summarise, and send ONE combined email for all provided URLs."""
    today_date = date.today()
    processed_articles = []
 
    for url in urls:
        print(f"  📄 Fetching: {url}")
        parsed = get_article_text(url)
        if not parsed:
            print(f"  ⚠️ Skipping {url} — could not parse.")
            continue
 
        title        = parsed.title or "Untitled Article"
        pub_date     = extract_publish_date(parsed, url)
        cleaned_text = clean_article_text(parsed.text)

        if not cleaned_text:
            print(f"  ⚠️ Skipping {url} — empty body.")
            continue
 
        image = extract_best_image(parsed)
        processed_articles.append({
            "title": title,
            "link":  url,
            "text":  cleaned_text,
            "date":  pub_date,
            "image": image,
        })
 
    if not processed_articles:
        print("  ⚠️ No valid articles to summarize — no email sent.")
        return
 
    for article in processed_articles:
        article['summary'] = summarize_with_llama(article["text"])
 
    email_body = generate_email_html(processed_articles)
    subject    = f"[{today_date}] On-Demand Supply Chain Summary"
    print(f"\n📧 Sending one combined email with {len(processed_articles)} article(s)...")
    send_gmail_newsletter(email_body, recipients, subject=subject)


def run_on_demand_trigger():
    """
    Collect ALL URLs from ALL unseen trigger emails across every trusted sender,
    then send exactly ONE combined newsletter email.
    """
    print("📥 Checking inbox for on-demand trigger emails...")
    all_urls = fetch_all_trigger_urls()

    if not all_urls:
        print("📭 No URLs found across any trigger emails — nothing to send.")
        return

    print(f"\n🔄 Processing {len(all_urls)} unique URL(s) from all trigger emails...")
    process_url_batch(all_urls)
 
 
# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
 
if __name__ == "__main__":
    run_daily_rss_feed()
    run_on_demand_trigger()
