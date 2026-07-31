"""
resolver.py
============

Task 3 (input resolution half): turn the router's raw slot guesses into
values the actual tools/models can use -- real dataset team names instead
of nicknames, a real player row instead of a typed name, and a concrete
date instead of "this week".

This is deliberately a separate node from the router. The router's job is
"what is this query about" (fast, regex-level); this node's job is "can I
actually find that team/player/date in the data" (touches the dataframes
and can fail). Keeping them apart means a resolution failure doesn't need
to re-run intent classification, it just flows into validation.py.
"""

import re
import datetime
from typing import Optional, Dict, Any, List

from data_loader import resolve_team_name, resolve_player_name, load_player_game_stats, load_player_info


# ---------------------------------------------------------------------------
# get_current_date "tool" -- deliberately its own function (not inlined)
# so it can be registered as an actual LangGraph/LangChain tool if this
# graph is later wired into an LLM-driven router, per the brief's ask for
# a tool the router/prediction node can call to ground relative phrases.
# ---------------------------------------------------------------------------
def get_current_date() -> str:
    """Returns today's real date (ISO format). The single source of truth
    for resolving 'this week' / 'last year' / 'this season' -- never
    inferred from the dataset's own date range, since that range is
    historical and does not track real-world 'today'."""
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Team nickname -> alias table. resolve_team_name() in data_loader.py
# already does partial/substring matching against real dataset names
# ("tigers" -> "Richmond Tigers"), but common single-word AFL nicknames
# ("Pies", "Cats", "Dons") don't substring-match their real names at all,
# so they need an explicit lookup first.
# ---------------------------------------------------------------------------
_TEAM_PHRASES = [
    ("greater western sydney", "Greater Western Sydney Giants"),
    ("north melbourne", "North Melbourne Kangaroos"),
    ("west coast", "West Coast Eagles"),
    ("gold coast", "Gold Coast Suns"),
    ("port adelaide", "Port Adelaide Power"),
    ("st kilda", "St Kilda Saints"),
    ("western bulldogs", "Western Bulldogs"),
    ("brisbane lions", "Brisbane Lions"),
    ("brisbane bears", "Brisbane Bears"),
    ("pies", "Collingwood Magpies"), ("magpies", "Collingwood Magpies"),
    ("cats", "Geelong Cats"), ("tigers", "Richmond Tigers"),
    ("dons", "Essendon Bombers"), ("bombers", "Essendon Bombers"),
    ("blues", "Carlton Blues"), ("demons", "Melbourne Demons"), ("dees", "Melbourne Demons"),
    ("swans", "Sydney Swans"), ("eagles", "West Coast Eagles"),
    ("dockers", "Fremantle Dockers"), ("freo", "Fremantle Dockers"),
    ("power", "Port Adelaide Power"), ("crows", "Adelaide Crows"),
    ("saints", "St Kilda Saints"), ("bulldogs", "Western Bulldogs"), ("dogs", "Western Bulldogs"),
    ("hawks", "Hawthorn Hawks"), ("kangaroos", "North Melbourne Kangaroos"), ("roos", "North Melbourne Kangaroos"),
    ("giants", "Greater Western Sydney Giants"), ("gws", "Greater Western Sydney Giants"),
    ("suns", "Gold Coast Suns"),
    ("collingwood", "Collingwood Magpies"), ("essendon", "Essendon Bombers"),
    ("richmond", "Richmond Tigers"), ("carlton", "Carlton Blues"),
    ("melbourne", "Melbourne Demons"), ("geelong", "Geelong Cats"),
    ("fremantle", "Fremantle Dockers"), ("hawthorn", "Hawthorn Hawks"),
    ("sydney", "Sydney Swans"), ("adelaide", "Adelaide Crows"),
    ("fitzroy", "Fitzroy Lions"),
    ("lions", "Brisbane Lions"),
    ("brisbane", "Brisbane Lions"),
    ("western", "Western Bulldogs"),
]
_TEAM_PHRASES.sort(key=lambda pair: len(pair[0]), reverse=True)


