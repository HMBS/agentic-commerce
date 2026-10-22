#!/usr/bin/env python3
"""
End-to-end check of the storefront, driven with Playwright against the local server.

  python3 server.py &            # needs the site up on :8088
  python3 tests/e2e.py           # writes screenshots to /tmp/fvshots

Verifies, by clicking the real UI:
  - cream canvas, circular product imagery, 40px hero radius
  - age gate blocks a wine add, then unlocks it after confirmation
  - basket totals and the agent payload reflect the state
  - checkout returns HTTP 200 for a verified basket and HTTP 422 without the flag
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

# point at either backend: python3 server.py (:8088) or the PHP static build (:8090)
BASE = os.environ.get("FV_BASE", "http://127.0.0.1:8088/")
if not BASE.endswith("/"):
    BASE += "/"

CHROME = next(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
SHOTS = Path("/tmp/fvshots")
SHOTS.mkdir(exist_ok=True)

failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                              device_scale_factor=1, color_scheme="light")
    page = ctx.new_page()
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    page.goto(BASE, wait_until="networkidle")

    # ── visual assertions from computed styles ────────────────────────────
    body_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    check(body_bg == "rgb(243, 240, 238)", f"body canvas is #F3F0EE (got {body_bg})")

    radius = page.evaluate("getComputedStyle(document.querySelector('.hero__frame')).borderRadius")
    check(radius.startswith("40px"), f"hero stadium radius 40px (got {radius})")

    media_radius = page.evaluate("getComputedStyle(document.querySelector('.card__media')).borderRadius")
    check(media_radius == "50%", f"product imagery circular (got {media_radius})")

    font = page.evaluate("getComputedStyle(document.querySelector('h1')).fontFamily")
    check("Sofia Sans" in font, f"Sofia Sans applied (got {font})")

    # the ghost word bleeds past the frame on purpose (overflow:hidden), so check
    # the last real content block rather than scrollHeight
    hero_overflow = page.evaluate(
        """(() => {
             const f = document.querySelector('.hero__frame').getBoundingClientRect();
             const m = document.querySelector('.hero__meta').getBoundingClientRect();
             const h = document.querySelector('.hero__badge').getBoundingClientRect();
             return Math.round(Math.max(m.bottom - (f.bottom - 1), (f.top + 1) - h.top));
           })()"""
    )
    check(hero_overflow <= 0, f"hero copy sits inside its frame (clipped by {hero_overflow}px)")

    hscroll = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(hscroll <= 0, f"no horizontal overflow on desktop ({hscroll}px)")

    # lazy images below the fold need a scroll pass before the full-page capture
    page.add_style_tag(content="html{scroll-behavior:auto !important}")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(900)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    broken = page.evaluate("[...document.images].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.src)")
    check(not broken, f"all product images decoded ({broken})")

    page.screenshot(path=str(SHOTS / "desktop-full.png"), full_page=True)

    # ── age gate blocks the wine ─────────────────────────────────────────
    wine_btn = page.locator('[data-add="win-pet-2023"]')
    wine_btn.click()
    page.wait_for_timeout(300)
    check(page.locator("[data-age-modal]").evaluate("el => el.classList.contains('is-open')"),
          "adding wine opens the age gate")
    check(page.locator("[data-cart-count]").inner_text() == "0",
          "wine is NOT added before confirmation")
    page.screenshot(path=str(SHOTS / "age-gate.png"))

    page.locator("[data-age-confirm]").click()
    page.wait_for_timeout(300)
    check(not page.locator("[data-age-modal]").evaluate("el => el.classList.contains('is-open')"),
          "age gate closes after confirmation")
    check(page.locator("[data-cart-count]").inner_text() == "1",
          "the pending wine lands in the basket after confirmation")

    # a second wine now adds directly
    page.locator('[data-add="win-bar-2019"]').click()
    page.locator('[data-add="cof-yir-250"]').click()
    page.wait_for_timeout(200)
    check(page.locator("[data-cart-count]").inner_text() == "3", "basket counts 3 items")

    # ── basket + agent payload ───────────────────────────────────────────
    page.locator("[data-cart-open]").click()
    page.wait_for_timeout(400)
    total = page.locator("[data-cart-total]").inner_text()
    check(total == "€92.50", f"basket total €92.50 (got {total})")
    page.screenshot(path=str(SHOTS / "basket.png"))

    page.locator('[data-tab="agent"]').click()
    payload_text = page.locator("[data-agent-json]").inner_text()
    payload = json.loads(payload_text)
    check(payload["age_verified"] is True, "agent payload carries age_verified=true")
    check(len(payload["items"]) == 3, f"agent payload has 3 lines (got {len(payload['items'])})")
    page.screenshot(path=str(SHOTS / "agent-payload.png"))

    # ── checkout round-trip ──────────────────────────────────────────────
    page.locator("[data-checkout]").click()
    page.wait_for_selector("[data-receipt-modal].is-open", timeout=5000)
    page.wait_for_timeout(350)          # let the .22s modal fade finish before capture
    status = page.locator("[data-receipt-status]").inner_text()
    body = json.loads(page.locator("[data-receipt-body]").inner_text())
    check(status == "HTTP 200", f"verified basket prices (got {status})")
    check(body["checkout_session"]["total"] == 9250,
          f"session total 9250 minor units (got {body['checkout_session']['total']})")
    # VAT backed out of VAT-inclusive shelf prices: 2800*23/123=524 + 4600*23/123=860
    check(body["checkout_session"]["tax_total"] == 1384,
          f"VAT portion 1384 (got {body['checkout_session']['tax_total']})")
    page.screenshot(path=str(SHOTS / "receipt.png"))
    page.locator("[data-receipt-close]").first.click()
    page.wait_for_timeout(300)

    # ── the unverified path really is rejected by the API ────────────────
    unverified = page.evaluate(
        """async () => {
             const declared = document.querySelector('meta[name="fv-api"]')?.content;
             for (const url of [...new Set([declared, '/api/checkout', '/api/checkout.php'].filter(Boolean))]) {
               const r = await fetch(url, {
                 method: 'POST', headers: {'content-type': 'application/json'},
                 body: JSON.stringify({items: [{id: 'win-bar-2019', quantity: 1}], age_verified: false})
               });
               const ct = r.headers.get('content-type') || '';
               if (!ct.includes('json')) continue;          // static host 404 page
               return {status: r.status, body: await r.json(), url};
             }
             return {status: 0, body: {error: {code: 'api_unavailable'}}};
           }"""
    )
    check(unverified["status"] == 422, f"API rejects unverified restricted basket (got {unverified['status']})")
    check(unverified["body"]["error"]["code"] == "age_verification_required",
          "error code is age_verification_required")

    # ── mobile ───────────────────────────────────────────────────────────
    mob = ctx.new_page()
    mob.set_viewport_size({"width": 390, "height": 844})
    mob.goto(BASE, wait_until="networkidle")
    mob_h = mob.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(mob_h <= 0, f"no horizontal overflow at 390px ({mob_h}px)")
    mob.add_style_tag(content="html{scroll-behavior:auto !important}")  # smooth scroll makes captures race
    mob.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    mob.wait_for_timeout(900)
    mob.evaluate("window.scrollTo(0, 0)")
    mob.wait_for_timeout(300)
    mob.screenshot(path=str(SHOTS / "mobile-hero.png"))
    mob.screenshot(path=str(SHOTS / "mobile-full.png"), full_page=True)

    # the 422 below is an intentional API probe; ignore it when auditing the console
    unexpected = [e for e in console_errors if "422" not in e]
    check(not unexpected, f"no unexpected console errors ({unexpected[:3]})")

    browser.close()

print("\n" + ("E2E PASSED" if not failures else f"{len(failures)} E2E FAILURES"))
sys.exit(1 if failures else 0)
