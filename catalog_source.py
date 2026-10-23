"""Supabase-backed catalog for the Fitzwilliam Provisions MCP server.

The MCP process can either keep using the repository's products.json (the
default, useful for offline tests) or refresh its shelf from Supabase. Supabase
is used when SUPABASE_URL and a Supabase key are present.

Environment:
    SUPABASE_URL                 https://<project>.supabase.co
    SUPABASE_ANON_KEY            public anon key (read-only RLS policy)
    SUPABASE_SERVICE_ROLE_KEY    optional server-side alternative
    SUPABASE_PRODUCTS_TABLE      default: products
    CATALOG_CACHE_SECONDS        default: 60
    SUPABASE_TIMEOUT_SECONDS     default: 10

The module only performs GET requests. It never writes to the catalog; products
are managed in the Supabase dashboard or another trusted backend.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CatalogStatus:
    configured: bool
    source: str
    product_count: int
    fetched_at: float | None
    error: str | None


_lock = threading.Lock()
_next_attempt_at = 0.0
_fetched_at: float | None = None
_last_source = "local"
_last_error: str | None = None


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _base_url() -> str:
    return _env("SUPABASE_URL").rstrip("/")


def _api_key() -> str:
    return _env("SUPABASE_SERVICE_ROLE_KEY") or _env("SUPABASE_ANON_KEY")


def configured() -> bool:
    return bool(_base_url() and _api_key())


def _cache_seconds() -> float:
    raw = _env("CATALOG_CACHE_SECONDS", "60")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def _timeout_seconds() -> float:
    raw = _env("SUPABASE_TIMEOUT_SECONDS", "10")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 10.0


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _row_to_product(row: dict[str, Any]) -> dict[str, Any]:
    """Map a Supabase row onto the shape expected by server.py and MCP."""
    price = row.get("price_minor", row.get("price"))
    if price is None:
        raise ValueError(f"product {row.get('id')!r} is missing price_minor")
    image = row.get("image_url") or row.get("image") or ""
    restriction = row.get("age_restriction") or row.get("restriction") or None
    subtitle = row.get("subtitle") or row.get("description") or ""
    notes = _as_list(row.get("notes"))
    tags = _as_list(row.get("search_tags"))
    product = {
        "id": str(row["id"]),
        "sku": str(row["sku"]),
        "name": str(row["name"]),
        "subtitle": str(subtitle),
        "category": str(row["category"]),
        "price": int(price),
        "restriction": str(restriction) if restriction else None,
        "stock": int(row.get("stock") or 0),
        "image": str(image),
        "notes": notes,
        "aliases": tags,
    }
    return product


def _fetch_products() -> list[dict[str, Any]]:
    table = urllib.parse.quote(_env("SUPABASE_PRODUCTS_TABLE", "products"), safe="")
    query = urllib.parse.urlencode(
        {
            "select": "*",
            "active": "eq.true",
            "order": "name.asc",
        }
    )
    url = f"{_base_url()}/rest/v1/{table}?{query}"
    key = _api_key()
    headers = {
        "apikey": key,
        "Accept": "application/json",
    }
    # Legacy Supabase keys are JWTs and also go in Authorization. New
    # publishable/secret keys are identified by apikey alone.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Supabase returned HTTP {exc.code}: {detail}") from exc

    if not isinstance(payload, list):
        raise RuntimeError("Supabase products response was not a JSON array")
    return [_row_to_product(row) for row in payload if isinstance(row, dict)]


def ensure_fresh(store: Any, *, force: bool = False) -> CatalogStatus:
    """Refresh the in-process shelf when the cache is stale.

    A failed refresh keeps the previous catalog, so a Supabase outage does not
    take the shop offline. The MCP server retries after a short backoff.
    """
    global _next_attempt_at, _fetched_at, _last_source, _last_error

    if not configured():
        with _lock:
            _last_source = "local"
            return CatalogStatus(
                configured=False,
                source=_last_source,
                product_count=len(store.PRODUCTS),
                fetched_at=_fetched_at,
                error=None,
            )

    now = time.monotonic()
    with _lock:
        if not force and now < _next_attempt_at:
            return CatalogStatus(
                configured=True,
                source=_last_source,
                product_count=len(store.PRODUCTS),
                fetched_at=_fetched_at,
                error=_last_error,
            )
        # Reserve the attempt before the network call so concurrent requests
        # do not all hit Supabase at once.
        _next_attempt_at = now + _cache_seconds()

    try:
        products = _fetch_products()
        by_id = {product["id"]: product for product in products}
        with _lock:
            store.PRODUCTS = by_id
            store.CATALOG = {
                "merchant": store.MERCHANT,
                "products": list(by_id.values()),
            }
            _last_source = "supabase"
            _last_error = None
            _fetched_at = time.time()
            _next_attempt_at = time.monotonic() + _cache_seconds()
        return CatalogStatus(
            configured=True,
            source="supabase",
            product_count=len(by_id),
            fetched_at=_fetched_at,
            error=None,
        )
    except Exception as exc:  # keep the last good shelf on failure
        with _lock:
            _last_source = "local-fallback"
            _last_error = str(exc)
            # Retry sooner than the normal TTL so a transient Render/Supabase
            # hiccup does not leave the demo stale for a full minute.
            _next_attempt_at = time.monotonic() + min(_cache_seconds(), 15.0)
            product_count = len(store.PRODUCTS)
        return CatalogStatus(
            configured=True,
            source="local-fallback",
            product_count=product_count,
            fetched_at=_fetched_at,
            error=_last_error,
        )


def status(store: Any) -> CatalogStatus:
    with _lock:
        return CatalogStatus(
            configured=configured(),
            source=_last_source,
            product_count=len(store.PRODUCTS),
            fetched_at=_fetched_at,
            error=_last_error,
        )
