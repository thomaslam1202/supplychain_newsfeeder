import feedparser
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()  

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

supplyChianDive_rss_url = 'https://rss.app/feeds/6MAe8sojZPLiftsC.xml'

feed = feedparser.parse(supplyChianDive_rss_url)
selected_articles = []
today_date = datetime.today().date()

for entry in feed.entries:
    # Safely try to get a human-readable date string
    date_str = getattr(entry, 'published', 'No date provided')

    selected_articles.append({
        "title": entry.title,
        "link": entry.link,
        "date": date_str,
        "image": entry.get('media_content', [{}])[0].get('url', ''),
    })

    # Stop once we have the top 5
    if len(selected_articles) >= 5:
        break

from newspaper import Article

def get_article_text(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        print(f"⚠️ Failed to fetch {url}: {e}")
        return ""


def clean_article_text(text, max_chars=6000):
    text = text.replace("\n", " ")
    text = " ".join(text.split())
    return text[:max_chars]

def get_better_image(url, rss_image):
    try:
        # If RSS image is webp, try to find the 'top_image' from the site
        if ".webp" in rss_image.lower():
            a = Article(url)
            a.download()
            a.parse()
            # Only use it if it's not another webp
            if a.top_image and ".webp" not in a.top_image.lower():
                return a.top_image
        return rss_image
    except:
        return rss_image

processed_article = []


for item in selected_articles:
    raw_text = get_article_text(item["link"])
    cleaned_text = clean_article_text(raw_text)
    img_url = get_better_image(item['link'], item['image'])

    processed_article.append({
        "title": item["title"],
        "link": item["link"],
        "text": cleaned_text,
        "date": item["date"],
        "image": img_url
    })

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
            f'<img src="{image}" style="width: 100%; height: 200px; object-fit: cover;" alt="Article Image">'
            '<div style="padding: 20px;">'
            f'<h3 style="margin-top: 0; color: #2980b9;">{title}</h3>'
            f'<p style="font-size: 12px; color: #7f8c8d; margin-bottom: 15px;">Published: {date}</p>'
            '<div style="font-size: 14px; line-height: 1.6; color: #34495e;">'
            f'{summary_text}'
            '</div>'
            '<div style="margin-top: 20px;">'
            f'<a href="{link}" style="background-color: #2980b9; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">Read Full Article</a>'
            '</div>'
            '</div>'
            '</div>'
        )

    html += "</div>"
    return html

from groq import Groq

client = Groq(api_key=GROQ_API_KEY)

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
      ], temperature=0.5, max_tokens=1000
  )
  return completion.choices[0].message.content

final_result = []


for article in processed_article:
  summary = summarize_with_llama(article["text"])
  article['summary'] = summary
  final_result.append(article)

email_body = generate_email_html(final_result)

# Optional: Save it to an .html file to preview it in your browser
with open("preview.html", "w") as f:
    f.write(email_body)

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

recipients = ['chunting.lam@kautex.com','joshua.baker@kautex.com', 'quinn.labay@kautex.com','adrian.jimenez@kautex.com']

def send_gmail_newsletter(html_content, recipient_list):

    sender_email = GMAIL_ADDRESS
    app_password = GMAIL_APP_PASSWORD
    subject = f"[{today_date}] Daily Supply Chain Feed"

    msg = MIMEMultipart()
    msg['From'] = f"DO NOT REPLY <{sender_email}>"
    msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html'))

    try:
        # Port 465 is the standard for secure Gmail SMTP
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, app_password)
            # This sends the email to all 5 people at once
            server.sendmail(sender_email, recipient_list, msg.as_string())
        print("🚀 Gmail Newsletter sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send via Gmail: {e}")

send_gmail_newsletter(email_body, "joshua.baker@kautex.com")
