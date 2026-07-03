"""Build a static HTML gallery of the AI-generated tiles for visual QA.

Reads the catalog from Snowflake and renders every game as an app-style card
pointing at app/public/tiles/game_{id}.jpg. Output: tile-ai-gallery.html
"""
from __future__ import annotations

import html
import pathlib

from _session import get_session

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tile-ai-gallery.html"

session = get_session()
try:
    rows = session.sql(
        """SELECT g.GAME_TITLE_ID, g.GAME_TITLE, g.STUDIO_NAME, c.CATEGORY_NAME, c.VERTICAL
           FROM PLAYNOVA_RECS_DEMO.CORE.GAME_TITLE_DIM g
           JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM c ON c.CATEGORY_ID = g.CATEGORY_ID
           ORDER BY c.VERTICAL, c.CATEGORY_NAME, g.GAME_TITLE_ID"""
    ).collect()
finally:
    session.close()

cards = []
for r in rows:
    gid = r["GAME_TITLE_ID"]
    title = html.escape(r["GAME_TITLE"])
    studio = html.escape(r["STUDIO_NAME"])
    cat = html.escape(r["CATEGORY_NAME"])
    cards.append(f"""    <div class="card">
      <div class="thumb"><img loading="lazy" src="app/public/tiles/game_{gid}.jpg" alt=""><span class="pill"># {studio}</span></div>
      <div class="body"><div class="t">{title}</div><div class="s">{studio}</div>
      <div class="f"><span class="cat">{cat}</span><span class="sc">{gid}</span></div></div>
    </div>""")

doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>PlayNova · All 240 AI Tiles</title>
<style>
  body{{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#2a0a5e,#12002e 55%),#12002e;color:#f4ecff;font-family:Inter,Arial,sans-serif;padding:28px 36px 80px}}
  h1{{margin:0 0 4px;font-size:24px}} p.sub{{color:#b79fdd;margin:0 0 22px;font-size:14px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:16px}}
  .card{{background:#241043;border:1px solid #3a1f66;border-radius:12px;overflow:hidden}}
  .thumb{{position:relative;height:120px;background:#1a0640}}
  .thumb img{{width:100%;height:100%;object-fit:cover;display:block}}
  .pill{{position:absolute;top:8px;left:8px;font-size:10px;font-weight:700;background:#00000075;padding:2px 8px;border-radius:999px}}
  .body{{padding:9px 11px 11px}} .t{{font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .s{{font-size:11.5px;color:#b79fdd;margin-top:2px}} .f{{display:flex;justify-content:space-between;margin-top:7px;align-items:center}}
  .cat{{font-size:10px;background:#00000050;border:1px solid #3a1f66;padding:2px 8px;border-radius:999px;color:#cbb6f5}}
  .sc{{font-size:11px;color:#8f79c0}}
</style></head><body>
<h1>🎰 PlayNova · 240 AI-generated game tiles</h1>
<p class="sub">Method C rollout — grouped by vertical &amp; category. Each tile is a unique Flux render seeded by game ID.</p>
<div class="grid">
{chr(10).join(cards)}
</div></body></html>"""

OUT.write_text(doc)
print(f"Wrote {OUT} ({len(rows)} cards)")
