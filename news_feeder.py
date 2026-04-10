import feedparser
import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Only emails from this address will trigger the on-demand flow
TRUSTED_SENDER = 'chunting.lam@kautex.com'  # e.g. "yourname@gmail.com"

supplyChianDive_rss_url = 'https://rss.app/feeds/6MAe8sojZPLiftsC.xml'

recipients = [
    'chunting.lam@kautex.com',
    'joshua.baker@kautex.com',
    'quinn.labay@kautex.com',
    'adrian.jimenez@kautex.com',
]

# ─────────────────────────────────────────────
# SHARED UTILITIES
# ─────────────────────────────────────────────

from newspaper import Article, Config
from groq import Groq
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

client = Groq(api_key=GROQ_API_KEY)
today_date = datetime.today().date()

# Common tracking/session params that should be stripped before fetching
_TRACKING_PARAMS = {
    "msockid", "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referer",
    "_hsenc", "_hsmi", "hsctaTracking", "mkt_tok", "yclid",
}

# Mimic a real browser so sites like Fox News don't block the request
NEWSPAPER_CONFIG = Config()
NEWSPAPER_CONFIG.browser_user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
NEWSPAPER_CONFIG.request_timeout = 15


def clean_url(url: str) -> str:
    """Strip known tracking query parameters from a URL."""
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
    try:
        article = Article(url, config=NEWSPAPER_CONFIG)
        article.download()
        article.parse()
        if not article.text:
            print(f"  ⚠️ Parsed empty body for {url}")
            return None
        return article
    except Exception as e:
        print(f"  ⚠️ Failed to fetch {url}: {e}")
        return None


def clean_article_text(text, max_chars=6000):
    text = text.replace("\n", " ")
    text = " ".join(text.split())
    return text[:max_chars]


# URL substrings common in ad networks, trackers, and CDN resize/placeholder images
_AD_IMAGE_PATTERNS = [
    "doubleclick", "googlesyndication", "adservice", "adnxs", "moatads",
    "scorecardresearch", "pixel.", "/pixel?", "beacon.", "tracking.",
    "taboola", "outbrain", "revcontent", "mgid", "sharethrough",
    "placeholder", "blank.gif", "spacer.gif", "1x1", "transparent.png",
    "/ads/", "/ad/", "advertisement",
]

# Image file extensions that won't render reliably in email clients
_BAD_EXTENSIONS = (".webp", ".svg", ".gif")


def _is_valid_image_url(img_url: str) -> bool:
    """Return False if the image URL looks like an ad, tracker, or unsupported format."""
    if not img_url:
        return False
    lower = img_url.lower()
    if any(lower.endswith(ext) for ext in _BAD_EXTENSIONS):
        return False
    if any(pattern in lower for pattern in _AD_IMAGE_PATTERNS):
        return False
    return True


def extract_best_image(article_obj, rss_image: str = "") -> str:
    """
    Pick the best hero image for an article in priority order:
      1. og:image / twitter:image meta tag  — explicitly set by the publisher
      2. RSS feed image (if valid)
      3. newspaper3k top_image              — heuristic fallback
      4. Empty string                       — no usable image found

    Each candidate is checked by _is_valid_image_url before being accepted.
    """
    candidates = []

    # 1. Open Graph / Twitter Card (most trustworthy — set by the publisher)
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

    # 2. RSS feed image
    if rss_image:
        candidates.append(("rss feed", rss_image))

    # 3. newspaper3k heuristic top_image (least reliable — last resort)
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


def summarize_with_llama(article_text):
    completion = client.chat.completions.create(
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


def generate_email_html(articles_with_summaries):
    html = """
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; background-color: #f4f7f6; padding: 20px;">
        <h2 style="color: #2c3e50; text-align: center;">Today Articles</h2>
    """

    for art in articles_with_summaries:
        summary_text = art['summary'].replace('*', '•').replace('\n', '<br>')
        title = art['title']
        date = art['date']
        image = art['image']
        link = art['link']

        html += (
            '<div style="background-color: white; border-radius: 8px; overflow: hidden; margin-bottom: 25px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border: 1px solid #ddd;">'
            + (f'<img src="{image}" style="width: 100%; height: 200px; object-fit: cover;" alt="Article Image">' if image else '')
            + '<div style="padding: 20px;">'
            + f'<h3 style="margin-top: 0; color: #2980b9;">{title}</h3>'
            + f'<p style="font-size: 12px; color: #7f8c8d; margin-bottom: 15px;">Published: {date}</p>'
            + '<div style="font-size: 14px; line-height: 1.6; color: #34495e;">'
            + f'{summary_text}'
            + '</div>'
            + '<div style="margin-top: 20px;">'
            + f'<a href="{link}" style="background-color: #2980b9; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">Read Full Article</a>'
            + '</div>'
            + '</div>'
            + '</div>'
        )

    html += "</div>"
    return html


import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_gmail_newsletter(html_content, recipient_list, subject=None):
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
            "link": entry.link,
            "date": date_str,
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
            "link": item["link"],
            "text": cleaned_text,
            "date": item["date"],
            "image": img_url,
        })

    final_result = []
    for article in processed_articles:
        summary = summarize_with_llama(article["text"])
        article['summary'] = summary
        final_result.append(article)

    email_body = generate_email_html(final_result)

    with open("preview.html", "w") as f:
        f.write(email_body)

    send_gmail_newsletter(email_body, 'chunting.lam@kautex.com')


# ─────────────────────────────────────────────
# ON-DEMAND EMAIL TRIGGER FLOW
# ─────────────────────────────────────────────


