# RP Spray Analytics
# Copyright © 2026 Ryan Phillips
# All rights reserved.
# Unauthorized copying, distribution, or resale prohibited.

import streamlit as st
st.cache_data.clear()
import os
import json
import base64
import re
import hashlib
import httpx
import time  # anti-stuck processing lock + failsafe unlock
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd
from io import BytesIO

from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule, FormulaRule, CellIsRule
from supabase import create_client, Client

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def hash_access_code(code: str) -> str:
    pepper = st.secrets["ACCESS_CODE_PEPPER"]
    raw = (code.strip() + pepper).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
def admin_set_access_code(team_lookup: str, new_plain_code: str) -> bool:
    team_lookup = (team_lookup or "").strip().upper()
    new_plain_code = (new_plain_code or "").strip().upper()  # <-- FIXED

    if not team_lookup or not new_plain_code:
        return False

    new_hash = hash_access_code(new_plain_code).strip().lower()

    # Try by team_code first
    res = supabase.table("team_access").update(
        {"code_hash": new_hash, "is_active": True}
    ).eq("team_code", team_lookup).execute()

    if res.data:
        return True

    # Fallback by team_slug
    res2 = supabase.table("team_access").update(
        {"code_hash": new_hash, "is_active": True}
    ).eq("team_slug", team_lookup).execute()

    return bool(res2.data)


# -----------------------------
# PATHS / FOLDERS
# -----------------------------
SETTINGS_PATH = os.path.join("TEAM_CONFIG", "team_settings.json")
ASSETS_DIR = "assets"
os.makedirs(ASSETS_DIR, exist_ok=True)

# FORCE include team data folders (Streamlit Cloud quirk) — but don't crash if missing
try:
    if os.path.exists("data/teams"):
        _ = os.listdir("data/teams")
except Exception:
    pass


# -----------------------------
# SETTINGS LOADER
# -----------------------------
def load_settings():
    defaults = {
        "app_title": "RP Spray Charts",
        "subtitle": "Coaches that want to win, WILL put in the time",
        "primary_color": "#b91c1c",
        "secondary_color": "#111111",
        "background_image": os.path.join("assets", "background.jpg"),
        "logo_image": os.path.join("assets", "logo.png"),
        "strict_mode_default": True,
    }
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                defaults.update({k: v for k, v in user.items() if v is not None})
        except Exception:
            pass
    return defaults


SETTINGS = load_settings()
settings = SETTINGS  # alias so the rest of the code can use `settings`


# -----------------------------
# ✅ MUST BE FIRST STREAMLIT CALL
# -----------------------------
st.set_page_config(
    page_title=SETTINGS.get("app_title", "RP Spray Charts"),
    page_icon="⚾",
    layout="wide",
)

# ============================
# ACCESS CODE GATE
# ============================


@st.cache_data(show_spinner=False)
def load_team_codes() -> dict:
    try:
        res = (
            supabase.table("team_access")
            .select("team_slug, team_code, team_name, code_hash, is_active")
            .eq("is_active", True)
            .execute()
        )
        rows = res.data or []
        out = {}
        for r in rows:
            if r.get("team_code"):
                out[str(r["team_code"]).strip().upper()] = r
            if r.get("team_slug"):
                out[str(r["team_slug"]).strip().upper()] = r
        return out
    except Exception:
        return {}
