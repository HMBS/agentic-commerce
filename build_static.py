#!/usr/bin/env python3
"""
Build the static deployment bundle.

Shared hosting can serve files but cannot run server.py, so this produces a dist/
directory that keeps a real JSON API by shipping a PHP shim:

    dist/
      index.html            JSON-LD injected (the placeholder is server-only otherwise)
      catalog.json          generated from products.json
      llms.txt              generated
      .well-known/ucp       generated, forced to application/json by .htaccess
      products.json         read by api/checkout.php
      api/checkout.php      prices baskets, enforces the age gate
      api/quote.php         402 + PAYMENT-REQUIRED
      api/ping.php          is PHP actually executing here?
      assets/...            css, js, svg, fonts
      .htaccess             content types, CORS, cache

    python3 build_static.py --base-url https://store.leobo.me
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import server as core

ROOT = Path(__file__).resolve().parent
COPY = ["assets", "products.json"]
PHP = ["checkout.php", "quote.php", "ping.php"]

HTACCESS = """\
# Fitzwilliam Provisions — static deployment
AddDefaultCharset UTF-8
DirectoryIndex index.html

<IfModule mod_headers.c>
  # UCP profile must be JSON, and it has no file extension to hint at it
  <Files "ucp">
    ForceType application/json
    Header set Cache-Control "public, max-age=3600"
    Header set Access-Control-Allow-Origin "*"
  </Files>

  <FilesMatch "\\.(json)$">
    Header set Access-Control-Allow-Origin "*"
    Header set Cache-Control "public, max-age=60"
  </FilesMatch>

  <FilesMatch "\\.(css|js|svg|woff2)$">
    Header set Cache-Control "public, max-age=604800"
  </FilesMatch>

  Header set X-Content-Type-Options "nosniff"
</IfModule>

<IfModule mod_mime.c>
  AddType application/json .json
  AddType image/svg+xml .svg
  AddType font/woff2 .woff2
</IfModule>

# No directory listings
Options -Indexes
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="https://store.leobo.me",
                    help="public origin used in catalog/JSON-LD links")
    ap.add_argument("--out", default="dist")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    out = ROOT / args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # assets + catalog source
    for item in COPY:
        src = ROOT / item
        dst = out / item
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    # api shims
    (out / "api").mkdir()
    for name in PHP:
        shutil.copy2(ROOT / "api" / name, out / "api" / name)

    # index.html with JSON-LD injected where server.py does it at request time
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    if "<!--CATALOG_JSONLD-->" not in html:
        print("error: JSON-LD placeholder missing from index.html", file=sys.stderr)
        return 1
    script = (
        '<script type="application/ld+json">\n'
        + json.dumps(core.jsonld_doc(base), indent=2, ensure_ascii=False)
        + "\n</script>"
    )
    html = html.replace(
        '<meta name="fv-api" content="/api/checkout">',
        '<meta name="fv-api" content="/api/checkout.php">',
    )
    (out / "index.html").write_text(
        html.replace("<!--CATALOG_JSONLD-->", script), encoding="utf-8"
    )

    # machine documents
    (out / "catalog.json").write_text(
        json.dumps(core.catalog_doc(base), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "llms.txt").write_text(core.llms_txt(base), encoding="utf-8")
    wellk = out / ".well-known"
    wellk.mkdir()
    profile = json.dumps(core.ucp_profile(base), indent=2, ensure_ascii=False) + "\n"
    (wellk / "ucp").write_text(profile, encoding="utf-8")
    # fallback for hosts that hide dot-directories; linked from llms.txt only
    (out / "ucp-profile.json").write_text(profile, encoding="utf-8")
    (out / ".htaccess").write_text(HTACCESS, encoding="utf-8")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    files = [f for f in out.rglob("*") if f.is_file()]
    print(f"built {out.relative_to(ROOT)}/ — {len(files)} files, {total / 1024:.0f} KB")
    for f in sorted(files):
        print(f"  {f.relative_to(out)}")
    print(f"\nupload the CONTENTS of {args.out}/ to the web root, then open:")
    print(f"  {base}/api/ping.php   (expect JSON: proves PHP runs)")
    print(f"  {base}/               (checkout should now price the basket)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