# Domains that are almost never article sources — typically appear in signatures
_SIGNATURE_DOMAIN_BLOCKLIST = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "youtube.com", "tiktok.com",
    "maps.google.com", "goo.gl", "bit.ly", "t.co",
    "mailto:",  # not a domain but catches malformed hits
}


def _is_article_url(url: str) -> bool:
    """
    Return True only if the URL looks like a news/blog article rather than
    a homepage, social-media profile, or generic signature link.

    Heuristics (all must pass):
      1. Uses http/https scheme.
      2. Domain is not in the signature blocklist.
      3. Has a non-trivial path  — at least two path segments with real words,
         e.g.  /politics/trump-targets-drug-imports   ✓
               /                                       ✗
               /about                                  ✗  (single short segment)
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Must be http/https
    if parsed.scheme not in ("http", "https"):
        return False

    # Strip www. for blocklist matching
    domain = parsed.netloc.lower().lstrip("www.")
    if any(domain == blocked or domain.endswith("." + blocked)
           for blocked in _SIGNATURE_DOMAIN_BLOCKLIST):
        return False

    # Path must have at least two non-empty segments, each with 3+ characters
    # e.g.  /section/article-slug-here  → ['section', 'article-slug-here'] ✓
    #        /                           → [] ✗
    #        /about                      → ['about'] ✗  (only one segment)
    path_segments = [s for s in parsed.path.strip("/").split("/") if len(s) >= 3]
    if len(path_segments) < 2:
        return False

    return True


def extract_urls_from_text(text: str):
    """
    Return URLs from email body that look like real articles,
    filtering out homepage links, social profiles, and signature URLs.
    """
    raw_urls = re.findall(r'https?://[^\s<>"\')\]]+', text)
    article_urls = [u for u in raw_urls if _is_article_url(u)]

    if not article_urls:
        # Fallback: return all URLs so the caller can log a useful warning
        print("  ⚠️  No article-like URLs found after filtering. Raw URLs were:")
        for u in raw_urls:
            print(f"      {u}")

    return article_urls


def get_email_body(msg):
    """Extract plain-text or HTML body from an email.Message object."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            if content_type == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                body += part.get_payload(decode=True).decode(charset, errors="replace")
            elif content_type == "text/html" and not body:
                # Fallback to HTML body only if no plain text found
                charset = part.get_content_charset() or "utf-8"
                body += part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="replace")
    return body


def fetch_trigger_emails():
    """
    Connect to Gmail via IMAP, find unread emails from TRUSTED_SENDER
    with subject 'send' (case-insensitive), and return a list of URL lists.
    Marks processed emails as read so they are not re-processed.
    """
    if not TRUSTED_SENDER:
        print("⚠️ TRUSTED_SENDER not set in .env — skipping inbox check.")
        return []

    trigger_url_batches = []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("inbox")

        # Search for unread emails from the trusted sender
        search_criteria = f'(UNSEEN FROM "{TRUSTED_SENDER}" SUBJECT "send")'
        status, data = mail.search(None, search_criteria)

        if status != "OK" or not data[0]:
            print("📭 No trigger emails found.")
            mail.logout()
            return []

        email_ids = data[0].split()
        print(f"📬 Found {len(email_ids)} trigger email(s).")

        for eid in email_ids:
            # Fetch the full email
            _, msg_data = mail.fetch(eid, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            body = get_email_body(msg)
            urls = extract_urls_from_text(body)

            if urls:
                print(f"  🔗 Extracted URLs: {urls}")
                trigger_url_batches.append(urls)
            else:
                print(f"  ⚠️ No URLs found in trigger email — skipping.")

            # Mark email as read so it won't be processed again
            mail.store(eid, "+FLAGS", "\\Seen")

        mail.logout()

    except Exception as e:
        print(f"❌ IMAP error: {e}")

    return trigger_url_batches


def process_url_batch(urls):
    """Fetch, summarize, and email articles for a list of URLs."""
    processed_articles = []

    for url in urls:
        print(f"  📄 Fetching: {url}")
        parsed = get_article_text(url)
        if not parsed:
            print(f"  ⚠️ Skipping {url} — could not parse.")
            continue

        # Title
        title = parsed.title or "Untitled Article"

        # Published date — newspaper3k exposes publish_date
        pub_date = "Unknown date"
        if parsed.publish_date:
            pub_date = parsed.publish_date.strftime("%B %d, %Y")

        # Body text
        cleaned_text = clean_article_text(parsed.text)
        if not cleaned_text:
            print(f"  ⚠️ Skipping {url} — empty body.")
            continue

        # Image
        image = extract_best_image(parsed)

        processed_articles.append({
            "title": title,
            "link": url,
            "text": cleaned_text,
            "date": pub_date,
            "image": image,
        })

    if not processed_articles:
        print("  ⚠️ No valid articles to summarize.")
        return

    # Summarize
    for article in processed_articles:
        article['summary'] = summarize_with_llama(article["text"])

    email_body = generate_email_html(processed_articles)
    subject = f"[{today_date}] On-Demand Supply Chain Summary"
    send_gmail_newsletter(email_body, 'chunting.lam@kautex.com', subject=subject)


def run_on_demand_trigger():
    """Check inbox for trigger emails and process any found URL batches."""
    print("📥 Checking inbox for on-demand trigger emails...")
    url_batches = fetch_trigger_emails()

    for i, urls in enumerate(url_batches, start=1):
        print(f"\n🔄 Processing batch {i}/{len(url_batches)} ({len(urls)} URL(s))...")
        process_url_batch(urls)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # 1. Always run the daily RSS digest
    run_daily_rss_feed()

    # 2. Also check for any on-demand trigger emails
    run_on_demand_trigger()
