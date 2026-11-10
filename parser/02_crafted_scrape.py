#!/usr/bin/env python3
"""Шаг 2. Массовое скачивание payload'ов игроков CraftedNBA.

Для каждого slug'а скачивает /players/{slug}/_payload.json - это сырые данные
страницы игрока (Nuxt): вся посезонная история CPM/COPM/CDPM, DARKO, DRIP, BPM
и сопутствующая статистика.

Порядок источников:
  1) DEV-сборка  - быстро, ПОЛНАЯ история сезонов  <- основной канал
  2) PROD-сборка - медленно (~30-60 c), только текущий сезон, но срабатывает,
                   когда dev отдаёт HTTP 500/урезанный ответ

Особенности:
  - возобновляемость: уже валидные файлы пропускаются, просто перезапустите;
  - slug'и, которые не удалось скачать, пишутся в failed_slugs.txt -
    его можно скормить обратно:  python 02_crafted_scrape.py --slugs nba_data/failed_slugs.txt
"""
import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from config import DATA_DIR, PAYLOADS_DIR, CRAFTED_DEV, CRAFTED_PROD, UA

# --- Подписка (опционально) ---
# CRAFTEDNBA_COOKIE - полный заголовок Cookie из браузера (если он там есть).
# CRAFTEDNBA_AUTH   - заголовок Authorization (если авторизация токеном,
#                     напр. "Bearer eyJhbG..."), берётся там же в DevTools.
# CRAFTEDNBA_UA     - User-Agent ТОГО ЖЕ браузера (выполнять с того же IP!).
# Креденшелы действуют только на основном домене www.craftednba.com;
# если заданы - скрипт сначала пробует его, затем открытые *.pages.dev.
COOKIE = os.environ.get("CRAFTEDNBA_COOKIE", "")
AUTH = os.environ.get("CRAFTEDNBA_AUTH", "")
UA = os.environ.get("CRAFTEDNBA_UA", UA)
WWW = "https://www.craftednba.com"


def is_valid_payload(path: Path) -> bool:
    """Файл - валидный JSON-список Nuxt payload (не HTML-страница ошибки)."""
    try:
        with open(path) as f:
            d = json.load(f)
        return isinstance(d, list) and len(d) > 10
    except Exception:
        return False


def has_history(path: Path) -> bool:
    """В payload есть посезонная таблица (словарь с Season и CraftedPM)."""
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return False
    return any(isinstance(x, dict) and "Season" in x and "CraftedPM" in x for x in d)


def curl_payload(url: str, dest: Path, timeout: int) -> str:
    cmd = ["curl", "-s", "--compressed", "-A", UA, "-o", str(dest),
           "-w", "%{http_code}", "--max-time", str(timeout), url]
    if url.startswith(WWW):
        # креденшелы отправляем ТОЛЬКО на основной домен, которому они принадлежат
        if COOKIE:
            cmd += ["-H", f"Cookie: {COOKIE}"]
        if AUTH:
            cmd += ["-H", AUTH if AUTH.lower().startswith("authorization")
                    else f"Authorization: Bearer {AUTH}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def fetch_one(slug: str) -> tuple[str, str]:
    dest = PAYLOADS_DIR / f"{slug}.json"
    if dest.exists() and dest.stat().st_size > 5000 and is_valid_payload(dest):
        return slug, "cached"

    tmp = Path(f"./tmp_pay_{slug}_{os.getpid()}.json")
    try:
        # 0) www + креденшелы подписки: шанс получить историю и для retired
        if COOKIE or AUTH:
            for _ in range(2):
                code = curl_payload(f"{WWW}/players/{slug}/_payload.json", tmp, 45)
                if code == "200" and has_history(tmp):
                    dest.write_bytes(tmp.read_bytes())
                    return slug, "ok(www+cookie)"
                time.sleep(2)
        # 1) dev: до 3 попыток, требуем полную историю
        for _ in range(3):
            code = curl_payload(f"{CRAFTED_DEV}/players/{slug}/_payload.json", tmp, 45)
            if code == "200" and has_history(tmp):
                dest.write_bytes(tmp.read_bytes())
                return slug, "ok(dev)"
            time.sleep(1)
        # 2) prod: 1-2 попытки (медленный), истории там нет - берём что даёт
        for _ in range(2):
            code = curl_payload(f"{CRAFTED_PROD}/players/{slug}/_payload.json", tmp, 120)
            if code == "200" and is_valid_payload(tmp):
                dest.write_bytes(tmp.read_bytes())
                return slug, "ok(prod,только текущий сезон)"
            time.sleep(2)
        return slug, "FAIL"
    finally:
        tmp.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", default=str(DATA_DIR / "slugs_active.txt"),
                    help="файл со slug'ами, по одному на строку")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)
    slugs = [s.strip() for s in open(args.slugs) if s.strip()]
    print(f"slug'ов в очереди: {len(slugs)}")

    ok = fail = 0
    failed = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, s): s for s in slugs}
        for i, fut in enumerate(as_completed(futs), 1):
            slug, st = fut.result()
            if st == "FAIL":
                fail += 1
                failed.append(slug)
            else:
                ok += 1
            if i % 25 == 0 or st == "FAIL":
                print(f"[{i}/{len(slugs)} ok={ok} fail={fail} "
                      f"t={time.time() - t0:.0f}s] {slug} {st}", flush=True)

    if failed:
        (DATA_DIR / "failed_slugs.txt").write_text("\n".join(failed))
    print(f"ГОТОВО ok={ok} fail={fail}. Неудачи -> {DATA_DIR/'failed_slugs.txt'}")


if __name__ == "__main__":
    main()
