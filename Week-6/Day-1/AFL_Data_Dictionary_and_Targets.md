# AFL Data Dictionary and Target Definitions

Week 6, Day 1. Contract for every model built the rest of this week. 
## Source tables

| Table | Grain | Rows | Join key |
|---|---|---|---|
| `afl_players_info_raw.csv` | one row per player | 2,848 | `id` |
| `afl_players_seasonal_stats_raw.csv` | one row per player, per season, per finals flag | 25,491 | `player_id` |
| `afl_players_round_by_round_stats_raw.csv` | one row per player, per match | 274,079 (after removing 10 exact duplicate ids) | `player_id`, joined to info/season on `id` |
| `team_matches_home_away_raw.csv` | one row per team, per match | 15,808 | `team`+`opponent`+`match_date`, joined to round-by-round after cleaning names |

There is no shared match id. A match is reconstructed by grouping on `match_date` + `team` + `opponent`. Every team-match combination present in the round-by-round file has a match in the home/away file; the home/away file additionally covers 1,108 team-matches not present in round-by-round.

## Known gaps, updated

Still true across all four files:
- No player position field. Forward/midfielder/defender splits are not derivable without an external mapping.
- No weather data.

Other confirmed gaps:
- Player roster coverage per team-match, in the round-by-round file only, ramps from about 1 tracked player per side in 1983 to full ~22-man rosters by about 1999. Team-match outcome fields sourced from the home/away file (`result`, `margin`, `team_score`, venue, crowd) are complete for all years; any feature built by aggregating individual player rows to team level is only reliable from 1999 onward.
- Fitzroy Lions (1983 to 1996) merged with the Brisbane Bears (1987 to 1996) to form the Brisbane Lions from 1997 onward. Treat these as distinct entities before 1997.
- The home/away file has whitespace, tabs, and inconsistent casing in `team_name`/`opponent` (e.g. `'\tRichmond Tigers'`, `'W. Bulldogs'` instead of `'Western Bulldogs'`, about 15% of `opponent` values all lowercase), and trailing whitespace in `venue`. All cleaned via strip + a small explicit name mapping before use.
- The venue-to-state mapping used for the interstate travel feature is a manual lookup covering the venues that appear in the data, it is not an authoritative geographic dataset, and unmapped venues are labeled `UNKNOWN`.

## Prediction targets

| Target | Grain | Definition | Formula | Framing |
|---|---|---|---|---|
| `win_flag` | team-match | did this team win | 1 if `result` == 'W' else 0 | binary classification (primary) |
| `result_class` | team-match | 3-class outcome | `result` in {W, L, D} | multiclass classification |
| `margin` | team-match | signed score differential | `team_score - opponent_score`, provided directly in source data | regression (secondary) |
| `season_disposals` | player-season | top disposal getter | `sum(disposals)` grouped by player, year | ranking |
| `season_goals` | player-season | top goal kicker | `sum(goals)` grouped by player, year | ranking |
| `fantasy_points` | player-match | composite performance score | `3*kicks + 2*handballs + 3*marks + 4*tackles + free_kicks_for - 3*free_kicks_against + hit_outs + 6*goals + behinds` (verified exact match against source column) | ranking / regression |

Classification is primary for match winner because the practical question is categorical, draws are rare (about 1.4% of matches), and it maps directly onto tipping-style evaluation. Margin regression is kept as a secondary target since a good margin model can be thresholded at 0 to recover the classification, and it's needed for anything involving percentage or premiership margins.

## Engineered feature tables (versioned, leakage-safe)

All rolling and historical features use `.shift(1)` before any window aggregation, so no feature ever uses information from the match it is predicting or any later match.

**`afl_team_match_features_v2`** (all years, team-match grain, backbone is now the home/away file)

| Feature | Description | Window |
|---|---|---|
| `is_home` | is this team the home side | this match |
| `venue`, `venue_state` | match venue and its state (manual mapping) | this match |
| `is_interstate_travel` | is this team, playing away, outside its home state | this match |
| `team_winrate_last3/5/10` | rolling win rate, prior games only | last 3/5/10 |
| `team_avgmargin_last3/5/10` | rolling average margin, prior games only | last 3/5/10 |
| `team_streak_entering` | signed win/loss streak entering this match | all prior games |
| `days_since_last_game` | rest days since previous match | previous match |
| `ladder_rank_to_date` | rank by season win rate, strictly before this round | season to date |
| `h2h_winrate_vs_opp` | win rate vs this specific opponent, prior meetings only | all-time pairing |
| `h2h_games_played` | number of prior meetings vs this opponent | all-time pairing |

**`afl_player_match_features_v2`** (1999 onward, player-match grain, unchanged from v1)

| Feature | Description | Window |
|---|---|---|
| `player_disposals_last3/5` | rolling average disposals, prior games only | last 3/5 |
| `player_fantasy_last3/5` | rolling average fantasy points, prior games only | last 3/5 |

## Train/holdout split

Strict time-based split: train on seasons before the holdout year, hold out the most recent season(s) entirely. A random row-level split would let the model train on later rounds of a season while testing on earlier ones, leaking form and roster information that didn't exist yet, and would scatter a single team's season across both sets, letting the model see near-duplicate answers through correlated games. The reusable split function lives in the notebook (`time_based_split`) and every model this week should call it with the same `holdout_start_year`.

## Realistic accuracy ceiling

AFL outcomes carry real, unmodeled randomness: umpiring decisions, in-game injuries, weather, and the closing minutes of tight contests. Public AFL prediction work using richer inputs than this dataset typically lands around 65 to 72% win/loss classification accuracy. Anything reported meaningfully above the mid-70s, or near-perfect margin prediction, should be treated as a leakage signal rather than a win, most likely from a random split, a feature that encodes the result, or the pre-1999 roster-coverage gap, not a genuine solution to a fundamentally noisy problem.
