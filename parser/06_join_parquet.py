#!/usr/bin/env python3
"""Шаг 6. Финальный джойн CraftedNBA + basketball-reference -> parquet.

Гранулярность: 1 строка = 1 игрок x 1 сезон.

Основа - basketball-reference (все игроки всех сезонов с 2000-01).
Источники CraftedNBA (таргет CPM), в порядке приоритета:
  1) crafted_seasons.csv       - пейлоады страниц игроков (богатый набор:
                                 DARKO/DRIP, play-type, трекинг; только активные);
  2) crafted_seasonfinder.csv  - Season Finder API (все игроки 1952-2027,
                                 включая завершивших карьеру; закрывает
                                 историю активных и даёт CPM для retired).

Ключи джойна:
  - между таблицами b-ref: (bref_id, season)
  - crafted <-> b-ref: нормализованное имя (нижний регистр, без диакритики
    и знаков) + season. Редкие коллизии имён отбрасываются с предупреждением.

На выходе: nba_data/out/nba_cpm_dataset.parquet
"""
import re
import unicodedata

import pandas as pd

from config import PARSED_DIR, OUT_DIR, season_label

BREF_PLAYER_TABLES = [   # (файл, префикс колонок)
    ("bref_per_game", "pg_"),
    ("bref_per_36", "p36_"),
    ("bref_per_100", "p100_"),
    ("bref_advanced", "adv_"),
    ("bref_shooting", "shoot_"),
    ("bref_totals", "tot_"),
    ("bref_play_by_play", "pbp_"),
]
KEEP_FROM_BASE = ["season", "bref_id", "Player", "Age", "Team", "Pos", "G", "GS", "MP"]


def norm_name(s) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


_SUFFIX_RE = re.compile(r"(iii|ii|iv|jr|sr)$")

def name_key(s) -> str:
    """Нормализация + срезание суффиксов Jr./II/III: сайты пишут их по-разному
    (b-ref: 'Robert Williams', crafted: 'Robert Williams III')."""
    n = norm_name(s)
    return _SUFFIX_RE.sub("", n)