def resolve_team(raw_text: str) -> Dict[str, Any]:
    """Resolve a team mention (nickname, partial name, or full name) to
    exactly one dataset team name. Returns {"resolved": str} on success or
    {"error": str, "candidates": [...]} on failure."""
    text = raw_text.lower().strip()
    for phrase, team in _TEAM_PHRASES:
        if phrase == text:
            return {"resolved": team}

    matches = resolve_team_name(text)
    if len(matches) == 1:
        return {"resolved": matches[0]}
    if len(matches) == 0:
        return {"error": f"Could not match '{raw_text}' to any known team.", "candidates": []}
    return {"error": f"'{raw_text}' matches multiple teams.", "candidates": matches}


def resolve_team_mentions(query: str) -> List[Dict[str, Any]]:
    """Scan the query for team phrase/nickname hits, longest-phrase-first,
    masking each match out of a working copy so overlapping shorter
    phrases (e.g. 'adelaide' inside 'port adelaide') can't double-match
    the same span. Returns resolved team dicts ordered by first
    appearance in the sentence."""
    working = f" {query.lower()} "
    hits = []

    for phrase, team in _TEAM_PHRASES:
        pattern = re.compile(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])")
        match = pattern.search(working)
        if match and team not in [t for _, t in hits]:
            hits.append((match.start(), team))
            working = working[:match.start()] + ("#" * len(phrase)) + working[match.end():]

    hits.sort(key=lambda h: h[0])
    return [{"resolved": team} for _, team in hits[:2]]


def resolve_player(raw_text: str) -> Dict[str, Any]:
    """Resolve a player name mention against the bio table. Same
    zero/one/many contract as resolve_team."""
    matches = resolve_player_name(raw_text)
    if matches.empty:
        return {"error": f"Could not find a player matching '{raw_text}'.", "candidates": []}
    unique_names = matches["player_name"].unique().tolist()
    if len(unique_names) > 1:
        return {"error": f"'{raw_text}' matches multiple players.", "candidates": unique_names}
    return {"resolved": unique_names[0]}


# ---------------------------------------------------------------------------
# Generic query vocabulary to strip out during the lowercase fallback below
# -- words a user's own phrasing contributes ("give me ... stats for ...")
# that would otherwise get thrown at resolve_player() as noise candidates.
# This is deliberately conservative (verbs/prepositions/stat-jargon), not
# an attempt to enumerate every English word, since resolve_player() itself
# is the real safety net: a stray candidate simply won't match anyone.
# ---------------------------------------------------------------------------
_QUERY_STOPWORDS = {
    "give", "me", "get", "show", "tell", "please", "can", "could", "you",
    "what", "whats", "were", "was", "is", "are", "his", "her", "he", "she",
    "it", "they", "them", "their", "the", "a", "an", "of", "for", "and",
    "in", "on", "at", "to", "from", "stats", "stat", "statline", "season",
    "seasons", "year", "years", "did", "how", "many", "much", "who",
    "which", "that", "this", "last", "next", "round", "game", "match",
    "disposals", "kicks", "marks", "goals", "tackles", "handballs",
    "points", "s", "i", "mean", "sorry", "meant", "actually", "correction",
    "scratch", "about", "vs", "versus", "against", "with",
}


