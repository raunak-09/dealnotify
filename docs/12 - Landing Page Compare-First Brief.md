# DealNotify Landing Page Redesign Brief — Compare-First Positioning

**For:** the designer / design agent producing the new landing page
**Goal:** reposition DealNotify around the Compare feature as the lead value prop, while keeping price-drop and restock alerts intact as supporting features.
**Author:** Mishi (manisha.jmc@gmail.com), founder
**Existing site:** [dealnotify.co](https://www.dealnotify.co) — single `index.html`, ~1900 lines

---

## TL;DR for the designer

The current landing page tells a passive story: *"Set up tracking, we'll email you when prices drop."* That story still works, but DealNotify's strongest differentiator now is the **Compare feature**, which is active and visible *the moment a user lands on a product page*. The redesign should lead with Compare, anchor it visually with a real screenshot of the in-extension panel, and demote the existing "track-and-alert" story to a secondary feature.

---

## 1. The strategic shift in one sentence

> "Before DealNotify: open 5 tabs, check 5 retailers, give up. After DealNotify: stay on Amazon, see the cheaper price at Walmart automatically — then track it if you want to wait for an even better deal."

That sentence is the entire repositioning. Compare is the *act of saving money in the moment*. Alerts are the *act of saving money over time*. The page should communicate both, in that order.

---

## 2. Conversion priorities

**Primary CTA:** Free trial signup (`/signup` — currently anchored as `#signup`).
30-day free trial, no credit card.

**Secondary CTA:** Install the Chrome extension. The extension is how Compare actually delivers value, so the install path matters — but **conversion to a tracked-account user is the metric**. The Chrome install on its own is not the goal.

**Both CTAs should appear above the fold and at the bottom of every major section.**

---

## 3. Audience

Online shoppers in the US who:

- Buy electronics, home goods, gaming gear from major retailers (Amazon is the dominant starting point, but Walmart/Target/Best Buy/Costco/eBay are all in scope).
- Have been burned by price drops they missed, OR who manually compare prices across retailers and find it tedious.
- Are price-sensitive but not coupon/extreme-deal hunters — this is *casual savings*, not *extreme couponing*.
- Use Chrome (the extension is Chrome-only).

Tone: warm, helpful, no pressure. **Not** "GET RICH SAVING $$$" energy. Closer to a smart friend who happens to know prices.

---

## 4. Brand identity (already established — do not change)

| Element | Value |
|---|---|
| Primary color | Purple `#5b67f8` |
| Secondary | Navy `#1a1a2e` |
| Accent / success | Green `#27ae60` (for savings amounts) |
| Logo | Golden bell + "DealNotify" wordmark |
| Typography | Segoe UI / Inter (sans-serif) |
| Tagline | "Never miss a price drop or restock" *(may evolve — see Section 5)* |
| Voice | Friendly, direct, money-conscious. No emojis in headlines. Sparing in body copy. |

Existing brand assets in the repo:

- `dealnotify-logo-440x280.png`
- `dealnotify-promo-440x280.png`
- `dealnotify-square.png`
- `Restock Feature.png`, `Restock.png`, `restock_oos_1280x800.png` — restock UI captures
- `Target price Setup.png` — dashboard target-price UI
- `DealNotify Signup.png` — signup screen capture
- `extension-popup-screenshot.png` — current extension popup

**Missing assets that need to be produced** (callout for the designer):

1. **Compare panel hero shot** — high-fidelity capture of the Compare panel rendered on a real Amazon PDP, showing a *credible* savings example (e.g. Sony WH-1000XM5 cheaper at Walmart). This is the single most important visual on the page. ⚠️ **Must be captured using extension v1.6.1 or later** — earlier versions had a false-OOS bug that incorrectly auto-selected the Restock tab on in-stock urgency-messaged products like Sony WH-1000XM5. v1.6.1 fixes this; older screenshots will show the wrong default tab.
2. **Compare panel multi-retailer view** — same panel showing 3–4 retailers side-by-side with the green "best price" badge.
3. **Animated GIF or short mp4** of Compare appearing automatically when the user lands on a PDP — 3–5 seconds, looped. Drop into Hero or just below.
4. **Google sign-in button mockup** — extension v1.6.0+ supports Sign in with Google. The landing page's "Add to Chrome" / signup CTAs should reflect this option visually (Google G logo + "Sign in with Google" wording alongside the existing email/password flow).

---

## 5. Tagline evolution

Current tagline emphasizes alerts. With Compare leading, consider variations:

- "**The smartest way to never overpay online.**"
- "**Compare prices instantly. Track them automatically.**"
- "**See the cheaper price before you click 'Buy'.**"

Recommend: the second one. It captures both halves of the value prop in one line and the imperative verbs ("Compare", "Track") map directly to the two product modes.

---

## 6. Page structure (proposed)

Suggested section order, top to bottom:

| # | Section | Purpose | Hero asset |
|---|---|---|---|
| 1 | **Hero** | Headline value prop + primary CTA + Compare panel hero shot | Compare panel screenshot or animated GIF |
| 2 | **The "moment of truth" demo** | Animated/interactive demo: Amazon PDP → Compare panel appears → user clicks Walmart and saves $50 | Real animation/GIF |
| 3 | **Compare deep-dive** (NEW — centerpiece) | Three-column explainer: how it works, retailers covered, confidence/accuracy | Multi-retailer panel view |
| 4 | **Plus: never miss a deal again** | Track-and-alert features as the *complementary* story | Existing dashboard + alert email screenshots |
| 5 | **Social proof** | Stats, testimonials if any, retailer logos | Logo strip |
| 6 | **How it works** | 60-second setup (kept from current page, reordered) | Existing 3-step pattern |
| 7 | **Pricing** | Free trial → Pro $4.99 (kept) | (no image needed) |
| 8 | **FAQ** | Compare-specific questions added (privacy, accuracy, retailers, refund) | (no image) |
| 9 | **Final CTA + footer** | Repeat primary CTA | Brand colors |

Sections 3 and 4 are the meaningful structural change. Everything else is reorder + refresh.

---

## 7. Hero section in depth

This is the section that decides whether visitors keep scrolling.

**Layout:** Two-column. Left: text + CTAs. Right: Compare panel hero shot (or looped GIF).

**Headline:** Should answer "what does this do in one breath?"
Recommended: **"See the cheaper price before you click 'Buy'."**

**Sub-headline:** ~15–20 words, expands the value prop and names the retailers.
Recommended: *"DealNotify scans Walmart, Target, Best Buy, Costco, and eBay while you shop on Amazon. The cheaper price shows up automatically — no extra tabs, no hunting."*

**CTAs:**

- **Primary button:** "Start free trial — 30 days, no card" → `/signup`
- **Secondary button (lighter style):** "Add to Chrome →" → Chrome Web Store URL

**Sign-up auth options (under the primary CTA, on the signup form, and in the Chrome extension popup):** the extension as of v1.6.0 supports **Sign in with Google** in addition to email/password. The landing page should mirror this — show a Google G logo + "Sign in with Google" button on the signup CTA pattern, with email/password as the alternative below an "or" divider. Same visual pattern Notion, Linear, and Stripe use. Reduces friction and removes the email-verification round-trip for Google users (Google has already verified the email).

**Below the buttons (small text):** "Free for 30 days. $4.99/month after. Cancel any time."

**Trust strip below the hero:** Single row of retailer logos in muted grey: Amazon, Walmart, Target, Best Buy, Costco, eBay. Subtitle: "Works on every major retailer."

---

## 8. Compare deep-dive section (the centerpiece)

This is the section the rest of the page is building toward. Treat it like the marquee feature page on a SaaS site.

**Section heading:** "Compare prices in real time, automatically"
**Section sub-heading:** "Open any product on Amazon. DealNotify silently checks every other major retailer and shows you if it's cheaper somewhere else."

**Three columns (or three rows on mobile):**

1. **"Sees what you're looking at."**
   Caption: "DealNotify reads the product you're on — brand, model, specs — and finds the same item at every other retailer we support."
   Visual: small icon of a magnifying glass over a product card.

2. **"Compares across 5+ retailers."**
   Caption: "Walmart. Target. Best Buy. Costco. eBay. All checked in parallel, results in seconds."
   Visual: row of 5 retailer logos with checkmarks.

3. **"Shows you the best deal."**
   Caption: "If it's cheaper somewhere else, you'll see the savings amount, the retailer, and a direct link to buy."
   Visual: tight crop of the green "Save $52" badge.

**Below the three columns:** the **multi-retailer panel hero shot** at full width. Caption underneath: *"DealNotify on a Sony WH-1000XM5 product page. Walmart was $52 cheaper that day."*

**Confidence callout (smaller, secondary):** "*We use AI matching to make sure we compare the same product, not a knockoff or wrong size. If we're not sure, we say so.*"

---

## 9. "Plus: never miss a deal again" section (the demoted-but-still-loved alerts story)

This is the existing track-and-alert story, intact but now framed as "and on top of all that, here's the long-game savings tool."

**Section heading:** "And when the price still isn't right? Track it."
**Sub-heading:** "Set a target price and we'll watch it 24/7. The moment it hits, you get an email."

**Two side-by-side feature cards:**

- **Price drop alerts.** "Set the price you want. We check every 2 hours (Pro) or 6 hours (Free). When it drops, you'll know within minutes." — visual: existing `Target price Setup.png`.
- **Restock alerts.** "Out-of-stock item back in stock? You'll be the first to know." — visual: existing `Restock Feature.png`.

This section is short. Two cards, one paragraph each. Don't let it compete with the Compare deep-dive for attention.

---

## 10. FAQ — questions to add

The existing FAQ keeps. Add these Compare-specific ones at the top of the FAQ list:

- **"How accurate is the price comparison?"** — Mention AI matching, confidence levels, that we won't show a match unless we're sure.
- **"What retailers does Compare work on?"** — List them. Note the source-side support (Amazon currently primary; eBay/Walmart roadmap if not shipped).
- **"Does Compare slow down my browsing?"** — No. Comparisons run in the background; the panel only appears when results are ready.
- **"Is my browsing data tracked?"** — Link to privacy policy. Be explicit: only the product URL is sent to our server, only when you're on a supported retailer's PDP.
- **"Do you make money from the affiliate links?"** — Yes — be transparent. "When you click a 'View on Walmart' link and buy, we may earn a small commission. This never affects which retailer we show you — we always rank by lowest price."
- **"Can I sign up with Google?"** — Yes. As of extension v1.6.0, you can sign in with your Google account directly from the extension popup. Your email is auto-verified by Google, so you can start tracking and using Compare immediately — no email-verification round-trip required.

The transparency on affiliate links is a trust-builder, not a liability. Lead into it with confidence.

---

## 11. What to AVOID

- ❌ **Stock-image shoppers staring at laptops with credit cards.** Use real product UI screenshots only.
- ❌ **Hyperbolic savings claims** ("Save 50% on everything!"). Use a credible specific number — "$52 saved on a Sony WH-1000XM5" — not generic %s.
- ❌ **Walls of feature bullets.** The current page has 8 feature cards. The new design should have at most 4 features highlighted, with Compare getting 2× the visual weight of the others.
- ❌ **Multiple competing CTAs.** Hero gets one primary + one secondary. Mid-page CTAs should always be "Start free trial." No "Learn more" buttons that just scroll to another section — those are conversion-killers.
- ❌ **Price-comparison tables of competitors.** Don't compare DealNotify vs Honey vs Camelizer in a feature matrix. Trust the product to differentiate itself.
- ❌ **"AI-powered" front and center in the hero.** "AI matching" appears once in the Compare deep-dive as a confidence callout. That's enough.

---

## 12. Decisions Mishi needs to confirm before the designer starts

| Q | Default if unanswered | Why it matters |
|---|---|---|
| Final tagline? | "Compare prices instantly. Track them automatically." | Sets tone for entire hero |
| Animated GIF/mp4 in hero, or static screenshot? | Static for v1, animated for v2 | Animated is more compelling but takes ~1 week longer to produce |
| Source URL for the Compare panel hero shot — which product? | Sony WH-1000XM5 (already used in our demo script) | Needs to be a credible savings example with a real $ delta |
| Showcase real testimonials, or skip social proof? | Skip until we have ≥3 real ones | Fake/generic testimonials hurt trust more than no testimonials |
| Chrome Web Store install link — direct CWS URL or our dealnotify.co/install page? | Direct CWS URL | Less friction; CWS install button is more trusted than a redirect |

---

## 13. Deliverables expected from the designer

1. **High-fidelity mockup** of the redesigned landing page — Figma file or single-page HTML/CSS. Both desktop and mobile layouts.
2. **Two captures specifically called out** as new assets (see Section 4 missing-assets list): the Compare hero shot and the multi-retailer panel view.
3. **Optionally: an animated GIF/mp4** of the Compare panel appearing on a PDP — 3–5 seconds, looped, ≤5MB.
4. **Section-by-section copy** matching the structure in Section 6, with final approved headlines and body text.
5. **Conversion CTA copy** — primary button text, secondary button text, mid-page CTA variations (3–4 different ones the page can rotate through).

---

## 14. Reference inspirations (landing pages with strong feature-led hero)

These are pages whose *structure* the designer should study — not whose copy to imitate:

- **Linear.app** — minimal hero, real product UI front-and-center, clear hierarchy.
- **Arc Browser (arc.net)** — leads with a single bold value prop, animated demo immediately below.
- **Fathom Analytics (usefathom.com)** — clean two-column hero, dashboard screenshot on the right, very direct copy.
- **Honey (joinhoney.com)** — direct competitor; useful to study what NOT to copy (busy hero, generic illustrations).

---

## 15. Brand & technical guardrails

- All copy in American English. No British spellings.
- Buttons: rounded corners (`border-radius: 8px`), purple primary, navy or white secondary.
- Hero font size: 48–56px desktop, 32–36px mobile.
- All CTAs link to existing routes — `/signup`, `/dashboard`, Chrome Web Store URL. **Don't invent new routes.**
- Privacy policy link in footer must point to existing `/privacy` page.
- Existing analytics is Google Analytics (`G-QQ2L8EBBV8`). Make sure the redesign preserves the GA tag.

---

## 16. Out of scope (do not redesign)

- Dashboard UI — separate redesign, not this brief.
- Pricing page (it's part of the landing page; only minor updates if structure changes).
- Blog / blog posts.
- Email templates (price drop, restock, welcome).
- Chrome extension popup UI.

If the designer feels strongly any of these should change, raise it as a follow-up — don't bundle it in.

---

**Length expectation for the final deliverable:**
Total mockup should fit in a single scrollable page, ~6–8 sections, mobile-equivalent ~10–12 vertical "screens" of content. Not longer.
