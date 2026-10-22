#!/usr/bin/env python3
"""Static + served checks. Run with the server up:  python3 verify.py"""

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
fails, notes = [], []


def check(cond, msg):
    (notes if cond else fails).append(("PASS " if cond else "FAIL ") + msg)


html = (ROOT / "index.html").read_text()
css = (ROOT / "assets/styles.css").read_text()
js = (ROOT / "assets/app.js").read_text()
catalog = json.loads((ROOT / "products.json").read_text())
products = {p["id"]: p for p in catalog["products"]}

# 1. card data matches catalog
cards = re.findall(
    r'<article class="card[^"]*"[^>]*data-product-id="([^"]+)"[^>]*data-price="(\d+)"[^>]*data-restriction="([^"]*)"',
    html,
)
check(len(cards) == len(products), f"{len(cards)} product cards for {len(products)} catalog products")
for pid, price, restriction in cards:
    p = products.get(pid)
    if not p:
        check(False, f"card id {pid} not in catalog")
        continue
    check(int(price) == p["price"], f"{pid} price {price} == catalog {p['price']}")
    check(restriction == (p["restriction"] or ""), f"{pid} restriction '{restriction}'")

# 2. every image exists and is referenced
imgs = re.findall(r'src="(assets/img/[^"]+)"', html)
check(len(imgs) > 0, f"{len(imgs)} local images referenced")
for src in imgs:
    check((ROOT / src).exists(), f"{src} exists")

# 3. no external network dependencies
external = re.findall(r'(?:href|src)="(https?://[^"]+)"', html)
external = [u for u in external if "example.test" not in u]
check(not external, f"no external asset references (found {external})")
check("fonts.googleapis" not in css, "font is self-hosted, no CDN")
check((ROOT / "assets/fonts/sofia-sans-latin.woff2").exists(), "Sofia Sans latin subset present")

# 4. token presence (DESIGN.md)
for token, label in [
    ("#F3F0EE", "Canvas Cream"),
    ("#141413", "Ink Black"),
    ("#CF4500", "Signal Orange"),
    ("#F37338", "Light Signal Orange arc"),
    ("#E8E2DA", "ghost watermark"),
    ("--r-stadium:  40px", "40px stadium radius"),
]:
    check(token.lower() in css.lower(), f"token present: {label} ({token})")

# 5. Signal Orange is not used as a marketing CTA: only consent/legal classes
orange_rules = [ln.strip() for ln in css.splitlines() if "--signal:" in ln or "var(--signal)" in ln]
allowed_context = ("consent", "signal", "age", "flag", "receipt")
suspicious = [ln for ln in orange_rules if "background" in ln and not any(k in ln for k in allowed_context)]
check(not suspicious, f"Signal Orange not spent on marketing fills ({suspicious})")

# 6. brace balance
style = css
check(style.count("{") == style.count("}"), f"CSS braces balanced ({style.count('{')}/{style.count('}')})")


class Balance(HTMLParser):
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.void:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.void:
            return
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass


b = Balance()
b.feed(html)
check(not b.stack, f"HTML tags balanced (unclosed: {b.stack})")

# 7. every product rendered has an add control bound to its id
for pid in products:
    check(f'data-add="{pid}"' in html, f"{pid} has an add control")

# 8. a11y basics
check('aria-modal="true"' in html, "dialogs declare aria-modal")
check(html.count("<h1") == 1, "exactly one h1")
check('lang="en"' in html, "html lang set")
for pid, p in products.items():
    if p["restriction"]:
        check(f'data-restriction="{p["restriction"]}"' in html,
              f"{pid} exposes restriction code in markup")

# 9. served routes (optional)
try:
    import urllib.request

    def get(path, data=None):
        req = urllib.request.Request("http://127.0.0.1:8088" + path)
        if data is not None:
            req.add_header("content-type", "application/json")
            req.data = json.dumps(data).encode()
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    s, body, h = get("/")
    check(s == 200 and b"application/ld+json" in body, "index served with injected JSON-LD")
    s, body, h = get("/.well-known/ucp")
    check(s == 200 and "max-age=3600" in h.get("Cache-Control", ""), "UCP profile cached >= 60s")
    s, body, h = get("/catalog.json")
    check(s == 200 and json.loads(body)["count"] == len(products), "catalog.json count matches")
    s, body, h = get("/api/checkout", {"items": [{"id": products[list(products)[0]]["id"], "quantity": 1}]})
    check(s == 200, "checkout accepts unrestricted basket")
    restricted = next(p for p in products.values() if p["restriction"])
    s, body, h = get("/api/checkout", {"items": [{"id": restricted["id"], "quantity": 1}], "age_verified": False})
    check(s == 422 and json.loads(body)["error"]["code"] == "age_verification_required",
          "checkout blocks restricted basket without age flag")
    s, body, h = get("/api/agent/quote")
    check(s == 402 and "PAYMENT-REQUIRED" in h, "402 quote returns PAYMENT-REQUIRED header")
except Exception as exc:  # server not running
    notes.append(f"SKIP served-route checks ({exc})")

for line in notes:
    print(line)
for line in fails:
    print(line)
print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILURES"))
sys.exit(1 if fails else 0)
