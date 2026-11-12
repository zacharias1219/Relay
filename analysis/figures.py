"""Static SVG figures built from results.json data. No plotting library needed.

Colours follow a validated categorical order (blue, orange, aqua) with
separate light and dark steps; text stays in ink colours, never series colours.
Each mark carries a <title> so hovering shows its exact value when the SVG is
inlined in the page.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

STYLE = """
<style>
  .surface { fill: #fcfcfb; }
  .ink { fill: #0b0b0b; } .ink2 { fill: #52514e; } .muted { fill: #898781; }
  .grid { stroke: #e1e0d9; stroke-width: 1; } .axis { stroke: #c3c2b7; stroke-width: 1; }
  .zero { stroke: #52514e; stroke-width: 1; stroke-dasharray: 3 3; }
  .s1 { fill: #2a78d6; stroke: #2a78d6; } .s2 { fill: #eb6834; stroke: #eb6834; } .s3 { fill: #1baf7a; stroke: #1baf7a; }
  .dot { fill: #0b0b0b; stroke: #fcfcfb; stroke-width: 2; } .whisker { stroke: #52514e; stroke-width: 2; }
  .gap { stroke: #fcfcfb; stroke-width: 2; }
  text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size: 12px; }
  .title { font-size: 13px; font-weight: 600; }
  .tab { font-variant-numeric: tabular-nums; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .ink { fill: #f0efec; } .ink2 { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; } .axis { stroke: #383835; } .zero { stroke: #c3c2b7; }
    .s1 { fill: #3987e5; stroke: #3987e5; } .s2 { fill: #d95926; stroke: #d95926; } .s3 { fill: #199e70; stroke: #199e70; }
    .dot { fill: #f0efec; stroke: #1a1a19; } .whisker { stroke: #c3c2b7; } .gap { stroke: #1a1a19; }
  }
</style>
"""


def _svg(w: int, h: int, body: str, label: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{escape(label)}">{STYLE}<rect class="surface" width="{w}" height="{h}" rx="8"/>{body}</svg>\n')


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    import math
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = math.floor(lo / step) * step
    out, v = [], start
    while v <= hi + step * 1e-9:
        out.append(round(v, 12))
        v += step
    return out


def pass_vs_cost(policies: list[dict]) -> str:
    """Pass rate (y) against cost per solved task (x), each with its 95% interval."""
    W, H, L, R, T, B = 640, 360, 72, 24, 40, 48
    pts = [p for p in policies if p["cost_per_solved"]["point"] is not None]
    xs = [v for p in pts for v in (p["cost_per_solved"]["lo"], p["cost_per_solved"]["hi"], p["cost_per_solved"]["point"]) if v is not None]
    xmax = max(xs) * 1.1 if xs else 1
    xt = _ticks(0, xmax)
    xmax = xt[-1]
    sx = lambda v: L + (W - L - R) * v / xmax  # noqa: E731
    sy = lambda v: T + (H - T - B) * (1 - v)  # noqa: E731
    body = [f'<text class="ink title" x="{L}" y="22">Pass rate against cost per solved task (95% intervals)</text>']
    for t in (0, .25, .5, .75, 1):
        body.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{sy(t)}" y2="{sy(t)}"/>'
                    f'<text class="muted tab" x="{L - 8}" y="{sy(t) + 4}" text-anchor="end">{t * 100:.0f}%</text>')
    for t in xt:
        body.append(f'<text class="muted tab" x="{sx(t)}" y="{H - B + 18}" text-anchor="middle">${t:g}</text>')
    body.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{sy(0)}" y2="{sy(0)}"/>')
    body.append(f'<text class="ink2" x="{(L + W - R) / 2}" y="{H - 8}" text-anchor="middle">list-price USD per solved task</text>')
    for p in pts:
        c, pr = p["cost_per_solved"], p["pass_rate"]
        x, y = sx(c["point"]), sy(pr["point"])
        if c["lo"] is not None:
            body.append(f'<line class="whisker" x1="{sx(c["lo"])}" x2="{sx(c["hi"])}" y1="{y}" y2="{y}"/>')
        if pr["lo"] is not None:
            body.append(f'<line class="whisker" x1="{x}" x2="{x}" y1="{sy(pr["lo"])}" y2="{sy(pr["hi"])}"/>')
        tip = f'{p["id"]} {p["name"]}: pass {pr["display"]} {pr["display_ci"]}; cost/solved {c["display"]} {c["display_ci"]}'
        body.append(f'<circle class="dot" cx="{x}" cy="{y}" r="6"><title>{escape(tip)}</title></circle>'
                    f'<text class="ink" x="{x + 10}" y="{y - 8}">{escape(p["id"])}</text>')
    return _svg(W, H, "".join(body), "Pass rate against cost per solved task")


def paired_forest(paired: list[dict], metric: str, title: str, unit_fmt) -> str:
    rows = [p for p in paired if p["metric"] == metric and p["diff"]["point"] is not None]
    W, L, R, T, rowh = 640, 120, 24, 44, 34
    H = T + rowh * max(1, len(rows)) + 36
    vals = [v for p in rows for v in (p["diff"]["lo"], p["diff"]["hi"], p["diff"]["point"]) if v is not None] + [0]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.1 or 1
    lo, hi = lo - pad, hi + pad
    sx = lambda v: L + (W - L - R) * (v - lo) / (hi - lo)  # noqa: E731
    body = [f'<text class="ink title" x="16" y="24">{escape(title)}</text>']
    for t in _ticks(lo, hi, 4):
        if lo <= t <= hi:
            body.append(f'<line class="grid" x1="{sx(t)}" x2="{sx(t)}" y1="{T - 6}" y2="{H - 30}"/>'
                        f'<text class="muted tab" x="{sx(t)}" y="{H - 12}" text-anchor="middle">{escape(unit_fmt(t))}</text>')
    body.append(f'<line class="zero" x1="{sx(0)}" x2="{sx(0)}" y1="{T - 10}" y2="{H - 30}"/>')
    for i, p in enumerate(rows):
        y = T + rowh * i + rowh / 2
        d = p["diff"]
        body.append(f'<text class="ink" x="16" y="{y + 4}">{escape(p["policy"])} vs {escape(p["baseline"])}</text>')
        if d["lo"] is not None:
            body.append(f'<line class="whisker" x1="{sx(d["lo"])}" x2="{sx(d["hi"])}" y1="{y}" y2="{y}"/>')
        tip = f'{p["policy"]} minus {p["baseline"]}: {d["display"]} {d["display_ci"]} - {p["verdict"]}'
        body.append(f'<circle class="dot" cx="{sx(d["point"])}" cy="{y}" r="6"><title>{escape(tip)}</title></circle>')
    return _svg(W, H, "".join(body), title)


def time_split(policies: list[dict]) -> str:
    """Stacked horizontal bars: total model time, test time and queue wait per policy."""
    W, L, R, T, rowh = 640, 170, 24, 64, 34
    H = T + rowh * len(policies) + 40
    keys = [("model_ms", "model time", "s1"), ("test_ms", "test time", "s2"), ("queue_ms", "queue wait", "s3")]
    totals = [sum(p["time_split"][k] for k, _, _ in keys) for p in policies] or [1]
    xt = _ticks(0, max(totals) / 1000.0 or 1, 4)
    xmax = xt[-1] * 1000.0
    sx = lambda v: L + (W - L - R) * v / xmax  # noqa: E731
    body = [f'<text class="ink title" x="16" y="24">Where the time went, all tasks summed</text>']
    lx = 16
    for k, label, cls in keys:  # legend
        body.append(f'<rect class="{cls}" x="{lx}" y="36" width="12" height="12" rx="3"/>'
                    f'<text class="ink2" x="{lx + 18}" y="46">{label}</text>')
        lx += 120
    for t in xt:
        body.append(f'<line class="grid" x1="{sx(t * 1000)}" x2="{sx(t * 1000)}" y1="{T - 6}" y2="{H - 34}"/>'
                    f'<text class="muted tab" x="{sx(t * 1000)}" y="{H - 16}" text-anchor="middle">{t:g} s</text>')
    for i, p in enumerate(policies):
        y = T + rowh * i + 6
        body.append(f'<text class="ink" x="16" y="{y + 15}">{escape(p["id"])} {escape(p["name"])}</text>')
        x = L
        for k, label, cls in keys:
            v = p["time_split"][k]
            w = sx(v) - L
            if w <= 0:
                continue
            tip = f'{p["id"]} {label}: {p["time_split"][k + "_display"]}'
            body.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{max(w - 2, 1)}" height="20" rx="4">'
                        f'<title>{escape(tip)}</title></rect>')
            x += w
    return _svg(W, H, "".join(body), "Time split per policy")


def write_all(results: dict, figures_dir: Path) -> dict[str, Path]:
    figures_dir.mkdir(parents=True, exist_ok=True)
    pol = results["policies"]
    out = {
        "pass_vs_cost": pass_vs_cost(pol),
        "paired_pass_rate": paired_forest(results["paired"], "pass_rate",
                                          "Change in pass rate against the baseline (paired, 95%)",
                                          lambda v: f"{v * 100:+.0f} pts"),
        "paired_cost_per_solved": paired_forest(results["paired"], "cost_per_solved",
                                                "Change in cost per solved task against the baseline (paired, 95%)",
                                                lambda v: f"{'+' if v >= 0 else '-'}${abs(v):.2g}"),
        "time_split": time_split(pol),
    }
    paths = {}
    for name, svg in out.items():
        p = figures_dir / f"{name}.svg"
        p.write_text(svg)
        paths[name] = p
    return paths
