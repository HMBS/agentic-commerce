#!/usr/bin/env python3
"""End-to-end checks for the Fitzwilliam Provisions MCP server.

Starts mcp_server.py on a spare port and drives the JSON-RPC surface the same
way an MCP client does. Standard library only.

    python3 tests/test_mcp.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8099
URL = f"http://127.0.0.1:{PORT}/mcp"

failures: list[str] = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if condition:
        print(f"PASS {message}")
    else:
        print(f"FAIL {message}")
        failures.append(message)


def post(payload: dict, *, expect_status: int = 200):
    raw = json.dumps(payload).encode()
    request = urllib.request.Request(
        URL,
        data=raw,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    check(status == expect_status, f"HTTP {status} == {expect_status} for {payload.get('method')}")
    return status, body


def rpc(method: str, params: dict | None = None, *, message_id: int = 1):
    payload = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        payload["params"] = params
    status, body = post(payload)
    return json.loads(body)


def call_tool(name: str, arguments: dict, *, message_id: int = 10):
    response = rpc(
        "tools/call",
        {"name": name, "arguments": arguments},
        message_id=message_id,
    )
    check("error" not in response, f"{name} returned a result")
    return response["result"]


def wait_for_server(proc: subprocess.Popen):
    health = f"http://127.0.0.1:{PORT}/health"
    for _ in range(60):
        if proc.poll() is not None:
            raise SystemExit("mcp_server.py exited before it became ready")
        try:
            with urllib.request.urlopen(health, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise SystemExit("mcp_server.py did not become ready")


def main():
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "mcp_server.py"), "--port", str(PORT)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for_server(proc)

        health = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()
        )
        check(health["status"] == "ok", "health reports ok")
        check(health["products"] == 7, "health sees 7 products")

        init = rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
            message_id=1,
        )
        result = init.get("result", {})
        check(result.get("protocolVersion") == "2025-06-18", "initialize negotiates protocol")
        check(result.get("serverInfo", {}).get("name") == "fitzwilliam-provisions", "server name")
        check("tools" in result.get("capabilities", {}), "advertises tools capability")
        check("resources" in result.get("capabilities", {}), "advertises resources capability")

        status, _ = post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_status=202,
        )
        check(status == 202, "initialized notification gets 202")

        tools = rpc("tools/list", message_id=2).get("result", {}).get("tools", [])
        names = {tool["name"] for tool in tools}
        check(
            names == {
                "get_shop_profile",
                "list_products",
                "search_products",
                "get_product",
                "price_basket",
            },
            "tools/list exposes the shop tools",
        )
        for tool in tools:
            check(tool.get("inputSchema", {}).get("type") == "object", f"{tool['name']} has an object schema")

        catalog = call_tool("list_products", {}, message_id=3)
        structured = catalog["structuredContent"]
        check(structured["count"] == 7, "list_products returns 7 lines")
        check(structured["merchant"]["name"] == "Fitzwilliam Provisions", "merchant is present")
        check(
            structured["products"][0]["price_display"] == "18.50",
            "price_display comes from the merchant",
        )
        check(catalog["isError"] is False, "list_products is not an error")

        wine = call_tool("list_products", {"category": "wine"}, message_id=4)
        check(wine["structuredContent"]["count"] == 2, "category filter returns two wines")

        cheap = call_tool("list_products", {"max_price_minor": 1000}, message_id=5)
        check(cheap["structuredContent"]["count"] == 1, "price filter returns one line")
        check(
            cheap["structuredContent"]["products"][0]["id"] == "cho-sea-090",
            "price filter finds the €6.90 chocolate",
        )

        coffee = call_tool("search_products", {"query": "coffee"}, message_id=6)
        check(
            [p["id"] for p in coffee["structuredContent"]["products"]] == ["cof-yir-250"],
            "search for coffee finds the Yirgacheffe",
        )

        wines = call_tool("search_products", {"query": "wine"}, message_id=7)
        check(wines["structuredContent"]["count"] == 2, "search for wine finds both bottles")

        none = call_tool("search_products", {"query": "turnip"}, message_id=8)
        check(none["structuredContent"]["count"] == 0, "search with no match is empty")

        one = call_tool("get_product", {"sku": "FV-WIN-007"}, message_id=9)
        check(one["structuredContent"]["product"]["id"] == "win-bar-2019", "SKU lookup works")

        missing = call_tool("get_product", {"id": "nope"}, message_id=10)
        check(missing["isError"] is True, "unknown product is a tool error")
        check(missing["structuredContent"]["error"]["code"] == "unknown_item", "unknown_item code")

        priced = call_tool(
            "price_basket",
            {"items": [{"id": "cof-yir-250", "quantity": 2}]},
            message_id=11,
        )
        session = priced["structuredContent"]["checkout_session"]
        check(priced["isError"] is False, "unrestricted basket prices cleanly")
        check(session["total"] == 3700, "two coffees total €37.00")
        check(session["age_assertion"]["method"] == "none", "age assertion is absent by default")

        blocked = call_tool(
            "price_basket",
            {"items": [{"id": "win-pet-2023", "quantity": 1}]},
            message_id=12,
        )
        check(blocked["isError"] is True, "wine without age is refused")
        check(
            blocked["structuredContent"]["error"]["code"] == "age_verification_required",
            "age gate returns the actionable code",
        )
        check(
            blocked["structuredContent"]["error"]["recovery"] == "verify_age",
            "age gate tells the caller how to recover",
        )

        allowed = call_tool(
            "price_basket",
            {"items": [{"id": "win-pet-2023", "quantity": 1}], "age_verified": True},
            message_id=13,
        )
        check(allowed["isError"] is False, "wine prices after the age assertion")
        check(
            allowed["structuredContent"]["checkout_session"]["age_assertion"]["method"]
            == "self_declared",
            "age assertion is marked self-declared",
        )

        bad_qty = call_tool(
            "price_basket",
            {"items": [{"id": "cof-yir-250", "quantity": 0}]},
            message_id=14,
        )
        check(bad_qty["isError"] is True, "invalid quantity is refused")

        resources = rpc("resources/list", message_id=15).get("result", {}).get("resources", [])
        uris = {resource["uri"] for resource in resources}
        check("shop://catalog" in uris, "catalog resource is listed")
        check("shop://policy/age" in uris, "age policy resource is listed")

        catalog_resource = rpc(
            "resources/read", {"uri": "shop://catalog"}, message_id=16
        )["result"]["contents"][0]
        check(
            json.loads(catalog_resource["text"])["count"] == 7,
            "catalog resource carries 7 products",
        )

        product_resource = rpc(
            "resources/read", {"uri": "shop://products/cof-yir-250"}, message_id=17
        )["result"]["contents"][0]
        check(
            json.loads(product_resource["text"])["product"]["id"] == "cof-yir-250",
            "product resource template resolves",
        )

        bad_resource = rpc(
            "resources/read", {"uri": "shop://products/nope"}, message_id=18
        )
        check("error" in bad_resource, "unknown resource is a JSON-RPC error")
        check(bad_resource["error"]["code"] == -32002, "resource not found uses -32002")

        unknown = rpc(
            "tools/call", {"name": "not_a_tool", "arguments": {}}, message_id=19
        )
        check("error" in unknown, "unknown tool is a JSON-RPC error")
        check(unknown["error"]["code"] == -32602, "unknown tool uses -32602")

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/mcp", timeout=2) as response:
                get_status = response.status
        except urllib.error.HTTPError as exc:
            get_status = exc.code
        check(get_status == 405, "GET /mcp returns 405")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if failures:
        print(f"{len(failures)} of {checks} checks failed:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print(f"All {checks} checks passed.")


if __name__ == "__main__":
    main()
