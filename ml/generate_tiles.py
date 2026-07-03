"""Generate PlayNova-branded SVG game tiles from the catalog.

One tile per game (320x200), styled after a slots-site rail card: a vertical-
colored gradient, a "# Studio" pill top-left, the game title centered, and a
category label. Tiles are written to assets/tiles/ and uploaded to the
@PLAYNOVA_RECS_DEMO.APP.TILE_ASSETS stage so the deployment skill can serve them.

Usage:
    python ml/generate_tiles.py            # generate + upload to stage
    python ml/generate_tiles.py --no-upload
"""
from __future__ import annotations

import argparse
import html
import os
import pathlib

from _session import get_session

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "tiles"


def _darken(hex_color: str, factor: float = 0.55) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = (int(c * factor) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _wrap(title: str, width: int = 14) -> list[str]:
    words, lines, cur = title.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:3]


def make_svg(game_id: int, title: str, studio: str, category: str, color: str) -> str:
    dark = _darken(color)
    t = html.escape(title)
    st = html.escape(studio)
    cat = html.escape(category.upper())
    lines = _wrap(t)
    n = len(lines)
    start_y = 110 - (n - 1) * 17
    tspans = "".join(
        f'<text x="160" y="{start_y + i*34}" text-anchor="middle" '
        f'font-family="Inter,Arial,sans-serif" font-size="26" font-weight="800" '
        f'fill="#ffffff" style="paint-order:stroke;stroke:#00000055;stroke-width:1px">'
        f"{html.escape(line)}</text>"
        for i, line in enumerate(lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200">
  <defs>
    <linearGradient id="g{game_id}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{color}"/>
      <stop offset="100%" stop-color="{dark}"/>
    </linearGradient>
    <radialGradient id="h{game_id}" cx="0.3" cy="0.2" r="0.9">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.35"/>
      <stop offset="60%" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="320" height="200" rx="16" fill="url(#g{game_id})"/>
  <rect width="320" height="200" rx="16" fill="url(#h{game_id})"/>
  <g>
    <rect x="12" y="12" rx="10" height="22" width="{min(300, 30 + len(st) * 7)}" fill="#00000055"/>
    <text x="22" y="27" font-family="Inter,Arial,sans-serif" font-size="12" font-weight="700" fill="#ffffff"># {st}</text>
  </g>
  {tspans}
  <text x="160" y="178" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-size="12" font-weight="600" fill="#ffffffcc" letter-spacing="1.5">{cat}</text>
</svg>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = get_session()
    try:
        rows = session.sql(
            """SELECT g.GAME_TITLE_ID, g.GAME_TITLE, g.STUDIO_NAME, c.CATEGORY_NAME, g.TILE_COLOR_HEX
               FROM PLAYNOVA_RECS_DEMO.CORE.GAME_TITLE_DIM g
               JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID"""
        ).collect()
        for r in rows:
            svg = make_svg(
                r["GAME_TITLE_ID"], r["GAME_TITLE"], r["STUDIO_NAME"],
                r["CATEGORY_NAME"], r["TILE_COLOR_HEX"] or "#7A3FF2",
            )
            (OUT_DIR / f"game_{r['GAME_TITLE_ID']}.svg").write_text(svg)
        print(f"Generated {len(rows)} tiles -> {OUT_DIR}")

        if not args.no_upload:
            put = session.file.put(
                f"file://{OUT_DIR}/*.svg",
                "@PLAYNOVA_RECS_DEMO.APP.TILE_ASSETS/tiles",
                auto_compress=False,
                overwrite=True,
            )
            print(f"Uploaded {len(put)} tiles to @APP.TILE_ASSETS/tiles")
    finally:
        session.close()


if __name__ == "__main__":
    main()