def dedup(df, keys, tag):
    dup = df.duplicated(subset=keys, keep=False)
    if dup.any():
        print(f"  !! {tag}: {dup.sum()} строк с дублирующимся ключом {keys} - отброшены")
        df = df[~dup]
    return df


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- b-ref: сшиваем 7 игроковых таблиц по (bref_id, season) ---
    base = None
    for name, prefix in BREF_PLAYER_TABLES:
        path = PARSED_DIR / f"{name}.csv"
        if not path.exists():
            print(f"  пропускаю {name}.csv (нет файла)")
            continue
        df = pd.read_csv(path, low_memory=False)
        df = dedup(df, ["bref_id", "season"], name)
        if base is None:
            ren = {c: prefix + c for c in df.columns
                   if c not in KEEP_FROM_BASE and c not in ("table", "Rk", "Awards")}
            base = df.rename(columns=ren)
            continue
        drop = {"Player", "Age", "Team", "Tm", "Pos", "G", "GS", "MP",
                "table", "Rk", "Awards", "bref_id"}
        ren = {c: prefix + c for c in df.columns if c not in drop and c != "season"}
        df = df[["season", "bref_id"] + [c for c in df.columns if c in ren]]
        df = df.rename(columns=ren)
        base = base.merge(df, on=["season", "bref_id"], how="outer")
    if base is not None:
        base["name_norm"] = base["Player"].map(norm_name)
        base["name_key"] = base["Player"].map(name_key)
        print(f"b-ref: {len(base)} строк игрок x сезон")
    else:
        print("b-ref таблицы не найдены (шаги 03/05) — режим «только CraftedNBA»")

    # --- crafted: пейлоады игроков (если есть) ---
    cft = None
    pld_path = PARSED_DIR / "crafted_seasons.csv"
    if pld_path.exists():
        cft = pd.read_csv(pld_path, low_memory=False)
        cft["name_norm"] = cft["player"].map(norm_name)
        cft["name_key"] = cft["player"].map(name_key)
        cft = cft.dropna(subset=["season"])
        cft["_src"] = 1  # приоритетный источник (пейлоады игроков)
        print(f"crafted (пейлоады игроков): {len(cft)} строк игрок x сезон")
    else:
        print("crafted_seasons.csv не найден — пропускаю пейлоады (шаги 02/04)")

    # --- season-finder: все игроки 1952-2027 (в схему пейлоадов) ---
    SF_RENAME = {
        "Player": "player", "CPM": "CraftedPM", "COPM": "CraftedOPM",
        "CDPM": "CraftedDPM", "boxCreation": "boxCreationEst",
        "oLoad": "offensiveLoad", "PasserRtg": "passerRating",
        "cTOV%": "cTOVpct", "rTS%": "rTSpct", "rORB%": "rORBpct", "rDRB%": "rDRBpct",
    }
    SF_KEEP_EXTRA = ["3PMp75", "FGMp75", "FTMp75", "MVP"]  # нет в пейлоадах -> префикс sf_
    SF_PASSTHROUGH = [  # совпадают по смыслу и имени с пейлоадами
        "Season", "SQ", "Portability", "r3PAr", "rPF", "Rookie",
        "PTSp75", "ORBp75", "DRBp75", "REBp75", "STLp75", "BLKp75", "TOVp75",
        "ASTp75", "FGAp75", "3PAp75", "FTAp75",
    ]
    sf_path = PARSED_DIR / "crafted_seasonfinder.csv"
    if sf_path.exists():
        sf = pd.read_csv(sf_path, low_memory=False)
        if base is not None:
            # b-ref основа начинается с 2000-01 (end-year 2001)
            sf = sf[sf["Season"] >= 2001]
        cols = list(dict.fromkeys(
            ["Player"] + SF_PASSTHROUGH + list(SF_RENAME.keys()) + SF_KEEP_EXTRA))
        sf = sf[[c for c in cols if c in sf.columns]].rename(columns=SF_RENAME)
        sf = sf.rename(columns={c: "sf_" + c for c in SF_KEEP_EXTRA if c in sf.columns})
        sf["season"] = sf["Season"].map(season_label)
        for c in sf.columns:
            if c not in ("player", "season", "sf_MVP"):  # sf_MVP — строковый ранг (one/two/…)
                sf[c] = pd.to_numeric(sf[c], errors="coerce")
        sf["name_norm"] = sf["player"].map(norm_name)
        sf["name_key"] = sf["player"].map(name_key)
        sf["_src"] = 2  # запасной источник: заполняет пробелы пейлоадов
        print(f"crafted (season-finder): {len(sf)} строк игрок x сезон")
        # объединяем: пейлоады в приоритете, season-finder добирает остальное
        cft = sf if cft is None else pd.concat([cft, sf], ignore_index=True, sort=False)
    else:
        print("crafted_seasonfinder.csv не найден — работаем только по пейлоадам "
              "(запустите 07_seasonfinder_scrape.py для полного покрытия)")
    if cft is None:
        raise SystemExit("Нет ни b-ref, ни crafted данных — сначала запустите "
                         "шаги сбора (01/07 и/или 03/05).")

    cft = cft.sort_values("_src").drop_duplicates(subset=["name_key", "season"],
                                                  keep="first")
    cft = dedup(cft.drop(columns=["_src"]), ["name_key", "season"], "crafted")
    # Rookie из пейлоадов — bool, из season-finder — 1.0/NaN: унифицируем
    if "Rookie" in cft.columns:
        cft["Rookie"] = cft["Rookie"].map(
            {True: 1.0, False: 0.0, "True": 1.0, "False": 0.0, 1: 1.0, 0: 0.0})
    print(f"crafted объединённый: {len(cft)} строк игрок x сезон с CPM")

    # --- джойн ---
    if base is None:
        # crafted-only режим: единая таблица CraftedNBA без фич b-ref
        out = cft.rename(columns={"player": "Player"})
    else:
        out = base.merge(
            cft.drop(columns=["player", "Season", "name_norm"], errors="ignore"),
            on=["name_key", "season"], how="left", suffixes=("", "_crafted"))

    n_cpm = out["CraftedPM"].notna().sum()
    print(f"итог: {len(out)} строк, из них с таргетом CPM: {n_cpm}")
    by_season = out.groupby("season")["CraftedPM"].apply(lambda s: s.notna().sum())
    print("строк с CPM по сезонам:")
    print(by_season.to_string())

    path = OUT_DIR / "nba_cpm_dataset.parquet"
    out.to_parquet(path, index=False)
    print(f"\nсохранено: {path} ({path.stat().st_size / 1e6:.1f} МБ)")

    if base is None:
        return

    # контроль качества: какие crafted-строки С таргетом не нашли пару в b-ref
    # (у части crafted-строк CPM законно пустой - мало минут, это не ошибка)
    cft_valid = cft[cft["CraftedPM"].notna()]
    matched_keys = set(zip(out.loc[out["CraftedPM"].notna(), "name_key"],
                           out.loc[out["CraftedPM"].notna(), "season"]))
    mask = [k not in matched_keys
            for k in zip(cft_valid["name_key"], cft_valid["season"])]
    unmatched = cft_valid[mask]
    if len(unmatched):
        unmatched[["player", "season"]].to_csv(OUT_DIR / "unmatched_crafted.csv",
                                               index=False)
        print(f"crafted-строк с CPM без пары в b-ref: {len(unmatched)} "
              f"-> out/unmatched_crafted.csv (обычно разнобой в именах)")


if __name__ == "__main__":
    main()
