"""M5 exit test: every number on the results page exists in results.json.

Renders replay/index.html in headless Chromium, takes the visible text outside
figures (axis ticks are scale, not claims) and outside the live race counters
(they interpolate while the race plays and settle on results.json values),
and checks that every token containing a digit appears verbatim in results.json.

    python -m analysis.check_page [--config config.yaml]
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import socketserver
import sys
import threading
from functools import partial

from relay.config import Config

TOKEN = re.compile(r"\S*\d\S*")
STRIP = "()[],;:.·'\"“”"


def _launch(p):
    """Playwright's bundled browser, or an existing Chromium via CHROMIUM_PATH."""
    import glob

    try:
        return p.chromium.launch()
    except Exception:
        cands = [os.environ.get("CHROMIUM_PATH", "")] + sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux64/chrome"))
        for c in cands:
            if c and os.path.exists(c):
                return p.chromium.launch(executable_path=c, args=["--headless", "--no-sandbox"])
        raise


def page_text(root: str, results_rel: str) -> tuple[str, list[str]]:
    from playwright.sync_api import sync_playwright

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    handler = partial(Quiet, directory=root)
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with sync_playwright() as p:
            b = _launch(p)
            page = b.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/replay/index.html?results={results_rel}")
            page.wait_for_selector("#failures", timeout=15000)
            page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
            text = page.evaluate("""() => {
                const main = document.querySelector('main');
                main.querySelectorAll('[data-figure], [data-live], select, option, script, style').forEach(e => e.remove());
                return main.innerText;
            }""")
            b.close()
            httpd.shutdown()
    return text, errors


def check(text: str, results: dict) -> list[str]:
    blob = json.dumps(results, ensure_ascii=False)
    # JSON escapes newlines and quotes; compare against the unescaped strings too
    flat: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                flat.append(str(k))
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        else:
            flat.append(str(x))

    walk(results)
    hay = blob + "\n" + "\n".join(flat)
    missing = []
    for tok in TOKEN.findall(text):
        t = tok.strip(STRIP)
        if t.endswith(("'s", "\u2019s")):
            t = t[:-2]
        if not t or not any(c.isdigit() for c in t):
            continue
        if t not in hay:
            missing.append(t)
    return sorted(set(missing))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.environ.get("RELAY_CONFIG", "config.yaml"))
    args = ap.parse_args(argv)
    cfg = Config.load(args.config)
    rj = cfg.path("results_json")
    results = json.loads(rj.read_text())
    root = str(cfg.root)
    rel = os.path.relpath(rj, cfg.root / "replay")
    text, errors = page_text(root, rel)
    missing = check(text, results)
    n = len([t for t in TOKEN.findall(text) if any(c.isdigit() for c in t)])
    if errors:
        print("page errors:\n  " + "\n  ".join(errors))
    if missing:
        print(f"FAIL: {len(missing)} numbers on the page are not in {rj.name}:")
        for m in missing:
            print(f"  {m}")
        return 1
    print(f"PASS: all {n} numeric tokens on the page appear in {rj.name}" + ("" if not errors else " (with page errors)"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
