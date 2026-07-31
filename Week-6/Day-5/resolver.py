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

from data_loader import resolve_team_name, resolve_player_name


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


def extract_player_mention(query: str, exclude_words: List[str]) -> Optional[str]:
    """Player names aren't cued by a fixed vocabulary the way teams are,
    so this tries candidate capitalized n-grams (2 words, then 1) against
    the real player table and keeps the first that resolves uniquely.
    exclude_words filters out team names already found, so 'Richmond' in
    'Richmond's Dustin Martin' doesn't get tried as a player name."""
    raw_tokens = re.findall(r"[A-Z][a-zA-Z'.-]+", query)
    tokens = [re.sub(r"'s$", "", t) for t in raw_tokens]
    tokens = [t for t in tokens if t not in exclude_words]
    for i in range(len(tokens) - 1):
        candidate = f"{tokens[i]} {tokens[i+1]}"
        result = resolve_player(candidate)
        if "resolved" in result:
            return result["resolved"]
    for t in tokens:
        result = resolve_player(t)
        if "resolved" in result:
            return result["resolved"]
    return None


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
        if len(team_hits) < 2 and re.search(r"\b(they|them|their|it)\b", query, re.IGNORECASE):
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
            issues.append({"field": "round_number", "error": "No round specified."})

    date_info = resolve_date_phrase(entities.get("date_phrase"), entities.get("year"))
    entities["resolved_date"] = date_info["date"]
    if date_info["resolved_year"] and "year" not in entities:
        entities["year"] = date_info["resolved_year"]
    if date_info["note"]:
        entities["date_note"] = date_info["note"]

    log_step(state, "resolve_entities", entities=dict(entities), issues=issues)
    return {"entities": entities, "_resolution_issues": issues}
