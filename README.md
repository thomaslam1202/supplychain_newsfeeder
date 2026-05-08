# Supply Chain News Digest

An automated daily email briefing that monitors Supply Chain Dive, summarizes the top 5 articles using an LLM, and delivers a clean digest to subscribers every morning — fully automated via GitHub Actions with zero manual effort after setup.

---

## The Problem

Supply chain professionals need to stay current with industry news — disruptions, policy changes, logistics trends — but don't have time to read through multiple sources every day. Manually checking news sites and forwarding articles to a team is time-consuming and inconsistent.

---

## What It Does

- Fetches the **top 5 articles of the day** from Supply Chain Dive via RSS feed every morning
- Summarizes each article using **Llama 3.1 Versatile** (via Groq API) into a concise, readable paragraph
- Formats a clean daily digest email with article titles, summaries, and source links
- Delivers the digest automatically to subscribers via **SMTP email**
- Runs on a **scheduled GitHub Actions workflow** — no server required, no manual triggering

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python |
| News Source | Supply Chain Dive (RSS Feed) |
| LLM | Llama 3.1 Versatile (Groq API) |
| Email Delivery | SMTP (Gmail) |
| Scheduler | GitHub Actions (cron schedule) |
| RSS Parsing | feedparser |

---

## How It Works

```
GitHub Actions (runs daily at 7:00 AM)
     │
     ▼
RSS Feed Fetch
(Supply Chain Dive → top 5 articles of the day)
     │
     ▼
LLM Summarization
(each article body → Llama 3.1 → concise summary paragraph)
     │
     ▼
Email Formatting
(title + summary + source link for each article)
     │
     ▼
SMTP Delivery
(sent to all subscribers)
