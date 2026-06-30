# AFL Player Data — Data Quality, Cleaning & Validation Report

## 1. Overview

Two raw datasets were assessed and cleaned:

| Dataset | Raw rows | Raw columns |

| `afl_players_info_raw.csv` | 2,848 | 16 |
| `afl_players_seasonal_stats_raw.csv` | 25,491 | 54 |

They were profiled for quality issues, cleaned, and merged on `player_id` (stats) / `id` (info)
into a single analysis-ready table, `merged_players.csv`.

## 2. Data Quality Assessment (issues found in raw data)

| # | Dataset | Issue | Count |

| 1 | players_info | Fully duplicated rows | 5 |
| 2 | players_info | `weight` = 0 kg (impossible) | 2 |
| 3 | players_info | Literal `"NULL"` text values + untrimmed whitespace in text fields | widespread |
| 4 | players_info | Date columns stored as untyped text | 3 columns |
| 5 | seasonal_stats | Fully duplicated rows | 10 |
| 6 | seasonal_stats | `player_id` malformed with `"ID_"` text prefix | 10 |
| 7 | seasonal_stats | `player_id` loaded as mixed/object dtype, blocking merge | all rows |
| 8 | seasonal_stats | Negative `games_played` (impossible) | 8 |
| 9 | seasonal_stats | `team` had 114 raw text variants for 20 real clubs (case/whitespace) | 25,491 rows |
| 10 | seasonal_stats | Heavy, era-concentrated missingness in advanced stats (e.g. `bounces`, `brownlow_votes`, `hit_outs`, `contested_possessions`) | up to ~6,997 rows per column |
| 11 | merge | `player_id` values in stats with no match in players_info | 266 distinct players / 400 rows |

## 3. Cleaning actions & rationale

Full machine-readable detail is in `cleaning_log.csv`. Summary:

**players_info**
- Dropped 5 exact duplicate rows (kept first occurrence) — duplicates would double count a player.
- Replaced `weight == 0` with `NaN` — 0 kg is physically impossible; flagging as missing avoids a false data point without discarding the player.
- Trimmed whitespace and converted literal `"NULL"` strings to true `NaN` in name/team text fields — needed for clean joins and groupings.
- Parsed `born_date`, `debut_date`, `last_date` into proper `datetime` columns.
- Cross-checked `debut_age` against dates calculated from `born_date`/`debut_date`; differences were rounding-only, so original values were kept.
- Left `profile_pic`, `player_common_names`, `player_teams` nulls as-is where genuinely unknown — these are optional/cosmetic fields and cannot be reliably inferred.

**seasonal_stats**
- Dropped 10 exact duplicate rows.
- Stripped the `"ID_"` text prefix from 10 malformed `player_id` values — this was a scraping/encoding artifact; the underlying numeric id matches a real player.
- Cast `player_id` to a consistent nullable integer type, required for a reliable merge key.
- Dropped 8 rows with negative `games_played` (all belonging to one player/club/season combination) — games cannot be negative, the rows were also exact duplicates, and there is no reliable way to recover the true value.
- Standardised `team` text to trimmed Title Case, collapsing 114 raw variants down to the correct 20 AFL clubs.
- Left missing values in advanced-stat columns (`bounces`, `hit_outs`, `brownlow_votes`, `contested_possessions`, etc.) as `NaN` rather than imputing zero. Investigation showed missingness is **structural**, tied to which stats the AFL officially tracked in a given era (e.g. `bounces` is ~100% missing before 1992) — not random or erroneous. Imputing 0 would misrepresent "not recorded" as "recorded zero."
- Investigated negative `total_fantasy_points` values (10 rows): sense-checked against `games_played == 1` and confirmed these are legitimate low-impact single-game outcomes under AFL Fantasy scoring rules, not data errors — retained unchanged.

## 4. Merge approach

`seasonal_stats` (the fact table, one row per player-year-team-finals combination) was **left-joined**
onto `players_info` (the player dimension table) on `player_id = id`. A left join was chosen so that
no performance/statistical record is discarded purely because biographical data is missing for that
player — those rows are retained in `merged_players.csv` with blank bio columns.

## 5. Validation Report

| Metric | players_info | seasonal_stats |

| Rows before cleaning | 2,848 | 25,491 |
| Rows after cleaning | 2,843 | 25,477 |
| Duplicate/invalid rows removed | 5 | 14 (10 duplicates + 4 negative-`games_played` duplicates*) |
| Missing values remaining after cleaning | 5,070 | 139,251 |

The 8 negative-`games_played` rows included 4 that were also exact duplicates of each other, hence 14 total rows removed from seasonal_stats (10 duplicate + 4 additional after de-duplication; see `cleaning_log.csv` for the precise sequence).

**Merge outcome**
- Merged dataset: **25,477 rows**
- Unmatched `player_id` rows (stats record with no players_info match): **400 rows / 266 distinct players**
- Full list of unmatched ids: see `validation_report.json`

Remaining missing values after cleaning are expected and documented (era-based stat tracking gaps,
optional metadata fields) rather than indicating unresolved quality problems.

## 6. Observations & Insights

1. **Coverage** -  cleaned data spans 1983–2025 across 20 AFL clubs and ~3,100 distinct players.
2. **Scoring trend** - average goals per game fell from ~1.8 in the 1980s to under 0.7 in the
   2010s/2020s, consistent with the league's known shift toward congested, possession-based play.
3. **Advanced metrics are era-limited** - `bounces`, `contested_possessions`, `hit_outs`, etc. are
   only reliably populated from the 1990s/2000s onward; any modelling using these fields should
   filter by era or explicitly handle missingness rather than imputing zeros.
4. **266 players have stats but no profile** - likely a gap in how `players_info` was originally
   sourced/scraped. These were kept (not dropped) since the performance data is still valid; the
   id list is provided for follow-up data sourcing.
5. **Source data shows scraping/encoding artifacts**, not just normal missingness — the `"ID_"`
   prefix and inconsistent club-name casing both point to the raw files being assembled from
   multiple unstandardised sources.
