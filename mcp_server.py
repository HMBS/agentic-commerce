#!/usr/bin/env python3
"""
Fitzwilliam Provisions — MCP server.

A small Model Context Protocol server that exposes the shop to agents and to
the Helping Bee Android app. It speaks JSON-RPC 2.0 over Streamable HTTP at
POST /mcp and reuses the catalog, VAT arithmetic and age gate from server.py,
so the shelf has one source of truth.

Tools
  get_shop_profile   merchant, currency, age policy, categories
  list_products      the shelf, optionally filtered by category/price/stock
  search_products    free-text discovery (names, notes, SKUs, aliases)
  get_product        one product by id or SKU
  price_basket       re-price a basket and enforce the 18+ gate

Resources
  shop://catalog            full machine catalog
  shop://profile            merchant profile
  shop://policy/age         age policy in plain text
  shop://llms.txt           the store's llms.txt map
  shop://products/{id}      one product

Run:
    python3 mcp_server.py                      # 127.0.0.1:8090
    python3 mcp_server.py --host 0.0.0.0       # reachable from an Android emulator

For a hosted deployment, the usual environment variables are also read:
    HOST=0.0.0.0 PORT=10000 STORE_URL=https://store.example.com
Command-line flags take precedence over the environment.

The storefront in server.py is still the place that serves images and the
landing page. Point --store-url at it when the default derived URL is wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as store  # noqa: E402  (catalog + price_basket + llms_txt)
import catalog_source  # noqa: E402  (optional Supabase catalog)

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "fitzwilliam-provisions"
SERVER_VERSION = "1.0.0"
MAX_BODY = 256_000
STORE_PORT = 8088

# A caller can override this with --store-url. Otherwise the store URL is
# derived from the Host header so an emulator talking to 10.0.2.2:8090 gets
# image URLs at 10.0.2.2:8088 rather than localhost.
STORE_URL: str | None = None

def categories() -> list[str]:
    return sorted({p["category"] for p in store.PRODUCTS.values()})

# Search words a shopper uses that do not appear in the product's own copy.
ALIASES = {
    "cof-yir-250": ["coffee", "beans", "espresso", "filter", "yirgacheffe", "gasharu"],
    "cho-sea-090": ["chocolate", "cacao", "sea salt", "seventy", "bar"],
    "oil-kor-500": ["olive oil", "oil", "koroneiki", "greek", "salad"],
    "kit-sour-001": ["sourdough", "bread", "starter", "baking", "rye", "culture"],
    "kni-gyu-210": ["knife", "gyuto", "blade", "chef", "carbon steel", "shirogami"],
    "win-pet-2023": ["wine", "pet nat", "pét-nat", "sparkling", "skin contact", "bottle"],
    "win-bar-2019": ["wine", "barolo", "nebbiolo", "red", "bottle", "piedmont"],
}

SEARCH_STOP_WORDS = {
    "a", "add", "an", "and", "any", "anything", "are", "as", "at", "be",
    "bottle", "buy", "can", "could", "do", "for", "from", "get", "give",
    "have", "i", "in", "is", "it", "like", "me", "my", "of", "on", "or",
    "order", "please", "show", "some", "something", "take", "tell", "the",
    "to", "want", "what", "whats", "with", "would", "you", "your", "under",
    "over", "below", "above", "euro", "eur", "cheap", "cheapest",
}


# ── helpers ────────────────────────────────────────────────────────────────

class RpcError(Exception):
    """A JSON-RPC error that should be returned to the caller."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ToolError(Exception):
    """A tool-level failure: returned as isError rather than a protocol error."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def payload(self) -> dict:
        error = {"code": self.code, "message": self.message}
        error.update(self.details)
        return {"error": error}


def store_base(handler: BaseHTTPRequestHandler) -> str:
    if STORE_URL:
        return STORE_URL.rstrip("/")
    host = handler.headers.get("Host") or f"localhost:{STORE_PORT}"
    hostname = host.split(":")[0]
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return f"http://localhost:{STORE_PORT}"
    return f"http://{hostname}:{STORE_PORT}"


def catalog_for(base: str, products: list[dict] | None = None) -> dict:
    """The same wire shape as /catalog.json, optionally narrowed to products."""
    doc = store.catalog_doc(base)
    if products is not None:
        wanted = [p["id"] for p in products]
        by_id = {p["id"]: p for p in doc["products"]}
        doc["products"] = [by_id[pid] for pid in wanted if pid in by_id]
        doc["count"] = len(doc["products"])
    return doc


def product_wire(base: str, product: dict) -> dict:
    doc = catalog_for(base, [product])
    return doc["products"][0] if doc["products"] else {}


def money(product: dict) -> str:
    return f"€{product['price'] / 100:.2f}"


def product_name(product: dict) -> str:
    return f"{product['name']} ({product['sku']}, {money(product)})"


def summary_list(products: list[dict], limit: int = 8) -> str:
    names = [product_name(p) for p in products[:limit]]
    text = ", ".join(names)
    if len(products) > limit:
        text += f", and {len(products) - limit} more"
    return text


def validate_args(args, allowed: set[str]) -> dict:
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise RpcError(-32602, "Tool arguments must be an object.")
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise RpcError(-32602, f"Unknown argument(s): {', '.join(unknown)}")
    return args


def int_arg(args: dict, name: str, *, minimum: int, maximum: int, default: int | None = None):
    value = args.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RpcError(-32602, f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        raise RpcError(-32602, f"{name} must be between {minimum} and {maximum}.")
    return value


def bool_arg(args: dict, name: str, default: bool = False) -> bool:
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise RpcError(-32602, f"{name} must be a boolean.")
    return value


def category_arg(args: dict) -> str | None:
    value = args.get("category")
    if value is None:
        return None
    available = categories()
    if not isinstance(value, str) or value not in available:
        raise RpcError(
            -32602,
            f"category must be one of: {', '.join(available)}.",
        )
    return value


# ── tool implementation ───────────────────────────────────────────────────

def tool_result(payload: dict, summary: str, *, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": summary}],
        "structuredContent": payload,
        "isError": is_error,
    }


def tool_profile(base: str, mcp_url: str) -> dict:
    profile = {
        "merchant": store.MERCHANT,
        "currency": store.MERCHANT["currency"],
        "product_count": len(store.PRODUCTS),
        "categories": categories(),
        "age_policy": store.MERCHANT["age_policy"],
        "money": "Minor units (euro cents), VAT-inclusive.",
        "store_url": f"{base}/",
        "catalog_url": f"{base}/catalog.json",
        "checkout_url": f"{base}/api/checkout",
        "mcp_endpoint": mcp_url,
        "tools": [t["name"] for t in TOOLS],
        "resources": [r["uri"] for r in RESOURCES],
        "notes": [
            "Test shop. No payment is taken and no order is fulfilled.",
            "Age assertions are self-declared; price_basket enforces the gate.",
            "Prices are VAT-inclusive EUR; food is zero-rated, alcohol and kitchen goods are 23%.",
        ],
    }
    return tool_result(
        profile,
        f"{store.MERCHANT['name']}: {len(store.PRODUCTS)} products, "
        f"{len(categories())} categories, prices in {store.MERCHANT['currency']}.",
    )


def tool_list(base: str, args: dict) -> dict:
    args = validate_args(
        args,
        {"category", "in_stock_only", "max_price_minor", "limit"},
    )
    category = category_arg(args)
    in_stock_only = bool_arg(args, "in_stock_only", False)
    max_price = int_arg(args, "max_price_minor", minimum=0, maximum=10_000_00)
    limit = int_arg(args, "limit", minimum=1, maximum=100, default=50)

    products = list(store.PRODUCTS.values())
    if category:
        products = [p for p in products if p["category"] == category]
    if in_stock_only:
        products = [p for p in products if p["stock"] > 0]
    if max_price is not None:
        products = [p for p in products if p["price"] <= max_price]
    products = products[:limit]

    payload = catalog_for(base, products)
    if not products:
        return tool_result(payload, "No products match those filters.")
    return tool_result(
        payload,
        f"{len(products)} product(s): {summary_list(products)}.",
    )


def score_product(product: dict, terms: list[str]) -> int:
    name = product["name"].lower()
    subtitle = product["subtitle"].lower()
    notes = " ".join(product["notes"]).lower()
    category = product["category"].lower()
    alias = " ".join(
        [*product.get("aliases", []), *ALIASES.get(product["id"], [])]
    ).lower()
    sku = product["sku"].lower()
    pid = product["id"].lower()

    score = 0
    for term in terms:
        if term in name:
            score += 5
        if term in alias:
            score += 4
        if term in subtitle:
            score += 2
        if term in notes:
            score += 2
        if term in category:
            score += 1
        if term in sku or term in pid:
            score += 2
        if term not in name and len(term) >= 3:
            if any(word.startswith(term) for word in re.findall(r"[a-z0-9]+", name)):
                score += 3
    return score


def search_terms(query: str) -> list[str]:
    raw = re.findall(r"[a-z0-9\u00c0-\u024f]+", query.lower())
    terms = []
    for token in raw:
        if token in SEARCH_STOP_WORDS or len(token) < 2:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def tool_search(base: str, args: dict) -> dict:
    args = validate_args(
        args,
        {"query", "category", "in_stock_only", "max_price_minor", "limit"},
    )
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise RpcError(-32602, "query is required and must be a non-empty string.")
    category = category_arg(args)
    in_stock_only = bool_arg(args, "in_stock_only", False)
    max_price = int_arg(args, "max_price_minor", minimum=0, maximum=10_000_00)
    limit = int_arg(args, "limit", minimum=1, maximum=100, default=20)

    terms = search_terms(query)
    if not terms:
        raise RpcError(-32602, "query did not contain any searchable words.")

    candidates = list(store.PRODUCTS.values())
    if category:
        candidates = [p for p in candidates if p["category"] == category]
    if in_stock_only:
        candidates = [p for p in candidates if p["stock"] > 0]
    if max_price is not None:
        candidates = [p for p in candidates if p["price"] <= max_price]

    scored = [(score_product(p, terms), p) for p in candidates]
    scored = [(score, p) for score, p in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1]["name"]))
    products = [p for _, p in scored[:limit]]

    payload = catalog_for(base, products)
    payload["query"] = query
    if not products:
        return tool_result(
            payload,
            f"No products match {query!r}.",
        )
    return tool_result(
        payload,
        f"{len(products)} product(s) match {query!r}: {summary_list(products)}.",
    )


def find_product(args: dict) -> dict:
    args = validate_args(args, {"id", "sku"})
    pid = args.get("id")
    sku = args.get("sku")
    if not pid and not sku:
        raise RpcError(-32602, "Provide either id or sku.")
    if pid is not None and not isinstance(pid, str):
        raise RpcError(-32602, "id must be a string.")
    if sku is not None and not isinstance(sku, str):
        raise RpcError(-32602, "sku must be a string.")

    product = None
    if pid:
        product = store.PRODUCTS.get(pid)
    if product is None and sku:
        product = next(
            (p for p in store.PRODUCTS.values() if p["sku"].lower() == sku.lower()),
            None,
        )
    if product is None:
        raise ToolError(
            "unknown_item",
            f"No product with id {pid!r} or SKU {sku!r} on this shelf.",
        )
    return product


def tool_get_product(base: str, args: dict) -> dict:
    product = find_product(args)
    payload = {
        "product": product_wire(base, product),
        "merchant": store.MERCHANT,
        "currency": store.MERCHANT["currency"],
    }
    return tool_result(payload, f"{product_name(product)} — {product['subtitle']}.")


def tool_price_basket(base: str, args: dict) -> dict:
    args = validate_args(args, {"items", "age_verified"})
    items = args.get("items")
    if not isinstance(items, list) or not items:
        raise RpcError(-32602, "items must be a non-empty array.")
    if len(items) > 50:
        raise RpcError(-32602, "items must contain at most 50 lines.")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RpcError(-32602, f"items[{index}] must be an object.")
        pid = item.get("id")
        quantity = item.get("quantity", 1)
        if not isinstance(pid, str) or not pid:
            raise RpcError(-32602, f"items[{index}].id must be a non-empty string.")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise RpcError(-32602, f"items[{index}].quantity must be an integer.")

    age_verified = args.get("age_verified", False)
    if not isinstance(age_verified, bool):
        raise RpcError(-32602, "age_verified must be a boolean.")

    status, payload = store.price_basket(
        {"items": items, "age_verified": age_verified}
    )
    if status != 200:
        error = payload.get("error", {})
        return tool_result(
            payload,
            error.get("message", "The basket could not be priced."),
            is_error=True,
        )

    session = payload["checkout_session"]
    total = f"€{session['total'] / 100:.2f}"
    return tool_result(
        payload,
        f"Basket priced at {total} for {len(session['lines'])} line(s) "
        f"({session['status']}).",
    )


# ── tool catalog ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_shop_profile",
        "title": "Get shop profile",
        "description": (
            "Merchant identity, currency, age policy, categories and machine "
            "endpoints. Call this first to learn what the shop sells and how "
            "checkout behaves."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "list_products",
        "title": "List the shelf",
        "description": (
            "List products from the live shelf, with prices in minor units, "
            "stock, VAT and age-restriction codes. Optionally filter by "
            "category, availability and maximum price."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Only return this category (for example pantry, wine or kitchen).",
                },
                "in_stock_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Hide sold-out lines.",
                },
                "max_price_minor": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Maximum shelf price in euro cents.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "search_products",
        "title": "Search the shelf",
        "description": (
            "Free-text product discovery across names, descriptions, tasting "
            "notes, SKUs and shopper aliases such as 'coffee' or 'wine'. "
            "Returns matching products as a catalog."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 2,
                    "description": "What the shopper is looking for.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter.",
                },
                "in_stock_only": {
                    "type": "boolean",
                    "default": False,
                },
                "max_price_minor": {
                    "type": "integer",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_product",
        "title": "Get one product",
        "description": "Look up a single product by id or SKU.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Product id."},
                "sku": {"type": "string", "description": "Merchant SKU."},
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "price_basket",
        "title": "Price a basket",
        "description": (
            "Re-price a basket from the live shelf and enforce the 18+ gate. "
            "Sends only ids and quantities; prices always come from the "
            "merchant. Returns a checkout session or an actionable age error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Product id from the shelf.",
                            },
                            "quantity": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 99,
                                "default": 1,
                            },
                        },
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                },
                "age_verified": {
                    "type": "boolean",
                    "default": False,
                    "description": "Assert that an adult age check has happened.",
                },
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]

TOOL_HANDLERS = {
    "get_shop_profile": lambda base, args, mcp_url: tool_profile(base, mcp_url),
    "list_products": lambda base, args, mcp_url: tool_list(base, args),
    "search_products": lambda base, args, mcp_url: tool_search(base, args),
    "get_product": lambda base, args, mcp_url: tool_get_product(base, args),
    "price_basket": lambda base, args, mcp_url: tool_price_basket(base, args),
}


# ── resources ─────────────────────────────────────────────────────────────

RESOURCES = [
    {
        "uri": "shop://catalog",
        "name": "catalog",
        "title": "Full shelf catalog",
        "description": "Every product with price, stock, VAT and restriction codes.",
        "mimeType": "application/json",
    },
    {
        "uri": "shop://profile",
        "name": "profile",
        "title": "Merchant profile",
        "description": "Merchant identity, currency and age policy.",
        "mimeType": "application/json",
    },
    {
        "uri": "shop://policy/age",
        "name": "age-policy",
        "title": "Age policy",
        "description": "The shop's 18+ policy in plain text.",
        "mimeType": "text/plain",
    },
    {
        "uri": "shop://llms.txt",
        "name": "llms-txt",
        "title": "llms.txt",
        "description": "The store's plain-text map for language models.",
        "mimeType": "text/plain",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "shop://products/{productId}",
        "name": "product",
        "title": "Product detail",
        "description": "One product by id.",
        "mimeType": "application/json",
    },
]


def read_resource(uri: str, base: str, mcp_url: str) -> dict:
    if uri == "shop://catalog":
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(catalog_for(base), indent=2, ensure_ascii=False),
        }
    if uri == "shop://profile":
        profile = tool_profile(base, mcp_url)["structuredContent"]
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(profile, indent=2, ensure_ascii=False),
        }
    if uri == "shop://policy/age":
        policy = (
            f"# Age policy — {store.MERCHANT['name']}\n\n"
            f"{store.MERCHANT['age_policy']}\n\n"
            "Age-restricted lines: "
            + ", ".join(
                f"{p['name']} ({p['restriction']})"
                for p in store.PRODUCTS.values()
                if p["restriction"]
            )
            + "\n\nThis test build uses a self-declared assertion. It is not "
            "identity verification.\n"
        )
        return {"uri": uri, "mimeType": "text/plain", "text": policy}
    if uri == "shop://llms.txt":
        return {
            "uri": uri,
            "mimeType": "text/plain",
            "text": store.llms_txt(base),
        }
    if uri.startswith("shop://products/"):
        pid = unquote(uri[len("shop://products/"):])
        product = store.PRODUCTS.get(pid)
        if product is None:
            raise RpcError(-32002, f"Resource not found: {uri}", {"uri": uri})
        payload = {
            "product": product_wire(base, product),
            "merchant": store.MERCHANT,
        }
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(payload, indent=2, ensure_ascii=False),
        }
    raise RpcError(-32002, f"Resource not found: {uri}", {"uri": uri})


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────

def rpc_error(message_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def handle_initialize(params: dict) -> dict:
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    version = (
        requested
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
        else PROTOCOL_VERSION
    )
    return {
        "protocolVersion": version,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "title": "Fitzwilliam Provisions shelf",
            "version": SERVER_VERSION,
        },
        "instructions": (
            "Fitzwilliam Provisions is a small test grocery in Dublin. Use "
            "list_products or search_products to discover items, get_product "
            "for one line, and price_basket to re-price a basket. Prices are "
            "VAT-inclusive EUR minor units. Alcohol and bladed kitchen goods "
            "are 18+; price_basket refuses them unless age_verified is true. "
            "No payment is taken and no order is fulfilled."
        ),
    }


def handle_rpc(message, handler: BaseHTTPRequestHandler):
    if not isinstance(message, dict):
        raise RpcError(-32600, "Invalid Request: expected a JSON object.")

    message_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        if is_notification:
            return None
        raise RpcError(-32600, "Invalid Request: jsonrpc must be '2.0' and method is required.")

    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RpcError(-32602, "params must be an object.")

    # Keep the shelf fresh without making the app redeploy for each product.
    # This is a no-op unless Supabase is configured.
    catalog_source.ensure_fresh(store)

    base = store_base(handler)
    # The MCP URL is only used in the profile payload; derive it from the
    # request instead of guessing the scheme/port.
    forwarded = handler.headers.get("Host") or f"localhost:{handler.server.server_port}"
    mcp_url = f"http://{forwarded}/mcp"

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        result = {}
    elif method == "initialize":
        result = handle_initialize(params)
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not name:
            raise RpcError(-32602, "tools/call requires a tool name.")
        if name not in TOOL_HANDLERS:
            raise RpcError(-32602, f"Unknown tool: {name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "Tool arguments must be an object.")
        handler.log_message("tools/call %s", name)
        try:
            result = TOOL_HANDLERS[name](base, arguments, mcp_url)
        except ToolError as exc:
            result = tool_result(
                exc.payload(),
                exc.message,
                is_error=True,
            )
    elif method == "resources/list":
        result = {"resources": RESOURCES}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": RESOURCE_TEMPLATES}
    elif method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise RpcError(-32602, "resources/read requires a uri.")
        result = {"contents": [read_resource(uri, base, mcp_url)]}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        raise RpcError(-32601, f"Method not found: {method}")

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


# ── HTTP transport ────────────────────────────────────────────────────────

class McpHandler(BaseHTTPRequestHandler):
    server_version = "FitzwilliamMCP/1.0"

    def _path(self) -> str:
        return urlparse(self.path).path.rstrip("/") or "/"

    def _cors(self, extra: dict | None = None):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id",
        )
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        for key, value in (extra or {}).items():
            self.send_header(key, value)

    def _send_json(self, status: int, payload, extra: dict | None = None):
        raw = json.dumps(payload, indent=2, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._cors(extra)
        self.end_headers()
        self.wfile.write(raw)

    def _send_empty(self, status: int, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._cors(extra)
        self.end_headers()

    def do_OPTIONS(self):
        self._send_empty(HTTPStatus.NO_CONTENT)

    def do_GET(self):
        path = self._path()
        if path == "/health":
            catalog = catalog_source.status(store)
            self._send_json(
                200,
                {
                    "status": "ok",
                    "server": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "protocolVersion": PROTOCOL_VERSION,
                    "products": len(store.PRODUCTS),
                    "categories": categories(),
                    "catalogSource": catalog.source,
                    "catalogError": catalog.error,
                    "transport": "streamable-http",
                    "endpoint": "/mcp",
                },
            )
            return
        if path == "/":
            self._send_json(
                200,
                {
                    "name": SERVER_NAME,
                    "description": "Fitzwilliam Provisions MCP server",
                    "endpoint": "/mcp",
                    "health": "/health",
                    "transport": "streamable-http",
                    "protocolVersion": PROTOCOL_VERSION,
                    "tools": [t["name"] for t in TOOLS],
                },
            )
            return
        if path == "/mcp":
            # This server does not open a server-initiated SSE stream.
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "Use POST /mcp for JSON-RPC requests."},
                {"Allow": "POST"},
            )
            return
        self._send_json(404, {"error": f"Not found: {path}"})

    def do_DELETE(self):
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "This MCP server is stateless; there is no session to delete."},
            {"Allow": "POST"},
        )

    def do_POST(self):
        path = self._path()
        if path != "/mcp":
            self._send_json(404, {"error": f"Not found: {path}"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ValueError("empty body")
            if length > MAX_BODY:
                raise ValueError("body too large")
            raw = self.rfile.read(length)
            message = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(
                400,
                rpc_error(None, -32700, f"Parse error: {exc}"),
            )
            return

        if isinstance(message, list):
            self._send_json(
                200,
                rpc_error(None, -32600, "Batch requests are not supported."),
            )
            return

        notification = isinstance(message, dict) and "id" not in message
        try:
            response = handle_rpc(message, self)
        except RpcError as exc:
            if notification:
                self._send_empty(HTTPStatus.ACCEPTED)
                return
            response = rpc_error(message.get("id") if isinstance(message, dict) else None,
                                 exc.code, exc.message, exc.data)
        except Exception as exc:  # keep the wire clean on unexpected bugs
            self.log_message("internal error: %s", exc)
            if notification:
                self._send_empty(HTTPStatus.ACCEPTED)
                return
            response = rpc_error(message.get("id") if isinstance(message, dict) else None,
                                 -32603, f"Internal error: {exc}")

        if response is None:
            self._send_empty(HTTPStatus.ACCEPTED)
            return
        self._send_json(200, response)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


def main():
    global STORE_URL
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8090")))
    ap.add_argument(
        "--store-url",
        default=os.environ.get("STORE_URL") or None,
        help=(
            "Public base URL of the storefront used in product links and "
            "images (default: derive http://<request host>:%d)." % STORE_PORT
        ),
    )
    args = ap.parse_args()
    STORE_URL = args.store_url

    catalog = catalog_source.ensure_fresh(store, force=True)
    catalog_line = f"  catalog       {catalog.source} ({catalog.product_count} products)"
    if catalog.error:
        catalog_line += f" — {catalog.error}"

    srv = ThreadingHTTPServer((args.host, args.port), McpHandler)
    print(f"Fitzwilliam Provisions MCP → http://{args.host}:{args.port}/mcp")
    print(f"  health        /health")
    print(f"  tools         {', '.join(t['name'] for t in TOOLS)}")
    print(f"  store base    {STORE_URL or f'http://<request host>:{STORE_PORT}'}")
    print(catalog_line)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nclosed.")


if __name__ == "__main__":
    main()