def extract_player_mention(query: str, exclude_words: List[str]) -> Optional[str]:
    """Player names aren't cued by a fixed vocabulary the way teams are,
    so this tries candidate n-grams (2 words, then 1) against the real
    player table and keeps the first that resolves uniquely. exclude_words
    filters out team names already found, so 'Richmond' in 'Richmond's
    Dustin Martin' doesn't get tried as a player name.

    Two passes:
      1. Capitalized tokens ("Dustin Martin") -- the common case for
         well-formed queries, and cheap/precise since capitalization
         alone rules out almost everything that isn't a name.
      2. Lowercase fallback, only if pass 1 finds nothing -- chat input
         is very often not properly capitalized ("give me dustin
         martin's stats for 2017"), so requiring capital case would mean
         those queries never even attempt a lookup. This pass tokenizes
         case-insensitively and filters out generic query vocabulary
         (_QUERY_STOPWORDS) before trying resolve_player(), so it isn't
         just throwing every word in the sentence at the player table.
    """
    exclude_lower = {w.lower() for w in exclude_words}

    def _try(tokens: List[str]) -> Optional[str]:
        for i in range(len(tokens) - 1):
            candidate = f"{tokens[i]} {tokens[i + 1]}"
            result = resolve_player(candidate)
            if "resolved" in result:
                return result["resolved"]
        for t in tokens:
            result = resolve_player(t)
            if "resolved" in result:
                return result["resolved"]
        return None

    raw_tokens = re.findall(r"[A-Z][a-zA-Z'.-]+", query)
    cap_tokens = [re.sub(r"'s$", "", t) for t in raw_tokens]
    cap_tokens = [t for t in cap_tokens if t not in exclude_words]
    found = _try(cap_tokens)
    if found:
        return found

    raw_tokens_ci = re.findall(r"[A-Za-z][a-zA-Z'.-]+", query)
    ci_tokens = [re.sub(r"'s$", "", t) for t in raw_tokens_ci]
    ci_tokens = [
        t for t in ci_tokens
        if t.lower() not in _QUERY_STOPWORDS and t.lower() not in exclude_lower
    ]
    return _try(ci_tokens)


# ---------------------------------------------------------------------------
# Date-phrase resolution
# ---------------------------------------------------------------------------
def resolve_date_phrase(date_phrase: Optional[str], explicit_year: Optional[int]) -> Dict[str, Any]:
    """
    Turns a relative phrase into something concrete, grounded against
    get_current_date() rather than the dataset's own (historical) date
    range. Returns a dict with:
      - "date": ISO date string to pass to predict.py, or None
      - "resolved_year": int or None, for retrieval tools that key by year
      - "note": a caveat to surface to the user, if relevant (e.g. no
        fixture list exists, so "this week" can't be tied to a real match)
    """
    today = datetime.date.fromisoformat(get_current_date())

    if explicit_year:
        return {"date": f"{explicit_year}-06-01", "resolved_year": explicit_year, "note": None}

    if date_phrase in ("this week", "this weekend", "next round", "next week", "upcoming"):
        return {
            "date": today.isoformat(),
            "resolved_year": today.year,
            "note": (
                "No fixture list is available, so this isn't tied to a confirmed "
                "scheduled match -- it's based on each team's most recently "
                "recorded form."
            ),
        }
    if date_phrase == "last year":
        return {"date": f"{today.year - 1}-06-01", "resolved_year": today.year - 1, "note": None}
    if date_phrase in ("this season", "this year"):
        return {"date": f"{today.year}-06-01", "resolved_year": today.year, "note": None}
    if date_phrase == "next season":
        return {"date": f"{today.year + 1}-03-01", "resolved_year": today.year + 1, "note": None}

    return {"date": None, "resolved_year": None, "note": None}


