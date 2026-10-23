#!/usr/bin/env python3
"""
Fitzwilliam Provisions — local storefront + machine surface.

Serves the landing page and a small, honest agent-facing API:
  GET  /                      landing page (JSON-LD injected from products.json)
  GET  /.well-known/ucp       UCP business profile (illustrative, not a conformance claim)
  GET  /catalog.json          machine catalog
  GET  /llms.txt              plain-text map for language models
  GET  /api/products          same catalog, JSON
  POST /api/checkout          prices a basket; enforces the age gate server-side
  GET  /api/agent/quote       402 Payment Required, x402 v2 wire shape (no settlement)

Standard library only.  Run:  python3 server.py  [--port 8088]
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
CATALOG = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
MERCHANT = CATALOG["merchant"]
PRODUCTS = {p["id"]: p for p in CATALOG["products"]}

# Irish VAT treatment used for the demo: food is zero-rated, alcohol and
# non-food kitchen goods sit at the standard 23% rate. All shelf prices are
# VAT-inclusive, so the tax portion is backed out rather than added.
VAT_RATE = {"pantry": 0.0, "wine": 0.23, "kitchen": 0.23}

BASE_URL = "http://localhost:8088"  # rewritten from Host header when served


def vat_portion(gross_minor: int, rate: float) -> int:
    if rate == 0:
        return 0
    return round(gross_minor * rate / (1 + rate))


# ── Machine documents ──────────────────────────────────────────────────────

def ucp_profile(base: str) -> dict:
    return {
        "ucp": {
            "version": "2026-04-08",
            "supported_versions": ["2026-04-08"],
            "services": {"rest": {"base_url": base}},
            "capabilities": {
                "dev.ucp.shopping.catalog": ["2026-04-08"],
                "dev.ucp.shopping.cart": ["2026-04-08"],
                "dev.ucp.shopping.checkout": ["2026-04-08"],
            },
            "handlers": {
                "card": {"scheme": "platform_token"},
                "x402": {
                    "scheme": "exact",
                    "network": "eip155:8453",
                    "settlement": "unimplemented",
                },
            },
            "signing_keys": [],
            "merchant": {
                "name": MERCHANT["name"],
                "legal_name": MERCHANT["legal_name"],
                "address": MERCHANT["address"],
                "currency": MERCHANT["currency"],
                "age_policy": MERCHANT["age_policy"],
            },
            "_note": (
                "Illustrative profile for local testing. Field names follow the "
                "published shape but this is not a conformance claim and no "
                "signature verification is performed."
            ),
        }
    }


def product_image(base: str, image: str) -> str:
    """Keep CDN/storage URLs absolute; root relative paths at the storefront."""
    if image.startswith(("http://", "https://")):
        return image
    return f"{base}/{image.lstrip('/')}"


def catalog_doc(base: str) -> dict:
    return {
        "merchant": MERCHANT,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "currency": MERCHANT["currency"],
        "count": len(PRODUCTS),
        "products": [
            {
                "id": p["id"],
                "sku": p["sku"],
                "name": p["name"],
                "description": p["subtitle"],
                "category": p["category"],
                "price": p["price"],
                "price_minor_units": p["price"],
                "currency": MERCHANT["currency"],
                "price_display": f"{p['price'] / 100:.2f}",
                "stock": p["stock"],
                "in_stock": p["stock"] > 0,
                "age_restriction": p["restriction"],
                "requires_age_verification": p["restriction"] is not None,
                "vat_rate": VAT_RATE.get(p["category"], 0.23),
                "url": f"{base}/index.html#counter",
                "image": product_image(base, p["image"]),
                "flavour_notes": p["notes"],
            }
            for p in PRODUCTS.values()
        ],
        "checkout_endpoint": f"{base}/api/checkout",
        "profile_url": f"{base}/.well-known/ucp",
    }


def jsonld_doc(base: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Store",
                "@id": f"{base}/#store",
                "name": MERCHANT["name"],
                "legalName": MERCHANT["legal_name"],
                "url": f"{base}/",
                "address": {
                    "@type": "PostalAddress",
                    "streetAddress": "14 Fitzwilliam Lane",
                    "addressLocality": "Dublin 2",
                    "postalCode": "D02 XR20",
                    "addressCountry": "IE",
                },
                "currenciesAccepted": "EUR",
                "paymentAccepted": "Card, Stablecoin (x402)",
            },
            {
                "@type": "ItemList",
                "name": "On the counter",
                "numberOfItems": len(PRODUCTS),
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": i + 1,
                        "item": {
                            "@type": "Product",
                            "sku": p["sku"],
                            "name": p["name"],
                            "description": p["subtitle"],
                            "image": f"{base}/{p['image']}",
                            "category": p["category"],
                            "offers": {
                                "@type": "Offer",
                                "price": f"{p['price'] / 100:.2f}",
                                "priceCurrency": MERCHANT["currency"],
                                "availability": (
                                    "https://schema.org/InStock"
                                    if p["stock"] > 0
                                    else "https://schema.org/OutOfStock"
                                ),
                                "url": f"{base}/index.html#counter",
                                "seller": {"@id": f"{base}/#store"},
                                **(
                                    {
                                        "eligibleQuantity": {
                                            "@type": "QuantitativeValue",
                                            "minValue": 0,
                                        }
                                    }
                                    if not p["restriction"]
                                    else {}
                                ),
                            },
                            **(
                                {
                                    "additionalProperty": {
                                        "@type": "PropertyValue",
                                        "name": "age_restriction",
                                        "value": p["restriction"],
                                    }
                                }
                                if p["restriction"]
                                else {}
                            ),
                        },
                    }
                    for i, p in enumerate(PRODUCTS.values())
                ],
            },
        ],
    }


def llms_txt(base: str) -> str:
    lines = [
        f"# {MERCHANT['name']}",
        "",
        f"> A small grocery in Dublin 2 with {len(PRODUCTS)} products, two of them age-restricted.",
        "> This site is a test bench for agentic checkout: it publishes machine-readable",
        "> shelf data and enforces an age gate on the server, not just in the browser.",
        "",
        "## Machine endpoints",
        "",
        f"- [UCP profile]({base}/.well-known/ucp): capabilities, versions, payment handlers. Cached 1h.",
        f"- [Catalog]({base}/catalog.json): all products, prices in minor units, restriction codes.",
        f"- [Checkout]({base}/api/checkout): POST a basket, receive a priced session or a 422.",
        f"- [402 quote]({base}/api/agent/quote): x402 v2 PaymentRequired shape. Settlement is not implemented.",
        "",
        "## Policy",
        "",
        f"- Age policy: {MERCHANT['age_policy']}",
        "- Prices are VAT-inclusive EUR. Food is zero-rated; alcohol and kitchen goods are 23%.",
        "- Age assertions in this build are self-declared and must not be treated as verification.",
        "- No payment is taken and no order is fulfilled. Nothing here is a real shop.",
        "",
        "## Products",
        "",
    ]
    for p in PRODUCTS.values():
        flag = f" — AGE RESTRICTED ({p['restriction']})" if p["restriction"] else ""
        lines.append(f"- {p['name']} ({p['sku']}): {p['subtitle']}. €{p['price'] / 100:.2f}{flag}")
    return "\n".join(lines) + "\n"


# ── Checkout ───────────────────────────────────────────────────────────────

def price_basket(body: dict) -> tuple[int, dict]:
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return 400, {"error": {"code": "empty_basket", "message": "items must be a non-empty array"}}

    lines, subtotal, tax_total, blocked = [], 0, 0, []
    for raw in items:
        pid = raw.get("id") if isinstance(raw, dict) else None
        qty = raw.get("quantity", 1) if isinstance(raw, dict) else None
        if pid not in PRODUCTS:
            return 422, {
                "error": {
                    "code": "unknown_item",
                    "message": f"No product with id {pid!r} on this shelf",
                }
            }
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= 99:
            return 422, {
                "error": {
                    "code": "invalid_quantity",
                    "message": f"quantity for {pid} must be an integer 1-99",
                }
            }
        p = PRODUCTS[pid]
        rate = VAT_RATE.get(p["category"], 0.23)
        gross = p["price"] * qty
        subtotal += gross
        tax_total += vat_portion(gross, rate)
        lines.append(
            {
                "id": p["id"],
                "sku": p["sku"],
                "name": p["name"],
                "quantity": qty,
                "unit_price": p["price"],
                "line_total": gross,
                "vat_rate": rate,
                "age_restriction": p["restriction"],
            }
        )
        if p["restriction"]:
            blocked.append({"id": p["id"], "sku": p["sku"], "restriction": p["restriction"]})

    age_verified = body.get("age_verified") is True
    if blocked and not age_verified:
        return 422, {
            "error": {
                "code": "age_verification_required",
                "message": (
                    "Basket contains age-restricted lines. Assert age_verified=true "
                    "after an age check, or remove them."
                ),
                "retryable": True,
                "recovery": "verify_age",
                "details": {"blocked_items": blocked, "policy": MERCHANT["age_policy"]},
            }
        }

    session = {
        "id": f"ck_{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "ready_for_payment",
        "currency": MERCHANT["currency"],
        "merchant": {"name": MERCHANT["name"], "legal_name": MERCHANT["legal_name"]},
        "lines": lines,
        "subtotal": subtotal,
        "tax_total": tax_total,
        "total": subtotal,  # prices are VAT-inclusive
        "price_note": "All amounts in EUR minor units. Shelf prices include VAT.",
        "age_assertion": {
            "verified": age_verified,
            "method": "self_declared" if age_verified else "none",
            "restricted_lines": len(blocked),
            "note": (
                "Test build: the assertion comes from the client. A production "
                "shop must use an age-verification provider and keep an audit trail."
            ),
        },
        "payment": {"handlers": ["card", "x402"], "x402_quote": "/api/agent/quote"},
        "agent": body.get("agent") or None,
        "disclaimer": "Test shop. No payment is taken, no order is placed.",
    }
    return 200, {"checkout_session": session}


def payment_required(base: str) -> dict:
    return {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": {
            "url": f"{base}/api/agent/quote",
            "description": "Checkout priority slot for one basket (test resource)",
            "mimeType": "application/json",
            "serviceName": MERCHANT["name"],
            "tags": ["checkout", "grocery", "test"],
        },
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:8453",
                "amount": "1000000",
                "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "payTo": "merchant",
                "maxTimeoutSeconds": 60,
                "extra": {
                    "assetTransferMethod": "eip3009",
                    "paymentFlow": "authorization",
                    "name": "USDC",
                    "version": "2",
                    "settlement": "not_implemented_in_this_build",
                },
            }
        ],
    }


# ── HTTP handler ───────────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    server_version = "FitzwilliamTest/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # helpers -------------------------------------------------------------
    def base_url(self) -> str:
        host = self.headers.get("Host") or "localhost:8088"
        return f"http://{host}"

    def send_json(self, status: int, payload, extra_headers: dict | None = None):
        raw = json.dumps(payload, indent=2, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, PAYMENT-SIGNATURE, UCP-Agent")
        self.send_header("Access-Control-Expose-Headers", "PAYMENT-REQUIRED, PAYMENT-RESPONSE")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):  # quieter, but still useful
        print(f"  {self.address_string()} {fmt % args}")

    # routes --------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, PAYMENT-SIGNATURE, UCP-Agent")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        base = self.base_url()

        if path == "/.well-known/ucp":
            self.send_json(
                200,
                ucp_profile(base),
                {"Cache-Control": "public, max-age=3600"},
            )
            return
        if path == "/catalog.json":
            self.send_json(
                200,
                catalog_doc(base),
                {"Cache-Control": "public, max-age=60"},
            )
            return
        if path == "/api/products":
            self.send_json(200, catalog_doc(base))
            return
        if path == "/llms.txt":
            body = llms_txt(base).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/agent/quote":
            if self.headers.get("PAYMENT-SIGNATURE"):
                self.send_json(
                    501,
                    {
                        "error": {
                            "code": "settlement_not_implemented",
                            "message": (
                                "This build publishes the 402 wire shape only. "
                                "No facilitator is contacted and nothing is settled."
                            ),
                        }
                    },
                )
                return
            doc = payment_required(base)
            encoded = base64.b64encode(json.dumps(doc).encode()).decode()
            self.send_json(402, doc, {"PAYMENT-REQUIRED": encoded})
            return
        if path in ("/", "/index.html"):
            self.serve_index()
            return

        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path != "/api/checkout":
            self.send_json(404, {"error": {"code": "not_found", "message": path}})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64_000:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(
                400,
                {"error": {"code": "invalid_json", "message": str(exc)}},
            )
            return
        status, payload = price_basket(body)
        self.send_json(status, payload)

    def serve_index(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        script = (
            '<script type="application/ld+json">\n'
            + json.dumps(jsonld_doc(self.base_url()), indent=2, ensure_ascii=False)
            + "\n</script>"
        )
        html = html.replace("<!--CATALOG_JSONLD-->", script)
        raw = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def guess_type(self, path):  # explicit, in case mimetypes is sparse
        if path.endswith(".svg"):
            return "image/svg+xml"
        if path.endswith(".woff2"):
            return "font/woff2"
        return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Fitzwilliam Provisions → http://{args.host}:{args.port}/")
    print(f"  UCP profile   /.well-known/ucp")
    print(f"  Catalog       /catalog.json")
    print(f"  Checkout      POST /api/checkout")
    print(f"  402 quote     /api/agent/quote")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nclosed.")


if __name__ == "__main__":
    main()
