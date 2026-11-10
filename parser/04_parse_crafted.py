#!/usr/bin/env python3
"""Шаг 4. Парсинг payload'ов CraftedNBA -> посезонная таблица игроков.

Payload - это dedup-формат Nuxt: JSON-массив, где объекты ссылаются друг на
друга по индексам. Поэтому сначала "разворачиваем" ссылки (resolve), затем
находим список посезонных словарей (в них есть ключи Season и CraftedPM).

На выходе в nba_data/parsed/:
  crafted_seasons.csv - 1 строка = 1 игрок x 1 сезон:
      CraftedPM/CraftedOPM/CraftedDPM (таргет), ODARKO/DDARKO/DARKO,
      ODRIP/DDRIP/DRIP, OBPM/DBPM/BPM, PER, WS, VORP, боксскор, spacing,
      boxCreationEst, offensiveLoad, passerRating, SQ и др.
  hof_career.csv - карьерный CPM игроков Hall of Fame
"""
import json
import pandas as pd

from config import DATA_DIR, PAYLOADS_DIR, PARSED_DIR


def resolve(d, idx, depth=0, maxdepth=6):
    """Рекурсивно разворачивает индексные ссылки Nuxt payload."""
    v = d[idx] if isinstance(idx, int) else idx
    if depth > maxdepth:
        return None
    if isinstance(v, dict):
        return {k: resolve(d, x, depth + 1, maxdepth) for k, x in v.items()}
    if isinstance(v, list):
        return [resolve(d, x, depth + 1, maxdepth) for x in v]
    return v


def extract_seasons(payload_path) -> list[dict]:
    """Достаёт посезонные записи игрока из payload."""
    with open(payload_path) as f:
        d = json.load(f)

    def is_season(i):
        x = d[i] if isinstance(i, int) and i < len(d) else i
        return isinstance(x, dict) and "Season" in x and "CraftedPM" in x

    # основной путь: список индексов на сезонные словари
    candidates = []
    for x in d:
        if isinstance(x, list) and x:
            els = [e for e in x
                   if isinstance(e, int) and e < len(d) and is_season(e)]
            if els and len(els) >= len(x) * 0.5:
                candidates.append(els)
    if candidates:
        best = max(candidates, key=len)
        rows = [resolve(d, el, maxdepth=3) for el in best]
    else:
        # запасной путь: сезонные словари лежат без списка-обёртки
        rows = [resolve(d, i, maxdepth=3) for i in range(len(d)) if is_season(i)]

    # дедуп по (сезон, команда): в payload могут дублироваться блоки
    seen, out = set(), []
    for r in rows:
        k = (r.get("Season"), r.get("Tm"))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def extract_hof(payload_path) -> list[dict]:
    """Карьерные записи HOF-игроков из индексного payload."""
    with open(payload_path) as f:
        d = json.load(f)
    rows = []
    for i, x in enumerate(d):
        if isinstance(x, dict) and "CraftedPM" in x and "Player" in x:
            rows.append(resolve(d, i, maxdepth=2))
    return rows


def season_label(end_year) -> str:
    """CraftedNBA хранит год КОНЦА сезона: 2004 = сезон 2003-04."""
    try:
        y = int(end_year)
        return f"{y - 1}-{str(y)[2:]}"
    except (TypeError, ValueError):
        return None


def main():
    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    all_rows, empty = [], []
    for f in sorted(PAYLOADS_DIR.glob("*.json")):
        try:
            rows = extract_seasons(f)
        except Exception:
            rows = []
        if not rows:
            empty.append(f.stem)
            continue
        for r in rows:
            r["crafted_slug"] = f.stem
            r["season"] = season_label(r.get("Season"))
            all_rows.append(r)

    df = pd.DataFrame(all_rows)
    df.to_csv(PARSED_DIR / "crafted_seasons.csv", index=False)
    print(f"crafted_seasons.csv: {len(df)} строк, "
          f"{df['crafted_slug'].nunique()} игроков, "
          f"сезоны {df['season'].min()}..{df['season'].max()}")
    if empty:
        print(f"без посезонных данных ({len(empty)}): "
              f"{', '.join(empty[:10])}{'...' if len(empty) > 10 else ''}")

    hof_file = DATA_DIR / "hof_index_payload.json"
    if hof_file.exists():
        hof = pd.DataFrame(extract_hof(hof_file))
        hof.to_csv(PARSED_DIR / "hof_career.csv", index=False)
        print(f"hof_career.csv: {len(hof)} игроков")


if __name__ == "__main__":
    main()