# ---------------------------------------------------------------------------
# "last round" / "last game" resolution -- deliberately NOT routed to
# direct_answer_node's LLM call. That node exists for general AFL
# knowledge no tool covers (history, rules); a specific stat question is
# exactly what its own SYSTEM_PROMPT forbids it from answering freely
# ("never estimate, recall from memory, or guess a statistic"). Putting a
# real stat question in front of it would mean relying on prompt wording
# alone to stop a hallucinated number, instead of the code-level guarantee
# every other retrieval path already has.
#
# Instead: ground "last round" in the actual most recent game recorded
# for this player in the dataset, and say so plainly. The dataset caps at
# a real year (2025 as of writing) -- "last round" typed today could mean
# "the most recent round of the live season" (which the data doesn't have
# at all) or "the most recent round on record". There's no way to
# disambiguate that from the text alone, so rather than guess which one
# the user meant, this resolves to the one answer that's actually
# checkable -- the real last recorded game -- and attaches a caveat
# explaining that's what happened, mirroring resolve_date_phrase's
# existing "this week" pattern (a real value plus an honest note, not a
# silent assumption).
# ---------------------------------------------------------------------------
def resolve_last_round(player_name: str, year: Optional[int] = None) -> Dict[str, Any]:
    """Returns {"year": int, "round_number": str, "note": str} for the most
    recent recorded game for this (already-resolved, exact) player name,
    optionally constrained to a given year. Returns {"error": str} if no
    games are on record at all (e.g. name resolved but has zero rows in
    the game-level table)."""
    info = load_player_info()
    id_matches = info[info["player_name"] == player_name]
    if id_matches.empty:
        return {"error": f"No game records found for '{player_name}'."}
    player_id = id_matches.iloc[0]["id"]

    games = load_player_game_stats()
    rows = games[games["player_id"] == player_id]
    if year:
        rows = rows[rows["year"] == year]
    if rows.empty:
        return {"error": f"No recorded games found for '{player_name}'" + (f" in {year}." if year else ".")}

    last_row = rows.sort_values("match_date").iloc[-1]
    resolved_year = int(last_row["year"])
    resolved_round = str(last_row["round"])

    note = (
        f"Our data currently runs through the {resolved_year} season, so this is "
        f"the most recently recorded game for {player_name} on file (round "
        f"{resolved_round}, {resolved_year}) -- not necessarily their newest "
        f"real-world game if a later season isn't in the dataset yet."
    )
    return {"year": resolved_year, "round_number": resolved_round, "note": note}


def _teams_from_history(chat_history) -> List[Dict[str, Any]]:
    """For follow-up turns using pronouns ('they', 'them') instead of
    naming teams again, look back through recent user turns for a team
    pair already resolved, rather than failing to resolve them at all."""
    for turn in reversed(chat_history):
        if turn.get("role") != "user":
            continue
        hits = resolve_team_mentions(turn["content"])
        if len(hits) == 2:
            return hits
    return []


# ---------------------------------------------------------------------------
# Day 5 (round 2) fix -- closes the Category D gap identified in
# EVAL_RESULTS.md ("no equivalent of _teams_from_history for players or
# years"). Mirrors _teams_from_history's exact shape: scan chat_history in
# reverse over user turns only, re-run the same extraction the current
# turn would have used, and return the first hit. Re-extracting from the
# raw prior text (rather than reading back a resolved entity dict) matches
# how _teams_from_history already works, since chat_history here is just
# {"role", "content"} pairs -- there's no stored per-turn entity state to
# read back from.
# ---------------------------------------------------------------------------
def _players_from_history(chat_history) -> Optional[str]:
    """For follow-up turns using 'he'/'him'/'his' instead of naming the
    player again, look back through recent user turns for a player name
    that resolves uniquely, most recent turn first."""
    for turn in reversed(chat_history):
        if turn.get("role") != "user":
            continue
        team_hits = resolve_team_mentions(turn["content"])
        exclude = [h.get("resolved", "") for h in team_hits]
        player = extract_player_mention(turn["content"], exclude_words=exclude)
        if player:
            return player
    return None


def _year_from_history(chat_history) -> Optional[int]:
    """For follow-up turns using 'that year'/'that season' instead of
    restating the year, look back through recent user turns for the most
    recent explicit 4-digit year mentioned."""
    for turn in reversed(chat_history):
        if turn.get("role") != "user":
            continue
        full_years = re.findall(r"\b(?:19|20)\d{2}\b", turn["content"])
        if full_years:
            return int(full_years[-1])
    return None


_PLAYER_PRONOUN_RE = re.compile(r"\b(he|him|his)\b", re.IGNORECASE)
_YEAR_CARRYOVER_RE = re.compile(r"\b(that|same) (year|season)\b", re.IGNORECASE)


