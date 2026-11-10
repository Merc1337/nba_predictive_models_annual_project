#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07 — Season Finder API: посезонный CPM для ВСЕХ игроков NBA (1952–2027).

Эндпоинт: POST https://craftednba.pages.dev/api/season-finder
Тело: {"positions":[...], "ageMin":"any", "ageMax":"any", "seasons":[...],
       "firstyear":null, "statfilters":[], "currentpage":0,
       "sort":"SQ", "sortDirection":"desc", "playerName":"", "isPro":false}
Ответ: {"data": [...50 строк...], "count": N, "gated": bool, "maskedTop": int}

Механика маскировки (freemium):
  - глобальная выборка по всем сезонам  — без маскировки (maskedTop=0);
  - выборка по одному сезону            — топ-10 сезона скрыты ("Player":"Locked");
    обход: тот же запрос с sortDirection="asc" прячет уже ХУДШИЕ 10,
    так что desc ∪ asc даёт сезон целиком;
  - поиск по playerName                 — без маскировки.

Стратегия скрипта:
  1) глобальный проход по страницам (по 50 строк) — забирает почти всё;
  2) сверка count по каждому сезону (1 дешёвый запрос на сезон);
  3) добор недостающих сезонов связкой desc+asc;
  4) дедупликация по (Player, Season) и сохранение CSV.

