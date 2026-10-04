"""Capture fixture-backed dashboard pages and compare stylesheet refactors.

Requires Playwright, Pillow and a Chromium browser in a separate test environment.
Serve dashboard/dist with SPA fallback, then capture a reference and compare the
rebuilt checkout using the same browser and fixture. Never connects to live APIs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGES = {"home": "/", "apps": "/apps", "app": "/apps/mealie", "chat": "/chat", "settings": "/settings/security"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8809")
    parser.add_argument("--browser", required=True, help="Path to the test Chromium executable")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    snapshot = json.loads((ROOT / "tests/fixtures/dashboard-snapshot.json").read_text())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
        for width, height in ((1440, 1000), (390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height}, color_scheme="light")

            def api(route):
                payload = (
                    snapshot
                    if route.request.url.endswith("/api/v1/snapshot")
                    else {
                        "ok": True,
                        "items": {},
                        "servers": [],
                        "summary": {},
                        "providers": [],
                        "handoffs": [],
                        "people": [],
                        "seeded": True,
                        "seeded_at": "",
                        "browser_extension": {"browsers": [], "server_url": "", "signed_in": False},
                    }
                )
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

            page.route("**/api/**", api)
            for name, path in PAGES.items():
                page.goto(args.url + path)
                page.wait_for_selector(".shell")
                page.wait_for_timeout(500)
                filename = f"{name}-{width}.png"
                output = args.output / filename
                page.screenshot(path=str(output), full_page=True, animations="disabled")
                if args.reference:
                    with Image.open(args.reference / filename) as before, Image.open(output) as after:
                        if (
                            before.size != after.size
                            or ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox()
                        ):
                            raise SystemExit(f"Screenshot changed: {filename}")
            page.close()
        browser.close()
    print("10 dashboard screenshots captured" + (" and matched." if args.reference else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
