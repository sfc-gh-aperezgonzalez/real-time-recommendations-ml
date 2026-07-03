"""Generate AI game tiles for the PlayNova catalog (Method C).

For every game in GAME_TITLE_DIM we build a category- and theme-aware prompt and
fetch a bespoke 320x200 image from Pollinations (Flux, no API key). Images are
saved as JPGs to assets/tiles_ai/game_{id}.jpg.

The run is resumable (existing valid JPEGs are skipped), uses limited concurrency
with retries + backoff, and validates each download is a real JPEG (not an error
page).

Usage:
    python ml/generate_ai_tiles.py                 # all 240, 6 workers
    python ml/generate_ai_tiles.py --workers 4
    python ml/generate_ai_tiles.py --limit 12      # smoke test
    python ml/generate_ai_tiles.py --force         # re-download everything
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from _session import get_session

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "tiles_ai"

STYLE = (
    ", vibrant digital game key art, cinematic dramatic lighting, highly detailed, "
    "rich saturated colors, centered composition, no text, no words, no letters, poster art"
)

# First word of the title -> vivid visual subject.
THEME = {
    "Gold": "opulent gold treasure hoard and coins",
    "Dragon": "a mighty fire-breathing dragon",
    "Fortune": "lucky fortune charms and golden ingots",
    "Treasure": "an overflowing pirate treasure chest of gold",
    "Aztec": "an ancient Aztec temple with jade and gold idols",
    "Pirate": "a pirate ship, skull and gold doubloons",
    "Mystic": "a mystical wizard with glowing magic runes",
    "Cosmic": "a cosmic galaxy of stars and colorful nebula",
    "Lucky": "lucky clovers, horseshoes and golden luck",
    "Phoenix": "a flaming phoenix rising from embers",
    "Diamond": "brilliant sparkling diamonds",
    "Wild": "wild jungle animals in untamed nature",
    "Royal": "a royal crown and jewels in a regal palace",
    "Pharaoh": "an Egyptian pharaoh, pyramids and hieroglyphs",
    "Viking": "a fierce viking warrior with longship and runes",
    "Samurai": "a samurai warrior among cherry blossoms with a katana",
    "Jungle": "a lush tropical jungle with exotic wildlife",
    "Ocean": "an underwater ocean world with treasure and fish",
    "Frost": "an icy frozen tundra of blue ice crystals",
    "Inferno": "a blazing inferno of fire and lava",
    "Neon": "a retro neon cyberpunk city at night",
    "Crystal": "a glowing magic crystal cavern",
    "Thunder": "a storm god amid lightning and thunder",
    "Gemstone": "multicolored precious gemstones",
    "Safari": "an african safari savanna with wild animals",
    "Carnival": "a vibrant festive carnival with lights",
    "Midas": "King Midas turning everything to gold",
    "Olympus": "Greek mythology gods on Mount Olympus",
    "Tiki": "a tropical tiki island with carved tiki masks",
    "Vault": "a bank vault full of cash and gold bars",
}

# Category -> the scene/format the theme should be staged in.
CATEGORY_SCENE = {
    "Video Slots": "as a fantasy video slot game",
    "Classic Slots": "as a classic fruit slot machine game",
    "Jackpot Slots": "as an explosive jackpot slot game with cascading coins",
    "Megaways Slots": "as a Megaways slot game with a glowing symbol grid",
    "Table Games": "on a luxurious green felt casino table",
    "Scratch & Instant": "as a shiny instant-win scratch card",
    "Live Roulette": "at a live dealer roulette wheel in an elegant studio",
    "Live Blackjack": "at a live dealer blackjack table with playing cards",
    "Live Baccarat": "at a live dealer baccarat table",
    "Live Game Shows": "as a flashy TV game show with a big prize wheel and studio lights",
    "Sportsbook": "in a floodlit sports stadium for sports betting",
    "Esports": "in a neon esports arena",
}
DEFAULT_SCENE = "as a casino game"


def build_prompt(title: str, category: str) -> str:
    theme_word = title.split()[0]
    subject = THEME.get(theme_word, "a luxurious casino jackpot scene")
    scene = CATEGORY_SCENE.get(category, DEFAULT_SCENE)
    return f"{subject} {scene}{STYLE}"


def is_valid_jpeg(path: pathlib.Path) -> bool:
    try:
        if path.stat().st_size < 4000:
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"\xff\xd8"
    except OSError:
        return False


def fetch_one(game_id: int, title: str, category: str, force: bool) -> tuple[int, str]:
    out = OUT_DIR / f"game_{game_id}.jpg"
    if not force and is_valid_jpeg(out):
        return game_id, "skip"
    prompt = build_prompt(title, category)
    enc = urllib.parse.quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{enc}"
        f"?width=320&height=200&seed={game_id}&nologo=true&model=flux"
    )
    last = ""
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            if data[:2] != b"\xff\xd8" or len(data) < 4000:
                last = f"bad payload ({len(data)}b)"
                time.sleep(5 + attempt * 6)
                continue
            out.write_bytes(data)
            return game_id, f"ok {len(data)//1024}KB"
        except urllib.error.HTTPError as e:  # noqa: PERF203
            last = f"HTTP {e.code}"
            # 429 / 5xx: wait longer before retrying
            time.sleep((10 if e.code == 429 else 5) + attempt * 7)
        except Exception as e:  # noqa: BLE001
            last = str(e)[:80]
            time.sleep(5 + attempt * 6)
    return game_id, f"FAIL {last}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = get_session()
    try:
        rows = session.sql(
            """SELECT g.GAME_TITLE_ID, g.GAME_TITLE, c.CATEGORY_NAME
               FROM PLAYNOVA_RECS_DEMO.CORE.GAME_TITLE_DIM g
               JOIN PLAYNOVA_RECS_DEMO.CORE.GAME_CATEGORY_DIM c
                 ON c.CATEGORY_ID = g.CATEGORY_ID
               ORDER BY g.GAME_TITLE_ID"""
        ).collect()
    finally:
        session.close()

    games = [(r["GAME_TITLE_ID"], r["GAME_TITLE"], r["CATEGORY_NAME"]) for r in rows]
    if args.limit:
        games = games[: args.limit]

    total = len(games)
    print(f"Generating {total} AI tiles -> {OUT_DIR} (workers={args.workers})", flush=True)
    done = ok = skip = fail = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(fetch_one, gid, title, cat, args.force): gid
            for gid, title, cat in games
        }
        for fut in cf.as_completed(futs):
            gid, status = fut.result()
            done += 1
            if status.startswith("ok"):
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            print(f"[{done}/{total}] game_{gid}: {status}", flush=True)

    print(f"\nDone. ok={ok} skip={skip} fail={fail} total={total}", flush=True)
    if fail:
        print("Re-run the script to retry failed tiles (it is resumable).", flush=True)


if __name__ == "__main__":
    main()
