#!/usr/bin/env python3
"""Шаг 5. Парсинг HTML basketball-reference -> CSV по каждому типу таблицы.

Нюансы b-ref:
  - часть таблиц спрятана в HTML-комментариях -> снимаем <!-- -->;
  - в таблицах повторяются строки-заголовки и строка "League Average";
  - id игрока (bref_id, напр. 'iversal01') лежит в ссылке /players/x/<id>.html;
  - игрок, переехавший по ходу сезона, идёт несколькими строками
    (по командам + суммарная 'TOT'). По умолчанию оставляем только 'TOT'
    (флаг --keep-split сохранит все строки).

На выходе в nba_data/parsed/:
  bref_<тип>.csv для per_game, per_36, per_100, advanced, shooting,
  totals, play_by_play  +  bref_team_ratings.csv (командная таблица)
"""
import argparse
import re
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup

from config import BREF_DIR, PARSED_DIR, BREF_TABLES


def flatten_cols(cols) -> list[str]:
    """Двухуровневые заголовки b-ref -> плоские уникальные имена.
    По умолчанию берём нижний уровень ('Player', '2P'...); при коллизии
    добавляем группу или суффикс: 'Shoot' -> 'Fouls Committed_Shoot' / 'Shoot_2'."""
    if not isinstance(cols, pd.MultiIndex):
        cols = [("", str(c)) for c in cols]
    out, used = [], set()
    for top, sub in cols:
        top, sub = str(top), str(sub)
        name = sub if not sub.startswith("Unnamed") else (
            top if not top.startswith("Unnamed") else "col")
        if name in used:
            alt = f"{top}_{sub}"
            base = alt if not alt.startswith("Unnamed") else name
            name, i = base, 2
            while name in used:
                name = f"{base}_{i}"
                i += 1
        used.add(name)
        out.append(name)
    return out


def parse_one(path, fname: str, keep_split: bool) -> pd.DataFrame | None:
    # stem вида "2019-20_per_game" -> label "2019-20" (отрезаем _<fname>)
    label = path.stem[: -(len(fname) + 1)]
    html = path.read_text(errors="ignore")

    # таблицы в комментариях: раскомментируем
    html = html.replace("<!--", "").replace("-->", "")

    # карта "имя игрока -> bref_id" из ссылок /players/x/<id>.html
    soup = BeautifulSoup(html, "lxml")
    name2id = {}
    for td in soup.select("td[data-stat='name_display'], td[data-stat='player']"):
        a = td.find("a", href=True)
        if not a:
            continue
        m = re.search(r"/players/[a-z]/([^.]+)\.html", a["href"])
        if m:
            name2id.setdefault(td.get_text(strip=True), m.group(1))

    tables = [t.pipe(lambda t: t.set_axis(flatten_cols(t.columns), axis=1))
              for t in pd.read_html(StringIO(html))]
    cand = [t for t in tables if "Player" in t.columns or "Team" in t.columns]
    if not cand:
        return None
    df = max(cand, key=len)

    is_team = "Player" not in df.columns
    if not is_team:
        # убрать строки-заголовки и "League Average"
        df = df[(df["Player"] != "Player") & (df["Player"] != "League Average")]
        df = df.dropna(subset=["Player"])
        df["bref_id"] = df["Player"].astype(str).str.strip().map(name2id)
        team_col = "Team" if "Team" in df.columns else ("Tm" if "Tm" in df.columns else None)
        if not keep_split and team_col:
            # переехавшие по ходу сезона: b-ref даёт строки по командам
            # + суммарную '2TM'/'3TM' - оставляем только её
            dup = df.duplicated(subset=["Player"], keep=False)
            is_multi = df[team_col].astype(str).str.fullmatch(r"\d+TM").fillna(False)
            df = df[~dup | is_multi]
    df["season"] = label
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-split", action="store_true",
                    help="не схлопывать строки переехавших игроков в TOT")
    args = ap.parse_args()
    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    for fname, _ in BREF_TABLES:
        parts = []
        for f in sorted(BREF_DIR.glob(f"*_{fname}.html")):
            try:
                df = parse_one(f, fname, args.keep_split)
            except Exception as e:
                print(f"!! {f.name}: {e}")
                continue
            if df is not None and len(df):
                df["table"] = fname
                parts.append(df)
        if not parts:
            print(f"{fname}: нет данных")
            continue
        out = pd.concat(parts, ignore_index=True)
        name = "bref_team_ratings" if fname == "ratings" else f"bref_{fname}"
        out.to_csv(PARSED_DIR / f"{name}.csv", index=False)
        print(f"{name}.csv: {len(out)} строк, {out['season'].nunique()} сезонов")


if __name__ == "__main__":
    main()
