#!/usr/bin/env python3
"""Шаг 3. Скачивание таблиц basketball-reference за все сезоны.

8 типов таблиц x сезоны 2000-01 .. 2026-27 = до 216 файлов HTML.

basketball-reference защищён Cloudflare и режет >20 запросов/мин,
поэтому используется headless Chromium (Playwright) с паузами.
Из домашнего интернета обычно проходит без проблем; с дата-центровых IP
Cloudflare может упорно не пускать.

Перед запуском:
    pip install -r requirements.txt
    playwright install chromium

Возобновляемость: скачанные файлы пропускаются, просто перезапустите.
Примеры:
    python 03_bref_scrape.py                    # все сезоны
    python 03_bref_scrape.py --from 2001 --to 2005   # только 2000-01..2004-05
"""
import argparse
import asyncio

from playwright.async_api import async_playwright

from config import BREF_DIR, BREF_BASE, BREF_TABLES, BREF_SEASON_YEARS, UA, season_label

PAUSE_SEC = 3.2          # ~18 запросов/мин, у b-ref лимит 20
MIN_VALID_SIZE = 50_000  # меньше - почти наверняка страница-заглушка


async def fetch_table(page, year: int, fname: str, urlslug: str) -> str:
    label = f"{year - 1}-{str(year)[2:]}"
    dest = BREF_DIR / f"{label}_{fname}.html"
    try:
        if dest.exists() and dest.stat().st_size > MIN_VALID_SIZE:
            return "cached"
    except OSError:
        pass

    url = f"{BREF_BASE}/leagues/NBA_{year}_{urlslug}.html"
    for attempt in range(4):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            html = await page.content()
            # антибот-заглушка Cloudflare
            if "Just a moment" in html or "cf-chl" in html:
                await asyncio.sleep(8)
                html = await page.content()
                if "Just a moment" in html:
                    continue
            if "data-stat" in html and len(html) > MIN_VALID_SIZE:
                dest.write_text(html)
                return f"OK {len(html)}"
            return f"THIN {len(html)} (нет таблиц - проверьте URL вручную)"
        except Exception:
            await asyncio.sleep(5)
    return "FAIL"


async def main(year_from: int, year_to: int):
    BREF_DIR.mkdir(parents=True, exist_ok=True)
    years = [y for y in BREF_SEASON_YEARS if year_from <= y <= year_to]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = await browser.new_context(user_agent=UA,
                                        viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        for year in years:
            for fname, urlslug in BREF_TABLES:
                st = await fetch_table(page, year, fname, urlslug)
                print(f"{year - 1}-{str(year)[2:]} {fname} {st}", flush=True)
                await asyncio.sleep(PAUSE_SEC)
        await browser.close()
    print("ГОТОВО")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="year_from", type=int, default=2001,
                    help="первый год URL (2001 = сезон 2000-01)")
    ap.add_argument("--to", dest="year_to", type=int, default=2027)
    args = ap.parse_args()
    asyncio.run(main(args.year_from, args.year_to))
