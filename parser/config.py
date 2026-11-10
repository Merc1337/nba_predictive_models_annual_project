"""Общие настройки пайплайна сбора NBA CPM датасета."""
from pathlib import Path

# Куда складывать все данные (создастся рядом со скриптами)
DATA_DIR = Path("./nba_data")
PAYLOADS_DIR = DATA_DIR / "crafted_payloads"   # сырые JSON CraftedNBA
BREF_DIR = DATA_DIR / "bref_html"              # сырой HTML basketball-reference
PARSED_DIR = DATA_DIR / "parsed"               # промежуточные CSV
OUT_DIR = DATA_DIR / "out"                     # финальный parquet

# CraftedNBA: технические поддомены Cloudflare Pages без защиты CF.
# DEV - быстрый и отдаёт ПОЛНУЮ посезонную историю (это ключевое!).
# PROD - медленный, отдаёт только текущий сезон (история скрыта),
#        но зато работает для Hall of Fame и когда dev глючит.
CRAFTED_DEV = "https://9cfcf932.craftednba.pages.dev"
CRAFTED_PROD = "https://craftednba.pages.dev"

# Basketball-reference
BREF_BASE = "https://www.basketball-reference.com"
# (имя файла, slug в URL). ВНИМАНИЕ: per 36 = per_minute, per 100 = per_poss,
# play-by-play пишется через дефис. Ошибка в slug -> тихий редирект на сводку сезона.
BREF_TABLES = [
    ("per_game",     "per_game"),
    ("per_36",       "per_minute"),
    ("per_100",      "per_poss"),
    ("advanced",     "advanced"),
    ("shooting",     "shooting"),
    ("totals",       "totals"),
    ("play_by_play", "play-by-play"),
    ("ratings",      "ratings"),      # командная таблица (SRS и т.п.), не игроки!
]
# Сезоны 2000-01 .. 2026-27. В URL b-ref год = второй год сезона (2000-01 -> 2001).
BREF_SEASON_YEARS = list(range(2001, 2028))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def season_label(end_year: int) -> str:
    """2001 -> '2000-01' (метка сезона как на basketball-reference)."""
    return f"{end_year - 1}-{str(end_year)[2:]}"