def resolve_entities_node(state) -> dict:
    from state import log_step
    query = state["query"]
    intent = state["intent"]
    sub_type = state["sub_type"]
    entities = dict(state.get("entities", {}))
    issues = []

    if intent in ("prediction", "retrieval") and sub_type in ("match", "head_to_head"):
        team_hits = resolve_team_mentions(query)
        # LLM-provided hints (router.py's llm_router.py) take priority
        # over both the regex scan and history carry-over -- if the
        # extractor already read team_a/team_b (including resolving a
        # pronoun against chat history itself), trust that over
        # re-deriving from the raw query text.
        llm_hits = []
        for raw in (entities.get("team_a"), entities.get("team_b")):
            if raw:
                resolved = resolve_team(raw)
                llm_hits.append(resolved if "resolved" in resolved else {"error": resolved.get("error", ""), "candidates": resolved.get("candidates", [])})
        if len(llm_hits) >= 2:
            team_hits = llm_hits
        elif len(team_hits) < 2 and re.search(r"\b(they|them|their|it)\b", query, re.IGNORECASE):
            team_hits = _teams_from_history(state.get("chat_history", [])) or team_hits
        if len(team_hits) >= 1:
            entities["team_a_raw"] = team_hits[0]
        if len(team_hits) >= 2:
            entities["team_b_raw"] = team_hits[1]
        for i, hit in enumerate(team_hits[:2]):
            label = "team_a" if i == 0 else "team_b"
            if "resolved" in hit:
                entities[label] = hit["resolved"]
            else:
                issues.append({"field": label, **hit})
        if len(team_hits) < 2:
            issues.append({"field": "team_b" if team_hits else "team_a",
                            "error": "Could not find two teams mentioned in the query."})

    elif intent in ("prediction", "retrieval") and sub_type in ("player", "team_leaders"):
        team_hits = resolve_team_mentions(query)
        if team_hits:
            if "resolved" in team_hits[0]:
                entities["team"] = team_hits[0]["resolved"]
            else:
                issues.append({"field": "team", **team_hits[0]})
        else:
            issues.append({"field": "team", "error": "Could not find a team mentioned in the query."})
        if sub_type == "team_leaders" and not entities.get("year"):
            issues.append({"field": "year", "error": "No season/year specified for the leaderboard."})

    elif intent == "retrieval" and sub_type in ("player_season", "player_game"):
        team_hits = resolve_team_mentions(query)
        exclude = [h.get("resolved", "") for h in team_hits]
        player = None
        llm_player_hint = entities.get("player_name")
        if llm_player_hint:
            hint_result = resolve_player(llm_player_hint)
            if "resolved" in hint_result:
                player = hint_result["resolved"]
        if not player:
            player = extract_player_mention(query, exclude_words=exclude)
        if not player and _PLAYER_PRONOUN_RE.search(query):
            player = _players_from_history(state.get("chat_history", []))
        if player:
            entities["player_name"] = player
        else:
            issues.append({"field": "player_name",
                            "error": "Could not identify a player name in the query."})
        if "year" not in entities or entities.get("year") is None:
            if _YEAR_CARRYOVER_RE.search(query):
                hist_year = _year_from_history(state.get("chat_history", []))
                if hist_year:
                    entities["year"] = hist_year
        if ("year" not in entities or entities.get("year") is None) and sub_type == "player_season":
            issues.append({"field": "year", "error": "No season/year specified."})
        if sub_type == "player_game" and "round_number" not in entities:
            if player and entities.get("round_phrase"):
                last_round_result = resolve_last_round(player, entities.get("year"))
                if "error" not in last_round_result:
                    entities["round_number"] = last_round_result["round_number"]
                    if "year" not in entities or entities.get("year") is None:
                        entities["year"] = last_round_result["year"]
                    entities["round_note"] = last_round_result["note"]
                else:
                    issues.append({"field": "round_number", "error": last_round_result["error"]})
            else:
                issues.append({"field": "round_number", "error": "No round specified."})

    date_info = resolve_date_phrase(entities.get("date_phrase"), entities.get("year"))
    entities["resolved_date"] = date_info["date"]
    if date_info["resolved_year"] and "year" not in entities:
        entities["year"] = date_info["resolved_year"]
    if date_info["note"]:
        entities["date_note"] = date_info["note"]

    log_step(state, "resolve_entities", entities=dict(entities), issues=issues)
    return {"entities": entities, "_resolution_issues": issues}