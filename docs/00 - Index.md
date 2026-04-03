# PriceGuard — Master Index

> **Brand name:** PriceGuard
> **Domain:** www.dealnotify.co
> **GitHub:** https://github.com/raunak-09/dealnotify
> **Hosted on:** Railway (auto-deploys on push to `main`)
> **Last updated:** April 2026

---

## What is this?

PriceGuard is a SaaS price monitoring web app. Users paste a product URL (Amazon, Walmart, Best Buy, Target, eBay, Costco), set a target price, and get an email the moment the price drops. It runs on a Free tier (3 products, 6-hour check interval) and a Pro tier ($4.99/month or $39.99/year, unlimited products, 2-hour check interval).

---

## Documentation Map

| Note | What it covers |
|------|---------------|
| [[01 - Project Overview]] | Business model, features, file structure, key decisions |
| [[02 - Tech Stack & Architecture]] | Flask, PostgreSQL, Railway, Firecrawl, Stripe, APScheduler |
| [[03 - Database Schema]] | All tables, columns, migrations, relationships |
| [[04 - API Reference]] | Every endpoint — method, auth, request, response |
| [[05 - Frontend & Session Management]] | dashboard.html, index.html, localStorage sessions, toast UI |
| [[06 - Stripe & Payments]] | Checkout flow, webhooks, monthly vs annual, Stripe env vars |
| [[07 - Price Monitoring System]] | Scheduler, tiered intervals, Firecrawl scraping, alert logic |
| [[08 - Environment Variables]] | Every env var, where to get it, Railway setup |
| [[09 - Deployment Guide]] | Railway deploy, custom domain, Cloudflare, HTTPS, git workflow |
| [[10 - Bugs & Fixes]] | Every bug fixed, root cause, solution — for future reference |

---

## Quick Reference

### Giving Claude context
Paste the content of any note below into a new conversation, or say:
> "Read my PriceGuard docs in Obsidian before we start."

### Most important files in the repo
| File | Role |
|------|------|
| `web_app.py` | Entire backend — Flask routes, DB, Stripe, scheduler |
| `dashboard.html` | User dashboard SPA |
| `index.html` | Public landing / pricing / login page |
| `email_alerts.py` | Gmail SMTP email templates |
| `requirements.txt` | Python dependencies |
| `Procfile` | `web: python web_app.py` |

### Live URLs
| URL | Purpose |
|-----|---------|
| https://www.dealnotify.co | Main site |
| https://dealnotify.co | Redirects → www |
| https://www.dealnotify.co/dashboard | User dashboard (needs token) |
| https://www.dealnotify.co/admin | Admin panel (internal) |
| https://www.dealnotify.co/api/stripe-webhook | Stripe webhook endpoint |