def license_is_active(team_code: str) -> bool:
    """
    Returns True if this team has an active license (and not expired, if expires_at is set).
    Table: licenses (team_code text, status text, expires_at timestamptz)
    """
    try:
        res = (
            supabase.table("licenses")
            .select("status, expires_at")
            .eq("team_code", str(team_code).strip().upper())
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return False  # no row = not licensed

        row = rows[0]
        status = str(row.get("status", "")).strip().lower()
        if status != "active":
            return False

        # Optional: expiration
        exp = row.get("expires_at")
        if exp:
            from datetime import datetime, timezone
            # Supabase returns ISO string typically
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                return False

        return True
    except Exception:
        return False
        

def require_team_access():
    codes = load_team_codes()

    if "team_code" not in st.session_state:
        st.session_state.team_code = None

    # Already logged in
    if st.session_state.team_code in codes:
        return st.session_state.team_code, codes[st.session_state.team_code]

    # Login screen
    st.title("Welcome to the Jungle of RP Spray Analytics")
    st.markdown("### Enter Access Code")

    code_raw = st.text_input("Access Code", value="")

    if st.button("Enter into the door of Success"):
        code = code_raw.strip().upper()

        if not code:
            st.error("Enter an access code")
        else:
            hashed = hash_access_code(code).strip().lower()
            row = codes.get(code)
            stored = str((row or {}).get("code_hash", "")).strip().lower()

            if row and hashed == stored:
                team_code = str(row.get("team_code", "")).strip().upper()

                if not license_is_active(team_code):
                    st.error("License inactive. Contact admin.")
                    st.stop()

                st.session_state.team_code = team_code
                st.rerun()

            else:
                st.error("Invalid access code")

    st.stop()
    return None, None

TEAM_CODE, _ = require_team_access()

# Load full team config (logo/background/data_folder) from TEAM_CONFIG/team_settings.json
def _load_team_cfg_from_file(team_code: str) -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        teams = data.get("teams", {}) or {}
        branding = data.get("team_branding", {}) or {}

        # Find the team entry whose team_code matches TEAM_CODE
        cfg = None
        for _, t in teams.items():
            if str(t.get("team_code", "")).strip().upper() == str(team_code).strip().upper():
                cfg = t
                break

        cfg = cfg or {}

        # Apply branding override (your new source of truth)
        b = branding.get(str(team_code).strip().upper(), {}) or {}
        if b.get("logo_path"):
            cfg["logo_path"] = b["logo_path"]
        if b.get("background_path"):
            cfg["background_path"] = b["background_path"]

        return cfg
    except Exception:
        return {}

TEAM_CFG = _load_team_cfg_from_file(TEAM_CODE) or {}

# -----------------------------
# TERMS OF USE (one-time per browser)
# -----------------------------
if "terms_accepted" not in st.session_state:
    st.session_state.terms_accepted = False


if not st.session_state.terms_accepted:
    st.markdown("### Terms of Use")

    st.markdown(
        """
By using **RP Spray Analytics**, you acknowledge that:

- This software and its analytics models are **proprietary**
- The logic, parsing rules, and reports may **not be copied, shared, or resold**
- Data entered is provided by the user and analyzed by this application

Unauthorized duplication or redistribution is prohibited.
        """
    )

    agree = st.checkbox("I agree to the Terms of Use")

    if st.button("Continue"):
        if not agree:
            st.error("You must agree to continue.")
        else:
            st.session_state.terms_accepted = True
            st.rerun()

    st.stop()


# -----------------------------
# RESOLVED TEAM BRANDING (logo + background)
# -----------------------------
LOGO_PATH = TEAM_CFG.get("logo_path") or SETTINGS.get("logo_image")
BG_PATH   = TEAM_CFG.get("background_path") or SETTINGS.get("background_image")


# -----------------------------
# ✅ TEAM-ISOLATED STORAGE (folders only for rosters; totals are in Supabase)
# -----------------------------
TEAM_CODE_SAFE = str(TEAM_CODE).strip().upper()
TEAM_ROOT = os.path.join("data", "teams", TEAM_CODE_SAFE)
TEAM_ROSTERS_DIR = os.path.join(TEAM_ROOT, "rosters")
TEAM_SEASON_DIR = os.path.join(TEAM_ROOT, "season_totals")  # legacy folder; not used for season totals anymore
os.makedirs(TEAM_ROSTERS_DIR, exist_ok=True)
os.makedirs(TEAM_SEASON_DIR, exist_ok=True)


# -----------------------------
# ENGINE CONSTANTS (MUST EXIST BEFORE empty_stat_dict/db_load)
# -----------------------------
LOCATION_KEYS = ["LF", "CF", "RF", "3B", "SS", "2B", "1B", "P", "Bunt", "Sac Bunt", "UNKNOWN"]
BALLTYPE_KEYS = ["GB", "FB"]
COMBO_LOCS = [loc for loc in LOCATION_KEYS if loc not in ["Bunt", "Sac Bunt", "UNKNOWN"]]
COMBO_KEYS = [f"GB-{loc}" for loc in COMBO_LOCS] + [f"FB-{loc}" for loc in COMBO_LOCS]

# Running event tracking (NOT balls in play)
RUN_KEYS = [
    # Stolen Bases
    "SB", "SB-2B", "SB-3B",
    # Caught Stealing
    "CS", "CS-2B", "CS-3B",
]

# -----------------------------
# PITCHING (YUKON) — IP / K / BB / STRIKE%
# -----------------------------
HALF_INNING_RE = re.compile(r"^(Top|Bottom)\s+\d+(?:st|nd|rd|th)?\s*-\s*(.+?)\s*$", re.IGNORECASE)
OUTS_MARKER_RE = re.compile(r"^\s*([123])\s+Outs?\s*$", re.IGNORECASE)

PBP_PITCHER_RE = re.compile(r"\b([A-Z]\s+[A-Za-z][A-Za-z'\-\.#0-9]+)\s+pitching\b")
PBP_IN_FOR_PITCHER_RE = re.compile(r"\b([A-Z]\s+[A-Za-z][A-Za-z'\-\.#0-9]+)\s+in\s+for\s+pitcher\b")

BALL_TOK_RE = re.compile(r"\bBall\s+[1234]\b", re.IGNORECASE)
STRIKE_TOK_RE = re.compile(r"\bStrike\s+[123]\b", re.IGNORECASE)
FOUL_TOK_RE = re.compile(r"\bFoul\b", re.IGNORECASE)
INPLAY_TOK_RE = re.compile(r"\bIn\s+play\b", re.IGNORECASE)

def parse_batting_team_from_half(line: str) -> Optional[str]:
    s = (line or "").strip().strip('"')
    m = HALF_INNING_RE.match(s)
    return m.group(2).strip() if m else None

def parse_outs_marker(line: str) -> Optional[int]:
    s = (line or "").strip().strip('"')
    m = OUTS_MARKER_RE.match(s)
    return int(m.group(1)) if m else None

def parse_pitcher_from_line(line: str) -> Optional[str]:
    s = (line or "").strip().strip('"')
    m = PBP_PITCHER_RE.search(s)
    if m:
        return m.group(1).strip()
    m = PBP_IN_FOR_PITCHER_RE.search(s)
    if m:
        return m.group(1).strip()
    return None

def count_pitch_tokens(line: str) -> Tuple[int, int]:
    """Returns (pitches, strikes) from visible pitch tokens in GC pitch strings."""
    s = line or ""
    balls = len(BALL_TOK_RE.findall(s))
    strikes = len(STRIKE_TOK_RE.findall(s))
    fouls = len(FOUL_TOK_RE.findall(s))
    inplay = len(INPLAY_TOK_RE.findall(s))
    pitches = balls + strikes + fouls + inplay
    strike_ct = strikes + fouls + inplay
    return pitches, strike_ct

def is_strikeout_detail(line: str) -> bool:
    s = (line or "").lower()
    if s.strip() == "strikeout":
        return False
    return (" strikes out" in s) or ("called out on strikes" in s)

def is_walk_detail(line: str) -> bool:
    s = (line or "").lower().strip()
    if s == "walk":
        return False
    # includes intentional walk lines as well
    return (" walks" in s) or ("intentionally walked" in s) or ("base on balls" in s)

def is_hbp_detail(line: str) -> bool:
    s = (line or "").lower().strip()
    if s in {"hit by pitch", "hit-by-pitch"}:
        return False
    return ("hit by pitch" in s) or ("is hit by pitch" in s)

def empty_pitching_stat() -> dict:
    return {"OUTS": 0, "K": 0, "BB": 0, "PITCHES": 0, "STRIKES": 0}

def ensure_pitching_keys(d: dict) -> dict:
    d.setdefault("OUTS", 0)
    d.setdefault("K", 0)
    d.setdefault("BB", 0)
    d.setdefault("PITCHES", 0)
    d.setdefault("STRIKES", 0)
    return d

def outs_to_ip_str(outs: int) -> str:
    inn = int(outs or 0) // 3
    rem = int(outs or 0) % 3
    return f"{inn}.{rem}"

# -----------------------------
# TEAM NAME MATCHING (PBP) — for reliable defense/offense detection
# -----------------------------
_TEAM_STOPWORDS = {"varsity","jv","freshman","frosh","hs","high","school","baseball"}

def _team_tokens(name: str):
    s = (name or "").lower()
    toks = re.findall(r"[a-z0-9]+", s)
    toks = [t for t in toks if t and t not in _TEAM_STOPWORDS and len(t) >= 3]
    return set(toks)

def team_matches_pbp(batting_team: str, our_team_name: str) -> bool:
    """
    True if the half-inning header team name appears to be OUR team.
    Uses token overlap (robust to 'Varsity', spacing, etc.).
    """
    bt = _team_tokens(batting_team)
    ot = _team_tokens(our_team_name)
    if not bt or not ot:
        return False
    return len(bt.intersection(ot)) > 0





# -----------------------------
# STAT HELPERS
# -----------------------------
def empty_stat_dict():
    d = {loc: 0 for loc in LOCATION_KEYS}
    for k in BALLTYPE_KEYS:
        d[k] = 0
    for ck in COMBO_KEYS:
        d[ck] = 0
    for rk in RUN_KEYS:
        d[rk] = 0
    return d


def ensure_all_keys(d: dict):
    for loc in LOCATION_KEYS:
        d.setdefault(loc, 0)
    for k in BALLTYPE_KEYS:
        d.setdefault(k, 0)
    for ck in COMBO_KEYS:
        d.setdefault(ck, 0)
    for rk in RUN_KEYS:
        d.setdefault(rk, 0)
    return d


# -----------------------------
# PBP NORMALIZATION + GAME HASH
# -----------------------------
def normalize_pbp(text: str) -> str:
    return "\n".join([ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()])


def game_key_from_pbp(team_key: str, pbp_text: str) -> str:
    norm = normalize_pbp(pbp_text)
    h = hashlib.sha1((team_key + "||" + norm).encode("utf-8")).hexdigest()
    return f"pbp_sha1_{h}"


# -----------------------------
# REGEX / PATTERNS (ENGINE)
# -----------------------------
GB_REGEX = [
    re.compile(r"\bground(?:s|ed)?\b"),
    re.compile(r"\bground ?ball\b"),
    re.compile(r"\bgrounder\b"),
    re.compile(r"\bchopper\b"),
    re.compile(r"\bbouncer\b"),
    re.compile(r"\bdribbler\b"),
    re.compile(r"\broller\b"),
    re.compile(r"\btapper\b"),
    re.compile(r"\bslow[- ]roller\b"),
]
LD_REGEX = [
    re.compile(r"\bline drive\b"),
    re.compile(r"\blines?\b"),
    re.compile(r"\blined\b"),
    re.compile(r"\bon a line\b"),
]
FB_REGEX = [
    re.compile(r"\bfly(?:\s?ball)?\b"),
    re.compile(r"\bflies\b"),
    re.compile(r"\bflied\b"),
    re.compile(r"\bpops?\b"),
    re.compile(r"\bpop[- ]?up\b"),
    re.compile(r"\bpopup\b"),
    re.compile(r"\btowering fly\b"),
    re.compile(r"\bhigh fly\b"),
    re.compile(r"\bdeep fly\b"),
    re.compile(r"\bshallow fly\b"),
    re.compile(r"\binfield fly\b"),
    re.compile(r"\bfoul pop\b"),
    re.compile(r"\bblooper\b"),
    re.compile(r"\bflare\b"),
    re.compile(r"\bfloater\b"),
    re.compile(r"\blofted\b"),
]
SACFLY_REGEX = [re.compile(r"\bsac(?:rifice)? fly\b")]

LF_PATTERNS = [
    "left fielder ", "to left fielder", "to left field", "to left", "into left field",
    "down the left field line", "down the left-field line",
    "down the lf line", "down the left line", "toward left field",
    "into shallow left", "into deep left", "into left-center", "into left center",
    "in front of left fielder"
]
CF_PATTERNS = [
    "center fielder ", "to center fielder", "to center field", "to center", "into center field",
    "into deep center", "into shallow center",
    "into left-center field", "into left center field",
    "into right-center field", "into right center field",
    "up the middle into center", "up the middle to center"
]
RF_PATTERNS = [
    "right fielder ", "to right fielder", "to right field", "to right", "into right field",
    "down the right field line", "down the right-field line",
    "down the rf line", "toward right field",
    "into shallow right", "into deep right",
    "into right-center", "into right center",
    "in front of right fielder"
]
SS_PATTERNS = [
    "shortstop ", "to shortstop", "to the shortstop", "to ss",
    "fielded by the shortstop", "fielded by shortstop",
    "shortstop fields", "shortstop to", "shortstop throws to",
    "shortstop makes the play", "at shortstop"
]
_2B_PATTERNS = [
    "second baseman ", "to second baseman", "to the second baseman", "to 2nd baseman",
    "fielded by second baseman", "fielded by the second baseman",
    "second baseman fields", "second baseman to", "second baseman throws to"
]
_3B_PATTERNS = [
    "third baseman ", "to third baseman", "to the third baseman", "to 3rd baseman",
    "fielded by third baseman", "fielded by the third baseman",
    "third baseman fields", "third baseman to", "third baseman throws to"
]
_1B_PATTERNS = [
    "first baseman ", "to first baseman", "to the first baseman", "to 1st baseman",
    "fielded by first baseman", "fielded by the first baseman",
    "first baseman fields", "first baseman to", "first baseman throws to"
]
P_PATTERNS = [
    "to pitcher", "to the pitcher", "back to the pitcher",
    "back to pitcher", "back to the mound",
    "fielded by pitcher", "fielded by the pitcher",
    "pitcher fields", "pitcher to", "pitcher throws to",
    "back up the middle to the pitcher"
]
LEFT_SIDE_PATTERNS = [
    "through the left side", "up the left side", "toward the left side",
    "between shortstop and third baseman", "between 3rd and ss",
    "between ss and 3b", "between short and third"
]
RIGHT_SIDE_PATTERNS = [
    "through the right side", "up the right side", "toward the right side",
    "between first baseman and second baseman", "between 1st and 2nd",
    "between second and first"
]

# -----------------------------
# RUNNING EVENTS (SB / CS / DI) — PICKOFFS REMOVED + FIXED
# -----------------------------
SB_ACTION_REGEX = re.compile(
    r"""
    \b(?:steals?|stole|stolen\s+base)\b
    (?:\s+(?:a|an))?
    (?:\s+base)?
    (?:\s+(?:at|to))?
    \s*(\(?\s*(?:2nd|3rd|home|second|third)\s*\)?)
    """,
    re.IGNORECASE | re.VERBOSE
)

CS_ACTION_REGEX = re.compile(
    r"""
    \b(?:caught\s+stealing|out\s+stealing)\b
    (?:\s+(?:at|trying\s+for|attempting|to))?
    (?:\s+base)?
    (?:\s*(\(?\s*(?:2nd|3rd|home|second|third)\s*\)?))?
    """,
    re.IGNORECASE | re.VERBOSE
)

DI_REGEX_1 = re.compile(
    r"""
    \bdefensive\s+indifference\b
    .*?
    \b(?:to|advances?\s+to|takes)\b
    (?:\s+base)?
    \s*(\(?\s*(?:2nd|3rd|home|second|third)\s*\)?)
    """,
    re.IGNORECASE | re.VERBOSE
)

DI_REGEX_2 = re.compile(
    r"""
    \b(?:to|advances?\s+to|takes)\b
    (?:\s+base)?
    \s*(\(?\s*(?:2nd|3rd|home|second|third)\s*\)?)
    .*?
    \bdefensive\s+indifference\b
    """,
    re.IGNORECASE | re.VERBOSE
)

DI_REGEX_BARE = re.compile(r"\bdefensive\s+indifference\b", re.IGNORECASE)
PAREN_NAME_REGEX = re.compile(r"\(([^)]+)\)")


def normalize_base_bucket(prefix: str, base_raw: Optional[str]) -> str:
    if not base_raw:
        return prefix
    b = base_raw.strip().lower().strip("()").strip()
    if b in ["2nd", "second"]:
        return f"{prefix}-2B"
    if b in ["3rd", "third"]:
        return f"{prefix}-3B"
    if b == "home":
        return f"{prefix}-H"
    return prefix


BAD_FIRST_TOKENS = {
    "top", "bottom", "inning", "pitch", "ball", "strike", "foul",
    "runner", "runners", "advances", "advance", "steals", "stole", "caught",
    "substitution", "defensive", "offensive", "double", "triple", "single", "home",
    "out", "safe", "error", "no", "one", "two", "three",
}


def starts_like_name(token: str) -> bool:
    if not token:
        return False
    t = token.strip().strip('"').strip().lower()
    return t[:1].isalpha() and t not in BAD_FIRST_TOKENS


def overall_confidence_score(conf_val: int):
    if conf_val >= 4:
        return "high"
    if conf_val >= 2:
        return "medium"
    return "low"


def get_batter_name(line: str, roster: set[str]):
    line = (line or "").strip().strip('"')
    if not line:
        return None

    line = re.sub(r"\([^)]*\)", "", line)
    line = re.sub(r"\s+", " ", line).strip()

    parts = line.split()
    if not parts:
        return None

    if not starts_like_name(parts[0]):
        return None

    if len(parts) >= 2:
        candidate_two = parts[0] + " " + parts[1]
        if candidate_two in roster:
            return candidate_two

    last = parts[0]
    last_matches = [p for p in roster if p.split() and p.split()[-1] == last]
    if len(last_matches) == 1:
        return last_matches[0]

    return None


def infer_our_team_name_from_pbp(lines, roster):
    """Infer OUR team name as it appears in half-inning headers by finding a header followed by a roster batter."""
    current_batting = None
    for raw in lines:
        s = (raw or "").strip().strip('"')
        s = re.sub(r"\([^)]*\)", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        maybe = parse_batting_team_from_half(s)
        if maybe:
            current_batting = maybe
            continue
        if current_batting:
            b = get_batter_name(s, roster)
            if b:
                return current_batting
    return None


def extract_runner_name_near_event(clean_line: str, match_start: int, roster: set[str]) -> Optional[str]:
    left = (clean_line[:match_start] or "").strip()
    if not left:
        return None

    chunk = left.split(",")[-1].strip()

    runner = get_batter_name(chunk, roster)
    if runner:
        return runner

    parts = chunk.split()
    if len(parts) >= 2:
        candidate = parts[-2] + " " + parts[-1]
        if candidate in roster:
            return candidate

    return None


def extract_runner_name_fallback(clean_line: str, roster: set[str]) -> Optional[str]:
    runner = get_batter_name(clean_line, roster)
    if runner:
        return runner

    pm = PAREN_NAME_REGEX.search(clean_line)
    if pm:
        inside = re.sub(r"\s+", " ", pm.group(1).strip())
        runner = get_batter_name(inside, roster)
        if runner:
            return runner

    return None


def parse_running_event(clean_line: str, roster: set[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (runner_name, total_key, base_key) or (None, None, None).
    PICKOFFS REMOVED.
    """
    m = SB_ACTION_REGEX.search(clean_line)
    if m:
        base_key = normalize_base_bucket("SB", m.group(1) if (m.lastindex or 0) >= 1 else None)
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "SB", base_key

    m = CS_ACTION_REGEX.search(clean_line)
    if m:
        base_raw = m.group(1) if (m.lastindex or 0) >= 1 else None
        base_key = normalize_base_bucket("CS", base_raw)
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "CS", base_key

    m = DI_REGEX_1.search(clean_line) or DI_REGEX_2.search(clean_line)
    if m:
        base_key = normalize_base_bucket("DI", m.group(1) if (m.lastindex or 0) >= 1 else None)
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "DI", base_key

    if DI_REGEX_BARE.search(clean_line):
        runner = extract_runner_name_fallback(clean_line, roster)
        return runner, "DI", "DI"

    return None, None, None


def is_ball_in_play(line_lower: str) -> bool:
    ll = (line_lower or "").strip()
    if not ll:
        return False

    # exclude non-BIP and running events
    if any(kw in ll for kw in [
        "hit by pitch", "hit-by-pitch", "hit batsman",
        "walks", "walked", " base on balls", "intentional walk",
        "strikes out", "strikeout", "called out on strikes",
        "reaches on catcher interference", "catcher's interference",
        "caught stealing", "out stealing",
        "steals", "stole", "stealing",
        "defensive indifference",
        "picked off", "pickoff",
    ]):
        return False

    bip_outcomes = [
        "grounds", "grounded", "ground ball", "groundball", "grounder",
        "singles", "doubles", "triples", "homers", "home run",
        "lines out", "line drive", "lined out", "line out",
        "flies out", "fly ball", "flied out", "fly out",
        "pops out", "pop up", "pop-out", "popup",
        "bloops", "blooper",
        "bunts", "bunt", "sacrifice bunt", "sac bunt", "sacrifice hit",
        "sac fly", "sacrifice fly",
        "reaches on a fielding error", "reaches on a throwing error",
        "reaches on error", "reached on error", "safe on error",
        "reaches on a missed catch error",
        "fielder's choice", "fielders choice",
        "double play", "triple play",
        "out at first", "out at second", "out at third", "out at home",
    ]
    if any(kw in ll for kw in bip_outcomes):
        return True

    # fallback: any explicit fielder/location markers
    fielder_markers = [
        "left fielder", "center fielder", "right fielder",
        "shortstop", "second baseman", "third baseman", "first baseman",
        "to left field", "to center field", "to right field",
        "to shortstop", "to second baseman", "to third baseman", "to first baseman",
        "to pitcher", "back to the mound",
        "down the left", "down the right", "left-center", "right-center"
    ]
    return any(m in ll for m in fielder_markers)


def classify_ball_type(line_lower: str):
    if "bunt" in line_lower:
        return "GB", 3, ["Contains 'bunt' → GB"]

    for rx in SACFLY_REGEX:
        if rx.search(line_lower):
            return "FB", 3, ["Matched sac fly regex → FB"]

    for rx in LD_REGEX:
        if rx.search(line_lower):
            return "FB", 2, ["Matched line drive regex → FB"]

    for rx in GB_REGEX:
        if rx.search(line_lower):
            return "GB", 2, [f"Matched GB regex: {rx.pattern}"]

    for rx in FB_REGEX:
        if rx.search(line_lower):
            return "FB", 2, [f"Matched FB regex: {rx.pattern}"]

    return None, 0, []


def classify_location(line_lower: str, strict_mode: bool = False):
    if "sacrifice bunt" in line_lower or "sac bunt" in line_lower or "sacrifice hit" in line_lower:
        return "Sac Bunt", 3, ["Contains 'sacrifice bunt/sac bunt' → Sac Bunt"]

    if "bunt" in line_lower:
        return "Bunt", 3, ["Contains 'bunt' → Bunt"]

    candidates = []

    def add_candidates(patterns, code, label):
        for kw in patterns:
            idx = line_lower.find(kw)
            if idx != -1:
                candidates.append((idx, code, f"Matched {label} phrase: '{kw}'"))

    add_candidates(LF_PATTERNS, "LF", "LF")
    add_candidates(CF_PATTERNS, "CF", "CF")
    add_candidates(RF_PATTERNS, "RF", "RF")
    add_candidates(SS_PATTERNS, "SS", "SS")
    add_candidates(_3B_PATTERNS, "3B", "3B")
    add_candidates(_2B_PATTERNS, "2B", "2B")
    add_candidates(_1B_PATTERNS, "1B", "1B")
    add_candidates(P_PATTERNS, "P", "P")

    if candidates:
        _, loc, reason = min(candidates, key=lambda x: x[0])
        return loc, 3, [reason]

    if strict_mode:
        return None, 0, ["Strict mode: no explicit fielder/location phrase found"]

    for kw in LEFT_SIDE_PATTERNS:
        if kw in line_lower:
            return "SS", 1, [f"Matched left-side phrase: '{kw}' → approximate SS"]

    for kw in RIGHT_SIDE_PATTERNS:
        if kw in line_lower:
            return "2B", 1, [f"Matched right-side phrase: '{kw}' → approximate 2B"]

    return None, 0, []


# -----------------------------
# UNLIMITED TEAMS: read roster files
# -----------------------------
def list_team_files():
    files = []
    for fn in os.listdir(TEAM_ROSTERS_DIR):
        if fn.lower().endswith(".txt"):
            files.append(fn)
    files.sort(key=lambda x: x.lower())
    return files


def team_name_from_file(filename: str) -> str:
    return os.path.splitext(filename)[0]


def safe_team_key(team_name: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9]+", "_", team_name.strip()).strip("_").lower()
    return key or "team"


def roster_path_for_file(filename: str) -> str:
    return os.path.join(TEAM_ROSTERS_DIR, filename)


def load_roster_text(path: str) -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def save_roster_text(path: str, text: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n" if text.strip() else "")


def add_game_to_season(season_team, season_players, game_team, game_players):
    for key in LOCATION_KEYS + BALLTYPE_KEYS + COMBO_KEYS + RUN_KEYS:
        season_team[key] = season_team.get(key, 0) + game_team.get(key, 0)

    for player, gstats in game_players.items():
        season_players.setdefault(player, empty_stat_dict())
        sstats = season_players[player]
        for key in LOCATION_KEYS + BALLTYPE_KEYS + COMBO_KEYS + RUN_KEYS:
            sstats[key] = sstats.get(key, 0) + gstats.get(key, 0)


# -----------------------------
# SUPABASE (persistent storage)
# -----------------------------
SUPABASE_SETUP_SQL = """
-- ============================
-- SEASON TOTALS (one row per team_code + team_key)
-- ============================
create table if not exists public.season_totals (
  id bigserial primary key,
  team_code text not null,
  team_key  text not null,
  data jsonb not null default '{}'::jsonb,
  games_played integer not null default 0,
  updated_at timestamptz not null default now()
);

create unique index if not exists season_totals_unique
  on public.season_totals (team_code, team_key);

create index if not exists season_totals_team_idx
  on public.season_totals (team_code, team_key);
  -- ============================
-- TEAM ROSTERS (persistent, per team_code)
-- ============================
create table if not exists public.team_rosters (
  id bigserial primary key,
  team_code   text not null,
  team_key    text not null,
  team_name   text not null,
  roster_text text not null default '',
  updated_at  timestamptz not null default now()
);

create unique index if not exists team_rosters_unique
  on public.team_rosters (team_code, team_key);

create index if not exists team_rosters_team_idx
  on public.team_rosters (team_code, team_name);


-- ============================
-- PROCESSED GAMES (hard dedupe)
-- ============================
create table if not exists public.processed_games (
  id bigserial primary key,
  team_code text not null,
  team_key  text not null,
  game_hash text not null,
  created_at timestamptz not null default now()
);

create unique index if not exists processed_games_unique
  on public.processed_games (team_code, team_key, game_hash);

create index if not exists processed_games_team_idx
  on public.processed_games (team_code, team_key);
""".strip()


def _show_db_error(e: Exception, label: str):
    st.error(f"**{label}**")
    try:
        parts = [f"type: {type(e)}"]
        for attr in ("message", "details", "hint", "code"):
            if hasattr(e, attr):
                val = getattr(e, attr)
                if val:
                    parts.append(f"{attr}: {val}")
        st.code("\n".join(parts), language="text")
    except Exception:
        st.write(str(e))


def _render_supabase_fix_block():
    st.error("Supabase tables are missing or mismatched (season_totals / processed_games).")
    ("### Fix (copy/paste into Supabase → SQL Editor → Run)")
    st.code(SUPABASE_SETUP_SQL, language="sql")
    (
        """
**Then refresh your Streamlit app.**  
If it still errors after running the SQL, your Streamlit **secrets** are wrong.
"""
    )


@st.cache_resource(show_spinner=False)
def get_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL", "").strip()
    key = st.secrets.get("SUPABASE_SERVICE_KEY", "").strip()  # service role key (server-side only)
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in Streamlit secrets.")
    return create_client(url, key)


try:
    supabase = get_supabase()
except Exception as e:
    _show_db_error(e, "Supabase secrets missing / invalid")
    st.stop()


def supa_execute_with_retry(builder, tries: int = 5):
    last_err = None
    for i in range(tries):
        try:
            return builder.execute()
        except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_err = e
            time.sleep(0.6 * (i + 1))
    raise last_err


def supabase_health_check_or_stop():
    try:
        supa_execute_with_retry(supabase.table("season_totals").select("id").limit(1))
        supa_execute_with_retry(supabase.table("processed_games").select("id").limit(1))
        supa_execute_with_retry(supabase.table("team_rosters").select("team_code").limit(1))


        return True
    except Exception as e:
        _show_db_error(e, "Supabase not ready")
        _render_supabase_fix_block()
        st.stop()


supabase_health_check_or_stop()


def db_load_season_totals(team_code: str, team_key: str, current_roster: set[str]):
    """
    Returns (season_team, season_players, games_played, processed_hashes_set, archived_players_set, season_pitching_dict)
    archived_players are players who exist in DB totals but are not on the current roster (or were removed).
    """
    season_team = empty_stat_dict()
    season_players = {p: empty_stat_dict() for p in current_roster}
    games_played = 0
    archived_players = set()

    season_pitching = {}
    try:
        res = (
            supabase.table("season_totals")
            .select("data, games_played")
            .eq("team_code", team_code)
            .eq("team_key", team_key)
            .limit(1)
            .execute()
        )
    except Exception as e:
        _show_db_error(e, "Supabase SELECT failed on season_totals")
        _render_supabase_fix_block()
        st.stop()

    if res.data:
        row = res.data[0]
        payload = row.get("data") or {}

        raw_team = payload.get("team") or {}
        raw_players = payload.get("players") or {}
        raw_meta = payload.get("meta") or {}


        raw_pitching = payload.get("pitching") or {}
        season_team = ensure_all_keys(raw_team if isinstance(raw_team, dict) else {})
        season_players = {}

        if isinstance(raw_players, dict):
            for p, stats in raw_players.items():
                season_players[p] = ensure_all_keys(stats) if isinstance(stats, dict) else empty_stat_dict()

        # Ensure current roster always exists in dict (so new guys show up immediately)
        for p in current_roster:
            if p not in season_players:
                season_players[p] = empty_stat_dict()


        # Pitching totals (optional, backward compatible)
        season_pitching = {}
        if isinstance(raw_pitching, dict):
            for pn, pst in raw_pitching.items():
                if isinstance(pst, dict):
                    season_pitching[str(pn).strip().strip('\"')] = ensure_pitching_keys(pst)
                else:
                    season_pitching[str(pn).strip().strip('\"')] = empty_pitching_stat()

        games_played = int(row.get("games_played") or 0)

        # Archived list stored in meta (optional)
        ap = raw_meta.get("archived_players", []) if isinstance(raw_meta, dict) else []
        if isinstance(ap, list):
            archived_players = {str(x).strip().strip('"') for x in ap if str(x).strip()}

    try:
        pres = (
            supabase.table("processed_games")
            .select("game_hash")
            .eq("team_code", team_code)
            .eq("team_key", team_key)
            .execute()
        )
    except Exception as e:
        _show_db_error(e, "Supabase SELECT failed on processed_games")
        _render_supabase_fix_block()
        st.stop()

    processed_set = set()
    if pres.data:
        processed_set = {r["game_hash"] for r in pres.data if r.get("game_hash")}

    return season_team, season_players, games_played, processed_set, archived_players, season_pitching


def db_save_season_totals(
    team_code: str,
    team_key: str,
    season_team: dict,
    season_players: dict,
    games_played: int,
    archived_players: set[str] | list[str] | None = None,
    season_pitching: dict | None = None,
):
    archived_list = []
    if archived_players:
        archived_list = sorted({str(x).strip().strip('"') for x in archived_players if str(x).strip()})

    payload = {
        "team": season_team,
        "players": season_players,
        "pitching": season_pitching or {},
        "meta": {"archived_players": archived_list},
    }

    try:
        (
            supabase.table("season_totals")
            .upsert(
                {
                    "team_code": team_code,
                    "team_key": team_key,
                    "data": payload,
                    "games_played": int(games_played),
                    "updated_at": datetime.utcnow().isoformat(),
                },
                on_conflict="team_code,team_key",
            )
            .execute()
        )
    except Exception as e:
        _show_db_error(e, "Supabase UPSERT failed on season_totals")
        _render_supabase_fix_block()
        st.stop()

        
def db_try_mark_game_processed(team_code: str, team_key: str, game_hash: str) -> bool:
    try:
        supabase.table("processed_games").insert(
            {"team_code": team_code, "team_key": team_key, "game_hash": game_hash}
        ).execute()
        return True
    except Exception:
        return False


def db_unmark_game_processed(team_code: str, team_key: str, game_hash: str):
    try:
        supa_execute_with_retry(
            supabase.table("processed_games")
            .delete()
            .eq("team_code", team_code)
            .eq("team_key", team_key)
            .eq("game_hash", game_hash)
        )
    except Exception:
        pass
        
def db_reset_season(team_code: str, team_key: str):
    try:
        supabase.table("season_totals").delete().eq("team_code", team_code).eq("team_key", team_key).execute()
        supabase.table("processed_games").delete().eq("team_code", team_code).eq("team_key", team_key).execute()
    except Exception as e:
        _show_db_error(e, "Supabase RESET failed")
        _render_supabase_fix_block()
        st.stop()

      # -----------------------------
# TEAM ROSTERS (SUPABASE - PERSISTENT)
# -----------------------------
def db_list_teams(team_code: str):
    """
    Returns list of dicts: [{team_key, team_name, roster_text, updated_at}]
    """
    try:
        res = (
            supabase.table("team_rosters")
            .select("team_key, team_name, roster_text, updated_at")
            .eq("team_code", team_code)
            .order("team_name")
            .execute()
        )
        return res.data or []
    except Exception as e:
        _show_db_error(e, "Supabase SELECT failed on team_rosters")
        _render_supabase_fix_block()
        st.stop()


def db_get_team(team_code: str, team_key: str):
    try:
        res = (
            supabase.table("team_rosters")
            .select("team_key, team_name, roster_text, updated_at")
            .eq("team_code", team_code)
            .eq("team_key", team_key)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
        return None
    except Exception as e:
        _show_db_error(e, "Supabase SELECT failed on team_rosters (single)")
        _render_supabase_fix_block()
        st.stop()


def db_get_roster(team_code: str, team_key: str) -> str:
    """
    Returns roster_text for a team, or empty string if none exists.
    """
    team = db_get_team(team_code, team_key)
    if team and team.get("roster_text"):
        return team["roster_text"]
    return ""


def db_upsert_team(team_code: str, team_key: str, team_name: str, roster_text: str):
    """
    Upserts one roster row per (team_code, team_key).
    Requires unique(team_code, team_key) on team_rosters.
    """
    try:
        (
            supabase.table("team_rosters")
            .upsert(
                {
                    "team_code": team_code,
                    "team_key": team_key,
                    "team_name": team_name,
                    "roster_text": roster_text or "",
                    "updated_at": datetime.utcnow().isoformat(),
                },
                on_conflict="team_code,team_key",
            )
            .execute()
        )
    except Exception as e:
        _show_db_error(e, "Supabase UPSERT failed on team_rosters")
        _render_supabase_fix_block()
        st.stop()


def db_delete_team(team_code: str, team_key: str):
    """
    Optional: delete a team roster row.
    (Season totals are separate; you can reset season with your reset button.)
    """
    try:
        supabase.table("team_rosters").delete().eq("team_code", team_code).eq("team_key", team_key).execute()
    except Exception as e:
        _show_db_error(e, "Supabase DELETE failed on team_rosters")
        _render_supabase_fix_block()
        st.stop()
  

# -----------------------------
# BRANDING + BACKGROUND
# -----------------------------
def get_base64_image(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


PRIMARY = SETTINGS.get("primary_color", "#b91c1c")
SECONDARY = SETTINGS.get("secondary_color", "#111111")

LOGO_PATH = (
    TEAM_CFG.get("logo_path")
    or SETTINGS.get("logo_image")
    or os.path.join("assets", "logo.png")
)

BG_PATH = (
    TEAM_CFG.get("background_path")
    or SETTINGS.get("background_image")
    or os.path.join("assets", "background.jpg")
)

if TEAM_CFG:
    LOGO_PATH = TEAM_CFG.get("logo_path", LOGO_PATH)
    BG_PATH = TEAM_CFG.get("background_path", BG_PATH)

BG_B64 = get_base64_image(BG_PATH)


# -----------------------------
# FONTS (force load)
# -----------------------------
st.markdown(
    """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Black+Ops+One&family=Jersey+10&display=swap" rel="stylesheet">
""",
    unsafe_allow_html=True,
)

# -----------------------------
# STYLES (sharp + bordered title)
# -----------------------------
st.markdown(
    f"""
<style>
h1.app-title {{
    font-family: 'Black Ops One', 'Jersey 10', sans-serif !important;
    font-size: 6.0rem !important;
    color: {PRIMARY} !important;
    text-align: center !important;
    letter-spacing: 0.20em !important;
    text-transform: uppercase !important;
    -webkit-text-stroke: 2.5px #000000;
    text-shadow:
        2px 2px 0 #000000,
        -2px 2px 0 #000000,
        2px -2px 0 #000000,
        -2px -2px 0 #000000;
    margin-top: -10px !important;
    margin-bottom: 12px !important;
}}

.app-subtitle {{
    font-size: 1.10rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.05em !important;
    color: #111827 !important;
    opacity: 0.97 !important;
    text-align: center !important;
    margin-bottom: 18px !important;
}}

[data-testid="stAppViewContainer"] {{
    background:
        linear-gradient(rgba(229,231,235,0.90), rgba(229,231,235,0.90)),
        url("data:image/jpeg;base64,{BG_B64}") no-repeat center fixed;
    background-size: 600px;
    color: #111827;
}}

.spray-card {{
    padding: 12px 14px;
    border-radius: 12px;
    border: 1px solid rgba(17,24,39,0.15);
    background: rgba(255,255,255,0.75);
}}

/* Make expander label bold */
[data-testid="stExpander"] summary {{
    font-weight: 800 !important;
}}
</style>
""",
    unsafe_allow_html=True,
)



# -----------------------------
# HEADER
# -----------------------------
st.markdown(f"<h1 class='app-title'>{SETTINGS.get('app_title','RP Spray Analytics')}</h1>", unsafe_allow_html=True)
st.markdown(f"<div class='app-subtitle'>{SETTINGS.get('subtitle','')}</div>", unsafe_allow_html=True)
st.markdown("---")

# -----------------------------
# SIDEBAR
# -----------------------------

# -----------------------------
# HALL OF FAME QUOTES (SIDEBAR)
# -----------------------------
HOF_QUOTES = [
    ("Hank Aaron", "Failure is a part of success."),
    ("Yogi Berra", "Baseball is 90% mental. The other half is physical."),
    ("Babe Ruth", "Never let the fear of striking out get in your way."),
    ("Ted Williams", "Hitting is timing. Pitching is upsetting timing."),
    ("Willie Mays", "It isn’t difficult to be great from time to time. What’s difficult is to be great all the time."),
    ("Cal Ripken Jr.", "Success is a process. You have to commit to the process."),
    ("Sandy Koufax", "Pitching is the art of instilling fear."),
    ("Nolan Ryan", "Enjoying success requires the ability to adapt."),
    ("Lou Gehrig", "It’s the ballplayer’s job to always be ready to play."),
    ("Jackie Robinson", "A life is not important except in the impact it has on other lives."),
]

def get_daily_quote(quotes):
    idx = int(datetime.utcnow().strftime("%Y%m%d")) % len(quotes)
    return quotes[idx]


with st.sidebar:
    # Logo
    if LOGO_PATH and os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=260)

    # Tags
    st.markdown("### ⚾ Spray Lab")
    st.markdown(
        """
<span class="badge">Unlimited Teams</span>
<span class="badge">GameChanger</span>
<span class="badge">First Contact</span>
<span class="badge">GB / FB</span>
""",
        unsafe_allow_html=True,
    )

    # Strict mode
    strict_mode = st.checkbox(
        "STRICT MODE (only count plays with explicit fielder/location)",
        value=bool(SETTINGS.get("strict_mode_default", True)),
    )

    st.markdown("---")

    # Daily quote card
    who, quote = get_daily_quote(HOF_QUOTES)
    st.markdown(
        f"""
        <div style="
            padding: 14px;
            border-radius: 14px;
            background: rgba(255,255,255,0.72);
            border: 1px solid rgba(0,0,0,0.10);
            box-shadow: 0 6px 18px rgba(0,0,0,0.06);
        ">
            <div style="font-size: 0.95rem; font-weight: 800; margin-bottom: 8px;">
                🏆 Hall of Fame Quote
            </div>
            <div style="font-size: 0.98rem; font-weight: 700; line-height: 1.35;">
                “{quote}”
            </div>
            <div style="margin-top: 10px; font-size: 0.90rem; font-weight: 800; opacity: 0.85;">
                — {who}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # -----------------------------
    # ADMIN: CHANGE ACCESS CODE (CLEAN + HIDDEN)
    # -----------------------------
    with st.expander("🔐 Admin", expanded=False):
        st.markdown(
            """
            <div style="
                padding: 12px;
                border-radius: 14px;
                background: rgba(255,255,255,0.72);
                border: 1px solid rgba(0,0,0,0.10);
                box-shadow: 0 6px 18px rgba(0,0,0,0.06);
                margin-bottom: 10px;
            ">
                <div style="font-size:0.92rem; font-weight:800; margin-bottom:6px;">
                    Change Access Code
                </div>
                <div style="font-size:0.85rem; opacity:0.85;">
                    Updates Supabase instantly.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        pin = st.text_input(
            "Admin PIN",
            type="password",
            label_visibility="collapsed",
            placeholder="Admin PIN",
        )

        if pin != st.secrets.get("ADMIN_PIN", ""):
            st.caption("Admin access only.")
        else:
            codes_map = load_team_codes()
            teams = sorted({
                (v.get("team_code") or "").strip().upper()
                for v in (codes_map.values() if isinstance(codes_map, dict) else [])
                if v and v.get("team_code")
            })

            if not teams:
                st.error("No active teams found in team_access.")
            else:
                team_pick = st.selectbox("Team", options=teams)

                new_code = st.text_input("New Code", type="password", placeholder="New access code")
                confirm  = st.text_input("Confirm", type="password", placeholder="Confirm new access code")

                c1, c2 = st.columns(2)
                with c1:
                    update_btn = st.button("Update", use_container_width=True)
                with c2:
                    clear_btn = st.button("Clear", use_container_width=True)

                if clear_btn:
                    st.rerun()

                if update_btn:
                    if not new_code.strip():
                        st.error("Enter a new code.")
                    elif new_code != confirm:
                        st.error("Codes don’t match.")
                    else:
                        ok = admin_set_access_code(team_pick, new_code)
                        if ok:
                            st.success("✅ Access code updated.")
                            load_team_codes.clear()  # clear cached codes
                            st.rerun()
                        else:
                            st.error("Update failed. Team not found in team_access.")




   
# -----------------------------
# TEAM SELECTION (SUPABASE - PERSISTENT)
# -----------------------------

teams = db_list_teams(TEAM_CODE_SAFE)

if not teams:
    st.warning("No teams found yet for THIS access code. Create one below.")
else:
    team_names = [t.get("team_name", "Unnamed Team") for t in teams]
    selected_team = st.selectbox("Choose a team:", team_names)

    selected_row = next((t for t in teams if t.get("team_name") == selected_team), teams[0])
    team_key = selected_row.get("team_key") or safe_team_key(selected_team)

with st.expander("➕ Add a new team (stored in Supabase)"):
    new_team_name = st.text_input("New team name:")
    if st.button("Create Team"):
        if not new_team_name.strip():
            st.error("Enter a team name first.")
        else:
            new_key = safe_team_key(new_team_name)
            db_upsert_team(TEAM_CODE_SAFE, new_key, new_team_name.strip(), "")
            st.success("Team created. Reloading…")
            st.rerun()

if not teams:
    st.stop()

st.markdown("---")

# -----------------------------
# ROSTER UI (SUPABASE - PERSISTENT)
# -----------------------------
st.subheader(f"📝 {selected_team} Roster (Hitters)")

default_roster_text = db_get_roster(TEAM_CODE_SAFE, team_key)

roster_text = st.text_area(
    "One player per line EXACTLY like GameChanger shows them (e.g., 'J Smith')",
    value=default_roster_text,
    height=220,
)

col_a, _ = st.columns([1, 3])
with col_a:
    if st.button("💾 Save Roster"):
        # Build the NEW roster from the text box (this is what coach just edited)
        new_roster = {ln.strip().strip('"') for ln in (roster_text or "").split("\n") if ln.strip()}

        # Save roster text
        db_upsert_team(TEAM_CODE_SAFE, team_key, selected_team, roster_text)

        # Reload season from DB (source of truth) – includes archived_players
        season_team, season_players, games_played, processed_set, archived_players, season_pitching = db_load_season_totals(
            TEAM_CODE_SAFE, team_key, new_roster
        )

        # Archive anyone removed from roster (but KEEP their stats)
        removed = set(season_players.keys()) - set(new_roster)
        archived_players = set(archived_players or set())
        archived_players.update(removed)

        # Unarchive anyone re-added
        archived_players = {p for p in archived_players if p not in new_roster}

        # Ensure new roster players exist in season_players
        for p in new_roster:
            season_players.setdefault(p, empty_stat_dict())

        # Save back with updated archived list
        db_save_season_totals(
            TEAM_CODE_SAFE, team_key, season_team, season_players, games_played, archived_players, season_pitching
        )

        st.success("Roster saved + removed players archived (reports will match roster).")
        st.rerun()

current_roster = {line.strip().strip('"') for line in roster_text.split("\n") if line.strip()}
st.write(f"**Hitters loaded:** {len(current_roster)}")


# ✅ LOAD FROM SUPABASE ONLY (source of truth) — includes archived_players
season_team, season_players, games_played, processed_set, archived_players, season_pitching = db_load_season_totals(
    TEAM_CODE_SAFE, team_key, current_roster
)

st.markdown(
    f"""
<div class="spray-card">
    <strong>Active team:</strong> {selected_team}<br>
    <strong>Games processed:</strong> {games_played}<br>
</div>
""",
    unsafe_allow_html=True,
)

# --- Reset button (Power Red, dynamic team name) ---
ACTIVE_TEAM_NAME = TEAM_CFG.get("team_name", TEAM_CODE)
reset_label = f"Reset SEASON totals"

st.markdown(
    """
    <style>
    button[aria-label^="Reset SEASON totals —"]{
        background-color:#b91c1c !important; /* Power Red */
        color:#ffffff !important;
        border:0 !important;
        font-weight:700 !important;
    }
    button[aria-label^="Reset SEASON totals —"]:hover{
        background-color:#991b1b !important;
        color:#ffffff !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

col_reset, _ = st.columns([1, 3])

with col_reset:
    if st.button("Reset Season Totals", key="reset_season", type="primary"):
        # Supabase is the source of truth now
        db_reset_season(TEAM_CODE_SAFE, team_key)

        season_team, season_players, games_played, processed_set, archived_players, season_pitching = db_load_season_totals(
            TEAM_CODE_SAFE, team_key, current_roster
        )

        st.rerun()


# -----------------------------
# ✅ COACH-PROOF BACKUP / RESTORE (SUPABASE)
# -----------------------------
with st.expander("🛟 Backup / Restore (Coach-Proof) — Download + Upload Season Totals"):
    raw_payload = {
        "meta": {"games_played": games_played},
        "team": season_team,
        "players": season_players,
        "pitching": season_pitching,
        "archived_players": sorted(list(archived_players or set())),
    }

    backup_bytes = json.dumps(raw_payload, indent=2).encode("utf-8")
    safe_team = re.sub(r"[^A-Za-z0-9_-]+", "_", selected_team).strip("_")

    st.download_button(
        label="⬇️ Download Season Totals JSON (backup)",
        data=backup_bytes,
        file_name=f"{TEAM_CODE}_{safe_team}_season_totals_backup.json",
        mime="application/json",
    )

    st.markdown("**Restore from a backup JSON:** (This overwrites the current season totals for this selected team.)")

    uploaded = st.file_uploader(
        "Upload backup JSON",
        type=["json"],
        accept_multiple_files=False,
        help="Choose a season_totals_backup.json file you downloaded earlier.",
    )

    do_restore = st.button("♻️ Restore Backup NOW")
    if do_restore:
        if uploaded is None:
            st.error("Upload a backup JSON first.")
            st.stop()

        try:
            incoming = json.load(uploaded)
            if not isinstance(incoming, dict):
                raise ValueError("Backup JSON is not an object.")

            incoming_team = incoming.get("team", {})
            incoming_players = incoming.get("players", {})
            incoming_pitching = incoming.get("pitching", {})
            incoming_meta = incoming.get("meta", {})
            incoming_archived = incoming.get("archived_players", [])

            if not isinstance(incoming_team, dict) or not isinstance(incoming_players, dict) or not isinstance(incoming_meta, dict):
                raise ValueError("Backup JSON is missing required sections: meta/team/players.")

            incoming_team = ensure_all_keys(incoming_team)
            fixed_players = {}
            for p, sd in incoming_players.items():
                fixed_players[p] = ensure_all_keys(sd) if isinstance(sd, dict) else empty_stat_dict()

            for p in current_roster:
                if p not in fixed_players:
                    fixed_players[p] = empty_stat_dict()

            # Pitching (optional)
            fixed_pitching = {}
            if isinstance(incoming_pitching, dict):
                for pn, pst in incoming_pitching.items():
                    fixed_pitching[str(pn).strip().strip('"')] = ensure_pitching_keys(pst) if isinstance(pst, dict) else empty_pitching_stat()
            incoming_pitching = fixed_pitching

            restored_games_played = int(incoming_meta.get("games_played", 0) or 0)

            # Archived players set
            restored_archived = set()
            if isinstance(incoming_archived, list):
                restored_archived = {str(x).strip().strip('"') for x in incoming_archived if str(x).strip()}

            legacy_processed = incoming_team.get("_processed_game_keys", [])
            legacy_hashes = []
            if isinstance(legacy_processed, list):
                legacy_hashes = [str(x) for x in legacy_processed if x]
                restored_games_played = len(set(legacy_hashes))

            db_reset_season(TEAM_CODE_SAFE, team_key)
            db_save_season_totals(TEAM_CODE_SAFE, team_key, incoming_team, fixed_players, restored_games_played, restored_archived, incoming_pitching)

            if legacy_hashes:
                for h in set(legacy_hashes):
                    db_try_mark_game_processed(TEAM_CODE_SAFE, team_key, h)

            st.success("✅ Restore complete (Supabase). Reloading…")
            st.rerun()

        except Exception as e:
            st.error(f"Restore failed: {e}")


# -----------------------------
# PLAY-BY-PLAY INPUT
# -----------------------------
st.subheader("📓 GameChanger Play-by-Play")

raw_text = st.text_area(
    f"Paste the full play-by-play for ONE game involving {selected_team}:",
    height=260,
)


# -----------------------------
# PROCESS GAME (SUPABASE DEDUPE + SUPABASE SAVE)
# -----------------------------
if "processing_game" not in st.session_state:
    st.session_state.processing_game = False
if "processing_started_at" not in st.session_state:
    st.session_state.processing_started_at = 0.0

# failsafe unlock after 15 seconds
if st.session_state.processing_game:
    try:
        if (time.time() - float(st.session_state.processing_started_at or 0.0)) > 15:
            st.session_state.processing_game = False
            st.session_state.processing_started_at = 0.0
    except Exception:
        st.session_state.processing_game = False
        st.session_state.processing_started_at = 0.0


st.markdown(
    """
    <style>
    div[data-testid="stButton"] button[data-key="process_game"] {
        background-color: #16a34a !important;  /* bright green */
        color: white !important;
        border: 1px solid #16a34a !important;
        font-weight: 700;
    }
    div[data-testid="stButton"] button[data-key="process_game"]:hover {
        background-color: #15803d !important;
        border-color: #15803d !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    /* Only the button inside this wrapper gets styled */
    #process-wrap button {
        background: #00c853 !important;   /* bright green */
        color: white !important;
        border: 0 !important;
        font-weight: 700 !important;
        border-radius: 10px !important;
        padding: 0.6rem 1rem !important;
    }
    #process-wrap button:hover {
        background: #00b84a !important;
        color: white !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div id="process-wrap">', unsafe_allow_html=True)
process_clicked = st.button("📥 Process Game (ADD to Season Totals)", key="process_game_btn")
st.markdown("</div>", unsafe_allow_html=True)

if process_clicked:

    if st.session_state.processing_game:
        st.warning("Already processing… please wait.")
        st.stop()

    st.session_state.processing_game = True
    st.session_state.processing_started_at = time.time()

    rerun_needed = False
    marked_processed = False
    gkey = None

    try:
        if not raw_text.strip():
            st.error("Paste play-by-play first.")
            st.stop()

        if not current_roster:
            st.error("Roster is empty. Add hitters first (and save).")
            st.stop()

        gkey = game_key_from_pbp(team_key, raw_text)

        if not db_try_mark_game_processed(TEAM_CODE_SAFE, team_key, gkey):
            st.warning("This exact play-by-play has already been processed for this team. Skipping.")
            st.stop()

        processed_set.add(gkey)
        marked_processed = True

        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

        game_team = empty_stat_dict()
        game_players = {p: empty_stat_dict() for p in current_roster}

        # --- Yukon pitching (IP / K / BB / Strike%) ---
        game_pitching = {}
        current_pitcher = "UNKNOWN_P"
        current_batting_team = None
        last_outs_in_half = 0
        team_pbp_name = (TEAM_CFG.get("team_name", "") or "").strip().lower()
        inferred_pbp_team = infer_our_team_name_from_pbp(lines, current_roster)
        if inferred_pbp_team:
            team_pbp_name = inferred_pbp_team.strip().lower()
        if not team_pbp_name:
            team_pbp_name = (selected_team or "").strip().lower()
        pending_outs = 0
        pending_pitches = 0
        pending_strikes = 0

        for line in lines:
            clean_line = line.strip().strip('"')
            clean_line = re.sub(r"\([^)]*\)", "", clean_line)
            clean_line = re.sub(r"\s+", " ", clean_line).strip()
            line_lower = clean_line.lower()

            # --- Track half inning + current pitcher (for Yukon pitching) ---
            maybe_batting = parse_batting_team_from_half(clean_line)
            if maybe_batting:
                current_batting_team = maybe_batting
                last_outs_in_half = 0
                pending_outs = 0
                pending_pitches = 0
                pending_strikes = 0
                # New half-inning: don't carry pitcher forward implicitly
                current_pitcher = "UNKNOWN_P"

            # Defense/offense detection: we only credit Yukon pitching when the OTHER team is batting
            # Defense/offense detection:
            #  - Primary: half-inning header team name (Top/Bottom ... - TEAM)
            #  - Failsafe: if we see one of OUR roster hitters in the line, we are on offense even if header was missed
            _our_batter = get_batter_name(clean_line, current_roster)
            if _our_batter:
                is_team_defense = False
            else:
                is_team_defense = bool(current_batting_team) and (not team_matches_pbp(current_batting_team, team_pbp_name))

            # Only update *our* current pitcher while on defense (prevents opponent pitcher bleed)
            maybe_p = parse_pitcher_from_line(clean_line)
            if is_team_defense and maybe_p:
                current_pitcher = maybe_p

                # If we previously saw outs before GC stated the pitcher, assign them now
                # If we previously saw outs/pitches before GC stated the pitcher, assign them now
                if (current_pitcher and current_pitcher != "UNKNOWN_P") and (pending_outs > 0 or pending_pitches > 0):
                    game_pitching.setdefault(current_pitcher, empty_pitching_stat())
                    if pending_outs > 0:
                        game_pitching[current_pitcher]["OUTS"] += int(pending_outs)
                        pending_outs = 0
                    if pending_pitches > 0:
                        game_pitching[current_pitcher]["PITCHES"] += int(pending_pitches)
                        game_pitching[current_pitcher]["STRIKES"] += int(pending_strikes)
                        pending_pitches = 0
                        pending_strikes = 0

            # Outs by delta (IP accuracy)
            outs_now = parse_outs_marker(clean_line)
            if outs_now is not None:
                delta_outs = max(0, int(outs_now) - int(last_outs_in_half))
                last_outs_in_half = int(outs_now)
                if is_team_defense and delta_outs > 0:
                    # GC often prints '3 Outs' BEFORE the line that contains 'X pitching'.
                    # If we don't know the pitcher yet, hold outs and assign once pitcher appears.
                    if (not current_pitcher) or (current_pitcher == "UNKNOWN_P"):
                        pending_outs += int(delta_outs)
                    else:
                        pname = current_pitcher
                        game_pitching.setdefault(pname, empty_pitching_stat())
                        game_pitching[pname]["OUTS"] += int(delta_outs)

            # If the half-inning ended (3 outs) and we still never saw a pitcher line,
            # bucket any held outs/pitches to UNKNOWN_P for that half (so IP stays correct).
            if is_team_defense and outs_now == 3 and (pending_outs > 0 or pending_pitches > 0) and (current_pitcher == "UNKNOWN_P"):
                game_pitching.setdefault("UNKNOWN_P", empty_pitching_stat())
                if pending_outs > 0:
                    game_pitching["UNKNOWN_P"]["OUTS"] += int(pending_outs)
                    pending_outs = 0
                if pending_pitches > 0:
                    game_pitching["UNKNOWN_P"]["PITCHES"] += int(pending_pitches)
                    game_pitching["UNKNOWN_P"]["STRIKES"] += int(pending_strikes)
                    pending_pitches = 0
                    pending_strikes = 0
            # K / BB from event detail lines (avoid header double-count)
            if is_team_defense:
                pname = current_pitcher or "UNKNOWN_P"

                if is_strikeout_detail(clean_line):
                    game_pitching.setdefault(pname, empty_pitching_stat())
                    game_pitching[pname]["K"] += 1

                if is_walk_detail(clean_line):
                    game_pitching.setdefault(pname, empty_pitching_stat())
                    game_pitching[pname]["BB"] += 1

                # Strike% from visible pitch tokens
                p_ct, s_ct = count_pitch_tokens(clean_line)
                if p_ct > 0:
                    # If pitcher not known yet in this defensive segment, hold pitches and assign once pitcher appears.
                    if (not pname) or (pname == "UNKNOWN_P"):
                        pending_pitches += int(p_ct)
                        pending_strikes += int(s_ct)
                    else:
                        game_pitching.setdefault(pname, empty_pitching_stat())
                        game_pitching[pname]["PITCHES"] += int(p_ct)
                        game_pitching[pname]["STRIKES"] += int(s_ct)

                # HBP: add +1 pitch only if the line doesn't already include visible pitch tokens
                # HBP: add +1 pitch only if the line doesn't already include visible pitch tokens
                if is_hbp_detail(clean_line) and p_ct == 0:
                    if (not pname) or (pname == "UNKNOWN_P"):
                        pending_pitches += 1
                    else:
                        game_pitching.setdefault(pname, empty_pitching_stat())
                        game_pitching[pname]["PITCHES"] += 1

            # running events (not BIP)
            runner, total_key, base_key = parse_running_event(clean_line, current_roster)
            if runner and total_key:
                game_team[total_key] += 1
                game_players[runner][total_key] += 1
                if base_key and base_key in RUN_KEYS:
                    game_team[base_key] += 1
                    game_players[runner][base_key] += 1

            batter = get_batter_name(clean_line, current_roster)
            if batter is None:
                continue
            if not is_ball_in_play(line_lower):
                continue

            loc, loc_conf, loc_reasons = classify_location(line_lower, strict_mode=strict_mode)
            ball_type, bt_conf, bt_reasons = classify_ball_type(line_lower)

            if loc is None:
                if strict_mode:
                    continue
                loc = "UNKNOWN"
                loc_reasons.append("No location match → bucketed as UNKNOWN")

            if ball_type is None and loc is not None:
                if loc in ["SS", "3B", "2B", "1B", "P", "Bunt", "Sac Bunt"]:
                    ball_type = "GB"
                    bt_conf += 1
                    bt_reasons.append("No explicit GB phrase → inferred GB from infield location")
                elif loc in ["LF", "CF", "RF"]:
                    ball_type = "FB"
                    bt_conf += 1
                    bt_reasons.append("No explicit FB phrase → inferred FB from outfield location")

            # (confidence labels kept for future debug; not displayed)
            _ = overall_confidence_score(loc_conf + bt_conf)
            _ = loc_reasons + bt_reasons

            game_team[loc] += 1
            game_players[batter][loc] += 1

            if ball_type in BALLTYPE_KEYS:
                game_team[ball_type] += 1
                game_players[batter][ball_type] += 1

            if ball_type in ["GB", "FB"] and loc in COMBO_LOCS:
                combo_key = f"{ball_type}-{loc}"
                game_team[combo_key] += 1
                game_players[batter][combo_key] += 1

        add_game_to_season(season_team, season_players, game_team, game_players)

        # --- Merge game pitching into season pitching ---
        if "season_pitching" not in locals() or season_pitching is None:
            season_pitching = {}
        for pn, pst in (game_pitching or {}).items():
            pname = str(pn).strip().strip('"') or "UNKNOWN_P"
            season_pitching.setdefault(pname, empty_pitching_stat())
            ensure_pitching_keys(season_pitching[pname])
            ensure_pitching_keys(pst)
            for k in ["OUTS", "K", "BB", "PITCHES", "STRIKES"]:
                season_pitching[pname][k] = int(season_pitching[pname].get(k, 0)) + int(pst.get(k, 0) or 0)

        # ✅ Save with archived_players too
        db_save_season_totals(TEAM_CODE_SAFE, team_key, season_team, season_players, len(processed_set), archived_players, season_pitching)

        st.success("✅ Game processed and added to season totals (Supabase).")
        rerun_needed = True

    except Exception as e:
        if marked_processed and gkey:
            try:
                processed_set.discard(gkey)
            except Exception:
                pass
            db_unmark_game_processed(TEAM_CODE_SAFE, team_key, gkey)

        _show_db_error(e, "Processing failed (rolled back dedupe mark so you can retry)")
        st.stop()

    finally:
        st.session_state.processing_game = False
        st.session_state.processing_started_at = 0.0

    if rerun_needed:
        st.rerun()


# -----------------------------
# SEASON OUTPUTS
# -----------------------------
st.subheader(f"📔 Per-Player Spray – SEASON TO DATE ({selected_team})")

show_archived = st.checkbox("Show archived players (not on current roster)", value=False)

season_rows = []

active_players = sorted([p for p in current_roster if p in season_players])

# ✅ archived list comes from DB (NOT recomputed)
archived_list = sorted([p for p in (archived_players or set()) if p in season_players and p not in current_roster])

if show_archived:
    display_players = active_players + archived_list
else:
    display_players = active_players

for player in display_players:
    stats = season_players[player]
    row = {"Player": player}
    for loc in LOCATION_KEYS:
        row[loc] = stats.get(loc, 0)
    row["GB"] = stats.get("GB", 0)
    row["FB"] = stats.get("FB", 0)
    for ck in COMBO_KEYS:
        row[ck] = stats.get(ck, 0)
    for rk in RUN_KEYS:
        row[rk] = stats.get(rk, 0)
    season_rows.append(row)

df_season = pd.DataFrame(season_rows)
col_order = (["Player"] + LOCATION_KEYS + ["GB", "FB"] + COMBO_KEYS + RUN_KEYS)
col_order = [c for c in col_order if c in df_season.columns]
df_season = df_season[col_order]

st.dataframe(df_season, use_container_width=True)

# -----------------------------
# YUKON PITCHING – SEASON TO DATE
# -----------------------------
st.subheader("⚾ Pitching – SEASON TO DATE")

pitch_rows = []
for pname in sorted((season_pitching or {}).keys(), key=lambda x: x.lower()):
    pst = ensure_pitching_keys((season_pitching or {}).get(pname, {}))
    # skip any empty / accidental entries
    if sum(int(pst.get(k, 0) or 0) for k in ["OUTS","K","BB","PITCHES","STRIKES"]) == 0:
        continue
    outs = int(pst.get("OUTS", 0) or 0)
    pitches = int(pst.get("PITCHES", 0) or 0)
    strikes = int(pst.get("STRIKES", 0) or 0)
    k = int(pst.get("K", 0) or 0)
    bb = int(pst.get("BB", 0) or 0)
    spct = (strikes / pitches) if pitches else None
    pitch_rows.append({
        "Pitcher": pname,
        "IP": outs_to_ip_str(outs),
        "K": k,
        "BB": bb,
        "Pitches": pitches,
        "Strikes": strikes,
        "Strike%": spct,
    })

df_pitching = pd.DataFrame(pitch_rows)
if df_pitching.empty:
    st.info("No pitching data found yet (requires opponent half-innings + pitching lines in GC play-by-play).")
else:
    st.dataframe(df_pitching, use_container_width=True)

csv_bytes = df_season.to_csv(index=False).encode("utf-8")
safe_team = re.sub(r"[^A-Za-z0-9_-]+", "_", selected_team).strip("_")


out = BytesIO()

# -----------------------------
# ✅ Excel Export (Team tab + one tab per ACTIVE player)
#   - Tab 1: full team report (Season)
#   - Tabs 2+: every active roster hitter gets their own tab (even 0 BIP)
# -----------------------------
def _safe_sheet_name(name: str) -> str:
    # Excel: max 31 chars, cannot contain: : \ / ? * [ ]
    name = re.sub(r'[:\\/?*\[\]]', '', str(name or '')).strip()
    name = re.sub(r'\s+', ' ', name)
    return (name or "Player")[:31]

def _unique_sheet_name(wb, base_name: str) -> str:
    if base_name not in wb.sheetnames:
        return base_name
    i = 2
    while True:
        suffix = f" {i}"
        candidate = (base_name[: 31 - len(suffix)] + suffix)[:31]
        if candidate not in wb.sheetnames:
            return candidate
        i += 1

from openpyxl.styles import Font, Alignment, PatternFill as OPFill

def _format_ws_like_season(ws, df: pd.DataFrame):
    # Freeze header
    ws.freeze_panes = "A2"

    header_font = Font(bold=True)
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    header_fill = OPFill("solid", fgColor="D9E1F2")

    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = header_align
        cell.fill = header_fill

    # Column widths (based on header + first N rows)
    for col_idx, col_name in enumerate(df.columns, start=1):
        col_letter = get_column_letter(col_idx)
        max_len = len(str(col_name))
        sample = df[col_name].astype(str).head(60).tolist()
        for v in sample:
            max_len = max(max_len, len(v))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 8), 22)

    # Conditional formatting range: numeric section only (everything after Player col)
    start_row = 2
    start_col = 2  # numeric starts after Player
    end_row = ws.max_row
    end_col = ws.max_column

    if end_row >= start_row and end_col >= start_col:
        start_cell = f"{get_column_letter(start_col)}{start_row}"
        end_cell = f"{get_column_letter(end_col)}{end_row}"
        data_range = f"{start_cell}:{end_cell}"

        # gray out zeros (relative formula so each cell checks itself)
        zero_fill = OPFill("solid", fgColor="EFEFEF")
        zero_rule = FormulaRule(
            formula=[f"{get_column_letter(start_col)}{start_row}=0"],
            fill=zero_fill,
            stopIfTrue=True,
        )
        ws.conditional_formatting.add(data_range, zero_rule)

        # heatmap
        heat_rule = ColorScaleRule(
            start_type="num", start_value=1, start_color="FFFFFF",
            mid_type="percentile", mid_value=50, mid_color="FFF2CC",
            end_type="max", end_color="F8CBAD",
        )
        ws.conditional_formatting.add(data_range, heat_rule)

    # highlight UNKNOWN > 0
    if "UNKNOWN" in list(df.columns):
        unk_idx = list(df.columns).index("UNKNOWN") + 1
        unk_col = get_column_letter(unk_idx)
        unk_range = f"{unk_col}{start_row}:{unk_col}{end_row}"
        unk_fill = OPFill("solid", fgColor="FFC7CE")
        unk_rule = CellIsRule(operator="greaterThan", formula=["0"], fill=unk_fill)
        ws.conditional_formatting.add(unk_range, unk_rule)

    # center numbers
    num_align = Alignment(horizontal="center", vertical="center")
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            if isinstance(cell.value, (int, float)):
                cell.alignment = num_align

with pd.ExcelWriter(out, engine="openpyxl") as writer:
    # --- Tab 1: TEAM (your existing Season sheet) ---
    sheet_name = "Season"
    df_season.to_excel(writer, index=False, sheet_name=sheet_name)
    ws_team = writer.book[sheet_name]
    _format_ws_like_season(ws_team, df_season)

    # --- Optional: Pitching sheet (unchanged) ---
    if "df_pitching" in locals() and isinstance(df_pitching, pd.DataFrame) and not df_pitching.empty:
        p_sheet = "Yukon Pitching"
        df_pitching.to_excel(writer, index=False, sheet_name=p_sheet)
        wp = writer.book[p_sheet]
        wp.freeze_panes = "A2"

        # Header style
        header_font = Font(bold=True)
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_fill = OPFill("solid", fgColor="D9E1F2")
        for cell in wp[1]:
            cell.font = header_font
            cell.alignment = header_align
            cell.fill = header_fill

        # Column widths
        for col_idx, col_name in enumerate(df_pitching.columns, start=1):
            col_letter = get_column_letter(col_idx)
            max_len = len(str(col_name))
            sample = df_pitching[col_name].astype(str).head(80).tolist()
            for v in sample:
                max_len = max(max_len, len(v))
            wp.column_dimensions[col_letter].width = min(max(max_len + 2, 8), 20)

        # Center numeric columns
        num_align = Alignment(horizontal="center", vertical="center")
        for row in wp.iter_rows(min_row=2, max_row=wp.max_row):
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    cell.alignment = num_align

        # Percent format for Strike%
        if "Strike%" in df_pitching.columns:
            col_idx = list(df_pitching.columns).index("Strike%") + 1
            col_letter = get_column_letter(col_idx)
            for r in range(2, wp.max_row + 1):
                wp[f"{col_letter}{r}"].number_format = "0.0%"

    # --- Tabs 2+: one sheet per ACTIVE hitter (even if all zeros) ---
    # Always use current roster as "active"
    export_players = sorted([p for p in current_roster if p in season_players], key=lambda s: s.lower())

    for player in export_players:
        stats = season_players.get(player, empty_stat_dict())

        # Build a single-row DF matching the Season columns exactly
        row = {"Player": player}
        for loc in LOCATION_KEYS:
            row[loc] = stats.get(loc, 0)
        row["GB"] = stats.get("GB", 0)
        row["FB"] = stats.get("FB", 0)
        for ck in COMBO_KEYS:
            row[ck] = stats.get(ck, 0)
        for rk in RUN_KEYS:
            row[rk] = stats.get(rk, 0)

        df_player = pd.DataFrame([row])
        # Match Season column order
        df_player = df_player[df_season.columns.tolist()]

        base = _safe_sheet_name(player)
        name = _unique_sheet_name(writer.book, base)
        df_player.to_excel(writer, index=False, sheet_name=name)
        ws_p = writer.book[name]
        _format_ws_like_season(ws_p, df_player)

excel_bytes = out.getvalue()

col_dl1, col_dl2 = st.columns(2)
with col_dl1:
    st.download_button(
        label="📊 Download Season Report (Excel)",
        data=excel_bytes,
        file_name=f"{TEAM_CODE}_{safe_team}_Season_Spray_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

with col_dl2:
    st.download_button(
        label="📄 Download Season Report (CSV - Google Sheets Ready)",
        data=csv_bytes,
        file_name=f"{TEAM_CODE}_{safe_team}_Season_Spray_Report.csv",
        mime="text/csv",
    )

st.subheader(f"🎯 Individual Spray – SEASON TO DATE ({selected_team})")

# ✅ Individual dropdown matches roster by default; archived optional
if show_archived:
    indiv_candidates = sorted(set(active_players + archived_list))
else:
    indiv_candidates = active_players

selectable_players = [p for p in indiv_candidates if p in season_players]

if not selectable_players:
    st.info("No hitters found on the active roster yet.")
else:
    selected_player = st.selectbox("Choose a hitter:", selectable_players)
    stats = season_players[selected_player]

    indiv_rows = [{"Type": loc, "Count": stats.get(loc, 0)} for loc in LOCATION_KEYS]
    indiv_rows.append({"Type": "GB (total)", "Count": stats.get("GB", 0)})
    indiv_rows.append({"Type": "FB (total)", "Count": stats.get("FB", 0)})

    for ck in COMBO_KEYS:
        indiv_rows.append({"Type": ck, "Count": stats.get(ck, 0)})

    # running events
    indiv_rows.append({"Type": "SB", "Count": stats.get("SB", 0)})
    indiv_rows.append({"Type": "CS", "Count": stats.get("CS", 0)})
    indiv_rows.append({"Type": "DI", "Count": stats.get("DI", 0)})
    for rk in RUN_KEYS:
        if rk not in ["SB", "CS", "DI"]:
            indiv_rows.append({"Type": rk, "Count": stats.get(rk, 0)})

    st.table(indiv_rows)


# -----------------------------
# FOOTER (Copyright)
# -----------------------------
st.markdown(
    """
    <style>
    .rp-footer {
        margin-top: 40px;
        padding-top: 12px;
        border-top: 1px solid rgba(0,0,0,0.12);
        text-align: center;
        font-size: 0.85rem;
        color: rgba(0,0,0,0.55);
    }
    </style>

    <div class="rp-footer">
        © 2026 RP Spray Analytics. All rights reserved.
    </div>
    """,
    unsafe_allow_html=True,
)





















































