Возобновляемо: сырые страницы пишутся в raw/seasonfinder/*.jsonl,
готовые сезоны помечаются, повторный запуск продолжит с места обрыва.
"""
import json, math, os, sys, time
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR, UA

API = "https://craftednba.pages.dev/api/season-finder"
RAW_DIR = os.path.join(DATA_DIR, "raw", "seasonfinder")
OUT_CSV = os.path.join(DATA_DIR, "parsed", "crafted_seasonfinder.csv")
PAGE = 50
SEASON_MIN, SEASON_MAX = 1952, 2027
PAUSE = 1.3          # вежливая пауза между запросами
TIMEOUT = 120


def base_body(**kw):
    b = {"positions": ["PG", "SG", "SF", "PF", "C"], "ageMin": "any", "ageMax": "any",
         "seasons": list(range(SEASON_MIN, SEASON_MAX + 1)), "firstyear": None,
         "statfilters": [], "currentpage": 0, "sort": "SQ", "sortDirection": "desc",
         "playerName": "", "isPro": False}
    b.update(kw)
    return b


def fetch(client, body, tries=6):
    """POST с ретраями и экспоненциальным бэкоффом. Возвращает dict или None."""
    for a in range(tries):
        try:
            r = client.post(API, json=body, timeout=TIMEOUT)
            if r.status_code == 200:
                d = r.json()
                if isinstance(d, dict) and "data" in d:
                    return d
                print("  ! неожиданный формат:", str(d)[:120])
            else:
                print(f"  ! HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:
            print(f"  ! {type(e).__name__}: {str(e)[:100]}")
        time.sleep(3 * (a + 1))
    return None


def jpath(name):
    return os.path.join(RAW_DIR, name)


def load_jsonl(name):
    p = jpath(name)
    rows = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def append_jsonl(name, rows):
    if not rows:
        return
    with open(jpath(name), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def global_sweep(client):
    """Шаг 1: глобальная выборка всех сезонов. Возобновляется по номеру страницы.

    Сначала добираем последовательно (состояние global_state.json), затем
    ускоряемся: оставшиеся страницы тянем пулом потоков (порядок не важен —
    в конце дедупликация и сортировка). Выполненные страницы помечаются
    в global_pages.json, так что прерывание ничего не портит.
    """
    state_p = jpath("global_state.json")
    pages_p = jpath("global_pages.json")
    done_pages = 0
    total_pages = None
    if os.path.exists(state_p):
        st = json.load(open(state_p))
        done_pages, total_pages = st.get("done_pages", 0), st.get("total_pages")
    done_set = set(json.load(open(pages_p))) if os.path.exists(pages_p) else set()
    first = fetch(client, base_body(currentpage=0))
    if not first:
        print("Глобальная страница 0 недоступна — стоп.")
        return False
    total = first["count"]
    total_pages = math.ceil(total / PAGE)
    if done_pages == 0 and 0 not in done_set:
        append_jsonl("global.jsonl", [x for x in first["data"] if x.get("Player") != "Locked"])
        done_pages = 1
        json.dump({"done_pages": done_pages, "total_pages": total_pages},
                  open(state_p, "w"))
        json.dump(sorted(done_set), open(pages_p, "w"))
    todo = [p for p in range(done_pages, total_pages) if p not in done_set]
    print(f"Глобальная выборка: {total} строк, {total_pages} страниц, "
          f"уже есть {len(done_set) + done_pages}, осталось {len(todo)}")
    if not todo:
        return True

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    lock = threading.Lock()
    pool = [httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)
            for _ in range(4)]

    def work(i_page):
        idx, page = i_page
        d = fetch(pool[idx % len(pool)], base_body(currentpage=page), tries=8)
        return page, d

    fails = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(work, (i, p)): p for i, p in enumerate(todo)}
        n = 0
        for fut in as_completed(futs):
            p = futs[fut]
            n += 1
            try:
                _, d = fut.result()
            except Exception:
                d = None
            if not d:
                fails += 1
                print(f"  стр.{p}: сбой — перезапуск доберёт")
                continue
            got = [x for x in d["data"] if x.get("Player") != "Locked"]
            with lock:
                append_jsonl("global.jsonl", got)
                done_set.add(p)
                json.dump(sorted(done_set), open(pages_p, "w"))
            if n % 25 == 0 or n == len(todo):
                print(f"  готово {n}/{len(todo)} страниц (последняя {p}, +{len(got)})")
    # фиксируем: последовательная часть доведена до конца, если всё собрано
    if len(done_set) + done_pages >= total_pages and fails == 0:
        json.dump({"done_pages": total_pages, "total_pages": total_pages},
                  open(state_p, "w"))
    print(f"Глобальный проход: страниц собрано {len(done_set) + done_pages}/{total_pages}, сбоев {fails}")
    return fails == 0


def season_counts(client):
    """Шаг 2: count по каждому сезону (строки не важны)."""
    p = jpath("season_counts.json")
    counts = json.load(open(p)) if os.path.exists(p) else {}
    for y in range(SEASON_MIN, SEASON_MAX + 1):
        if str(y) in counts:
            continue
        d = fetch(client, base_body(seasons=[y], currentpage=0))
        counts[str(y)] = d["count"] if d else -1
        json.dump(counts, open(p, "w"))
        time.sleep(PAUSE)
    return {int(k): v for k, v in counts.items()}


def rescue_season(client, year, need):
    """Шаг 3: добор сезона desc+asc. Возвращает список строк."""
    rows, seen = [], set()
    for direction in ("desc", "asc"):
        pages = math.ceil(need / PAGE)
        for p in range(pages):
            d = fetch(client, base_body(seasons=[year], currentpage=p,
                                        sort="SQ", sortDirection=direction))
            if not d:
                break
            for x in d["data"]:
                if x.get("Player") == "Locked":
                    continue
                k = (x.get("Player"), x.get("Season"))
                if k not in seen:
                    seen.add(k)
                    rows.append(x)
            time.sleep(PAUSE)
        if len(rows) >= need:
            break
    return rows


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    client = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)

    if not global_sweep(client):
        print("Глобальный проход не завершён — перезапустите позже, прогресс на диске.")
        return

    rows = load_jsonl("global.jsonl")
    print(f"Собрано глобально: {len(rows)} строк")

    counts = season_counts(client)
    by_season = {}
    for r in rows:
        by_season.setdefault(r.get("Season"), set()).add(r.get("Player"))

    short = {y: c for y, c in sorted(counts.items())
             if c > 0 and len(by_season.get(y, set())) < c}
    if short:
        print(f"Неполные сезоны ({len(short)}): добор desc+asc в 4 потока…")
        from concurrent.futures import ThreadPoolExecutor, as_completed
        pool = [httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)
                for _ in range(4)]
        items = sorted(short.items())
        def work(ic):
            i, (y, c) = ic
            return y, c, rescue_season(pool[i % len(pool)], y, c)
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(work, (i, yc)) for i, yc in enumerate(items)]
            for fut in as_completed(futs):
                y, c, extra = fut.result()
                add = 0
                for x in extra:
                    if x.get("Player") not in by_season.setdefault(y, set()):
                        by_season[y].add(x.get("Player"))
                        rows.append(x)
                        add += 1
                append_jsonl("global.jsonl", [x for x in extra])
                print(f"  {y}: +{add} (итого {len(by_season[y])}/{c})")
    else:
        print("Все сезоны полные.")

    # дедупликация и сохранение
    import pandas as pd
    df = pd.DataFrame(rows)
    df = df[df["Player"].notna() & (df["Player"] != "Locked")]
    df = df.drop_duplicates(subset=["Player", "Season"], keep="first")
    df = df.sort_values(["Season", "Player"]).reset_index(drop=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"Сохранено: {OUT_CSV} — {len(df)} строк, {df['Player'].nunique()} игроков, "
          f"сезоны {df['Season'].min()}–{df['Season'].max()}")


if __name__ == "__main__":
    main()
