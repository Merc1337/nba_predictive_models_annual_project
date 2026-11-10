#!/usr/bin/env python3
"""Шаг 1. Скачивает sitemap'ы CraftedNBA и список Hall of Fame.

На выходе в nba_data/:
  slugs_active.txt  - slug'и активных игроков (~612)
  slugs_hof.txt     - slug'и игроков Hall of Fame (~217)
  hof_index_payload.json - карьерные CPM всех HOF-игроков
"""
import json
import re
import subprocess
import time

from config import DATA_DIR, CRAFTED_DEV, CRAFTED_PROD, UA


def curl(url: str, dest, timeout: int = 60, tries: int = 4) -> bool:
    """curl с ретраями (pages.dev периодически рвёт соединение)."""
    for i in range(tries):
        r = subprocess.run(
            ["curl", "-sL", "--compressed", "-A", UA, "-o", str(dest),
             "-w", "%{http_code}", "--max-time", str(timeout), url],
            capture_output=True, text=True)
        if r.stdout.strip() == "200" and dest.exists() and dest.stat().st_size > 200:
            return True
        print(f"  retry {i + 1} for {url} (code={r.stdout.strip()})")
        subprocess.run(["sleep", "5"])
    return False


def main():
    DATA_DIR.mkdir(exist_ok=True)

    # --- sitemap index -> нужные под-sitemap'ы ---
    idx = DATA_DIR / "sitemap_index.xml"
    if not curl(f"{CRAFTED_DEV}/sitemap_index.xml", idx):
        raise SystemExit("Не удалось скачать sitemap_index.xml")

    for name, out_xml, out_txt, pattern in [
        ("players", "players_sitemap.xml", "slugs_active.txt", r"(?<=/players/)[^<]+"),
        ("hall-of-fame-players", "hof_sitemap.xml", "slugs_hof.txt",
         r"(?<=/hall-of-fame/players/)[^<]+"),
    ]:
        xml_path = DATA_DIR / out_xml
        if not curl(f"{CRAFTED_DEV}/{name}-sitemap.xml", xml_path):
            print(f"!! не скачался {name}-sitemap.xml")
            continue
        slugs = sorted(set(re.findall(pattern, xml_path.read_text(errors="ignore"))))
        (DATA_DIR / out_txt).write_text("\n".join(slugs))
        print(f"{name}: {len(slugs)} slug'ов -> {out_txt}")

    # --- HOF индекс: карьерный CPM всех 217 легенд ---
    # Важно: на dev-сборке страницы HOF сломаны (HTTP 500), индекс берём с prod.
    hof_idx = DATA_DIR / "hof_index_payload.json"
    if curl(f"{CRAFTED_PROD}/hall-of-fame/players/_payload.json", hof_idx,
            timeout=150, tries=5):
        try:
            json.load(open(hof_idx))
            print("hof_index_payload.json OK")
        except Exception:
            print("!! hof_index скачался, но это не JSON - перезапустите скрипт")
    else:
        print("!! не удалось скачать HOF индекс - перезапустите скрипт позже")


if __name__ == "__main__":
    main()
