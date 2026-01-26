import streamlit as st
import os
import json
import base64
import re
from datetime import datetime
from typing import Optional, Tuple

# -----------------------------
# PATHS / FOLDERS
# -----------------------------
SETTINGS_PATH = os.path.join("TEAM_CONFIG", "team_settings.json")
ROSTERS_DIR = os.path.join("TEAM_CONFIG", "rosters")
ASSETS_DIR = "assets"
SEASON_DIR = os.path.join("data", "season_totals")

os.makedirs(ROSTERS_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(SEASON_DIR, exist_ok=True)

# -----------------------------
# SETTINGS LOADER
# -----------------------------
def load_settings():
    defaults = {
        "app_title": "RP Spray Analytics",
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

# ============================
# ACCESS CODE GATE
# ============================

SETTINGS_PATH = os.path.join("TEAM_CONFIG", "team_settings.json")

@st.cache_data(show_spinner=False)
def load_team_codes() -> dict:
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        codes = data.get("codes", {}) or {}
        # Normalize keys to uppercase with no spaces
        return {str(k).strip().upper(): v for k, v in codes.items()}
    except Exception:
        return {}

def require_team_access():
    codes = load_team_codes()

    if "team_code" not in st.session_state:
        st.session_state.team_code = None

    # Already unlocked
    if st.session_state.team_code in codes:
        return st.session_state.team_code, codes[st.session_state.team_code]

    # Lock screen
    st.title("RP Spray Analytics")
    st.markdown("### Enter Access Code")

    code = st.text_input("Access Code").strip().upper()

    if st.button("Unlock"):
        if code in codes:
            st.session_state.team_code = code
            st.rerun()
        else:
            st.error("Invalid access code")

    st.stop()

TEAM_CODE, TEAM_CFG = require_team_access()

# -----------------------------
# PAGE CONFIG
# -----------------------------
st.set_page_config(
    page_title=SETTINGS["app_title"],
    page_icon="⚾",
    layout="wide",
)

def get_base64_image(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

# Team-specific branding override
if TEAM_CFG:
    BG_B64 = get_base64_image(TEAM_CFG["background_path"])
    LOGO_PATH = TEAM_CFG["logo_path"]
else:
    BG_B64 = get_base64_image(SETTINGS["background_image"])
    LOGO_PATH = SETTINGS["logo_image"]

PRIMARY = SETTINGS["primary_color"]
SECONDARY = SETTINGS["secondary_color"]
LOGO_B64 = get_base64_image(LOGO_PATH)


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

        /* CRISP BORDER */
        -webkit-text-stroke: 2.5px #000000;

        /* NO BLUR — HARD SHADOW ONLY */
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
            linear-gradient(rgba(229,231,235,0.90),
                            rgba(229,231,235,0.90)),
            url("data:image/jpeg;base64,{BG_B64}") no-repeat center fixed;
        background-size: 600px;
        color: #111827;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# HEADER
# -----------------------------
st.markdown(
    f"<h1 class='app-title'>{SETTINGS['app_title']}</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div class='app-subtitle'>{SETTINGS['subtitle']}</div>",
    unsafe_allow_html=True,
)
st.markdown("---")

# -----------------------------
# ENGINE CONSTANTS
# -----------------------------
LOCATION_KEYS = ["LF", "CF", "RF", "3B", "SS", "2B", "1B", "P", "Bunt", "Sac Bunt", "UNKNOWN"]
BALLTYPE_KEYS = ["GB", "FB"]
COMBO_LOCS = [loc for loc in LOCATION_KEYS if loc not in ["Bunt", "Sac Bunt", "UNKNOWN"]]
COMBO_KEYS = [f"GB-{loc}" for loc in COMBO_LOCS] + [f"FB-{loc}" for loc in COMBO_LOCS]

# Running event tracking (NOT balls in play)
RUN_KEYS = [
    # Stolen Bases
    "SB", "SB-2B", "SB-3B", "SB-H",
    # Caught Stealing
    "CS", "CS-2B", "CS-3B", "CS-H",
    # Defensive Indifference
    "DI", "DI-2B", "DI-3B", "DI-H",
    # Pickoffs
    "PO", "PO-1B", "PO-2B", "PO-3B", "PO-H",
    # Picked off + caught stealing
    "POCS", "POCS-2B", "POCS-3B", "POCS-H",
]

# -----------------------------
# REGEX / PATTERNS (YOUR ENGINE)
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
# RUNNING EVENTS (SB / CS / DI / PO / POCS) — UPGRADED
# -----------------------------

# SB: steals/stole/stolen base + optional wording + parentheses
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

# CS: caught stealing / out stealing + optional filler + optional base
CS_ACTION_REGEX = re.compile(
    r"""
    \b(?:caught\s+stealing|out\s+stealing)\b
    (?:\s+(?:at|trying\s+for|attempting|to))?
    (?:\s+base)?
    (?:\s*(\(?\s*(?:2nd|3rd|home|second|third)\s*\)?))?
    """,
    re.IGNORECASE | re.VERBOSE
)

# DI: defensive indifference with advance phrasing
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

DI_REGEX_BARE = re.compile(
    r"\bdefensive\s+indifference\b",
    re.IGNORECASE
)

# PO: picked off / pickoff + optional base
PO_REGEX = re.compile(
    r"""
    \b(?:picked\s+off|pickoff)\b
    (?:\s+(?:at|on|from))?
    (?:\s+base)?
    (?:\s*(\(?\s*(?:1st|2nd|3rd|home|first|second|third)\s*\)?))?
    """,
    re.IGNORECASE | re.VERBOSE
)

# POCS: pickoff + caught stealing (either order)
POCS_REGEX = re.compile(
    r"""
    \b(?:picked\s+off|pickoff)\b
    .*?
    \b(?:caught\s+stealing|out\s+stealing)\b
    (?:\s+(?:at|trying\s+for|attempting|to))?
    (?:\s+base)?
    (?:\s*(\(?\s*(?:2nd|3rd|home|second|third)\s*\)?))?

    |

    \b(?:caught\s+stealing|out\s+stealing)\b
    .*?
    \b(?:picked\s+off|pickoff)\b
    (?:\s+(?:at|on|from))?
    (?:\s+base)?
    (?:\s*(\(?\s*(?:2nd|3rd|home|second|third)\s*\)?))?
    """,
    re.IGNORECASE | re.VERBOSE
)
# Runner notation (some exports): "R1 steals 2nd", "R2 picked off", etc.
RUNNER_TAG_REGEX = re.compile(r"\bR([123])\b", re.IGNORECASE)

PAREN_NAME_REGEX = re.compile(r"\(([^)]+)\)")

def normalize_base_bucket(prefix: str, base_raw: Optional[str]) -> str:
    if not base_raw:
        return prefix  # unknown base
    b = base_raw.strip().lower()
    if b in ["1st", "first"]:
        return f"{prefix}-1B"
    if b in ["2nd", "second"]:
        return f"{prefix}-2B"
    if b in ["3rd", "third"]:
        return f"{prefix}-3B"
    if b == "home":
        return f"{prefix}-H"
    return prefix

BAD_FIRST_TOKENS = {
    "top","bottom","inning","pitch","ball","strike","foul",
    "runner","runners","advances","advance","steals","stole","caught","picked","pick","pickoff",
    "substitution","defensive","offensive","double","triple","single","home",
    "out","safe","error","no","one","two","three",
}
def starts_like_name(token: str) -> bool:
    if not token:
        return False
    t = token.strip().strip('"').strip().lower()
    return t[:1].isalpha() and t not in BAD_FIRST_TOKENS

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

def overall_confidence_score(conf_val: int):
    if conf_val >= 4:
        return "high"
    elif conf_val >= 2:
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

def extract_runner_name_near_event(clean_line: str, match_start: int, roster: set[str]) -> Optional[str]:
    """
    Best-effort: runner name often appears immediately before the event phrase after the last comma.
    Example: 'Ball 1, Ball 2, J Smith steals 2nd, ...'
    """
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
    """
    Backup methods:
    - If line starts with a name, get_batter_name catches it
    - If parentheses contain a name, try that
    """
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
    """
    # --- POCS (check first so it doesn't get swallowed by PO or CS) ---
    m = POCS_REGEX.search(clean_line)
    if m:
        base_raw = None
        for gi in range(1, (m.lastindex or 0) + 1):
            if m.group(gi):
                base_raw = m.group(gi)
                break
        base_key = normalize_base_bucket("POCS", base_raw)
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "POCS", base_key

    # --- SB ---
    m = SB_ACTION_REGEX.search(clean_line)
    if m:
        base_key = normalize_base_bucket("SB", m.group(1))
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "SB", base_key

    # --- CS ---
    m = CS_ACTION_REGEX.search(clean_line)
    if m:
        base_raw = m.group(1) if m.lastindex and m.group(1) else None
        base_key = normalize_base_bucket("CS", base_raw)
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "CS", base_key

    # --- DI ---
    m = DI_REGEX_1.search(clean_line) or DI_REGEX_2.search(clean_line)
    if m:
        base_key = normalize_base_bucket("DI", m.group(1))
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "DI", base_key

    if DI_REGEX_BARE.search(clean_line):
        runner = extract_runner_name_fallback(clean_line, roster)
        return runner, "DI", "DI"

    # --- PO ---
    m = PO_REGEX.search(clean_line)
    if m:
        base_raw = m.group(2) if m.lastindex and m.group(2) else None
        base_key = normalize_base_bucket("PO", base_raw)
        runner = extract_runner_name_near_event(clean_line, m.start(), roster) or extract_runner_name_fallback(clean_line, roster)
        return runner, "PO", base_key

    return None, None, None

def is_ball_in_play(line_lower: str) -> bool:
    ll = (line_lower or "").strip()
    if not ll:
        return False

    # Explicit NOT-BIP events
    if any(kw in ll for kw in [
        "hit by pitch","hit-by-pitch","hit batsman",
        "walks","walked"," base on balls","intentional walk",
        "strikes out","strikeout","called out on strikes",
        "reaches on catcher interference","catcher's interference",

        # running events — we track separately
        "caught stealing","out stealing",
        "picked off","pickoff",
        "steals","stole","stealing",
        "defensive indifference",
    ]):
        return False

    bip_outcomes = [
        "grounds","grounded","ground ball","groundball","grounder",
        "singles","doubles","triples","homers","home run",
        "lines out","line drive","lined out","line out",
        "flies out","fly ball","flied out","fly out",
        "pops out","pop up","pop-out","popup",
        "bloops","blooper",
        "bunts","bunt","sacrifice bunt","sac bunt","sacrifice hit",
        "sac fly","sacrifice fly",
        "reaches on a fielding error","reaches on a throwing error",
        "reaches on error","reached on error","safe on error",
        "reaches on a missed catch error",
        "fielder's choice","fielders choice",
        "double play","triple play",
        "out at first","out at second","out at third","out at home",
    ]
    if any(kw in ll for kw in bip_outcomes):
        return True

    fielder_markers = [
        "left fielder","center fielder","right fielder",
        "shortstop","second baseman","third baseman","first baseman",
        "to left field","to center field","to right field",
        "to shortstop","to second baseman","to third baseman","to first baseman",
        "to pitcher","back to the mound",
        "down the left","down the right","left-center","right-center"
    ]
    return any(m in ll for m in fielder_markers)

def classify_ball_type(line_lower: str):
    reasons = []
    ball_type = None
    conf = 0

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

    return None, conf, reasons

def classify_location(line_lower: str, strict_mode: bool = False):
    reasons = []
    loc = None
    conf = 0

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
        idx, loc, reason = min(candidates, key=lambda x: x[0])
        return loc, 3, [reason]

    if strict_mode:
        return None, 0, ["Strict mode: no explicit fielder/location phrase found"]

    for kw in LEFT_SIDE_PATTERNS:
        if kw in line_lower:
            return "SS", 1, [f"Matched left-side phrase: '{kw}' → approximate SS"]

    for kw in RIGHT_SIDE_PATTERNS:
        if kw in line_lower:
            return "2B", 1, [f"Matched right-side phrase: '{kw}' → approximate 2B"]

    return None, 0, reasons

# -----------------------------
# UNLIMITED TEAMS: read roster files
# -----------------------------
def list_team_files():
    files = []
    for fn in os.listdir(ROSTERS_DIR):
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
    return os.path.join(ROSTERS_DIR, filename)

def load_roster_text(path: str) -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def save_roster_text(path: str, text: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n" if text.strip() else "")

# -----------------------------
# SEASON FILES PER TEAM
# -----------------------------
def season_file_for_team(team_key: str) -> str:
    return os.path.join(SEASON_DIR, f"{team_key}_spray_totals.json")

def load_season_totals(team_key: str, current_roster):
    filename = season_file_for_team(team_key)

    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                data = json.load(f)
            raw_team = data.get("team", {})
            raw_players = data.get("players", {})
            meta = data.get("meta", {})
            games_played = meta.get("games_played", 0)
        except Exception:
            raw_team, raw_players, games_played = {}, {}, 0
    else:
        raw_team, raw_players, games_played = {}, {}, 0

    season_team = ensure_all_keys(raw_team if isinstance(raw_team, dict) else {})
    season_players = {}

    if isinstance(raw_players, dict):
        for p, stat_dict in raw_players.items():
            season_players[p] = ensure_all_keys(stat_dict) if isinstance(stat_dict, dict) else empty_stat_dict()

    for p in current_roster:
        if p not in season_players:
            season_players[p] = empty_stat_dict()

    return season_team, season_players, games_played

def save_season_totals(team_key: str, season_team, season_players, games_played: int):
    filename = season_file_for_team(team_key)
    data = {"meta": {"games_played": games_played}, "team": season_team, "players": season_players}
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def add_game_to_season(season_team, season_players, game_team, game_players):
    for key in LOCATION_KEYS + BALLTYPE_KEYS + COMBO_KEYS + RUN_KEYS:
        season_team[key] = season_team.get(key, 0) + game_team.get(key, 0)

    for player, gstats in game_players.items():
        season_players.setdefault(player, empty_stat_dict())
        sstats = season_players[player]
        for key in LOCATION_KEYS + BALLTYPE_KEYS + COMBO_KEYS + RUN_KEYS:
            sstats[key] = sstats.get(key, 0) + gstats.get(key, 0)

# -----------------------------
# SIDEBAR
# -----------------------------
with st.sidebar:
    if SETTINGS.get("logo_image") and os.path.exists(SETTINGS["logo_image"]):
        st.image(SETTINGS["logo_image"], width=260)

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

    strict_mode = st.checkbox(
        "STRICT MODE (only count plays with explicit fielder/location)",
        value=bool(SETTINGS.get("strict_mode_default", True)),
    )

    st.markdown("---")
    st.write("**Add teams** by creating a `.txt` roster file in:")
    st.code("TEAM_CONFIG/rosters/", language="text")

# -----------------------------
# TEAM SELECTION (AUTO FROM FILES)
# -----------------------------
st.subheader("🏟️ Team Selection")

team_files = list_team_files()
if not team_files:
    st.warning("No roster files found in TEAM_CONFIG/rosters/. Create one like 'My Team.txt' with 1 hitter per line.")
    st.stop()

team_names = [team_name_from_file(f) for f in team_files]
selected_team = st.selectbox("Choose a team (from roster files):", team_names)
selected_file = team_files[team_names.index(selected_team)]
team_key = safe_team_key(selected_team)

with st.expander("➕ Add a new team roster file"):
    new_team_name = st.text_input("New team name (creates a .txt in TEAM_CONFIG/rosters/):")
    if st.button("Create Team File"):
        if not new_team_name.strip():
            st.error("Enter a team name first.")
        else:
            new_file = f"{new_team_name.strip()}.txt"
            new_path = roster_path_for_file(new_file)
            if os.path.exists(new_path):
                st.error("That team file already exists.")
            else:
                save_roster_text(new_path, "")
                st.success(f"Created: {new_path}. Select it in the dropdown above.")

st.markdown("---")

# -----------------------------
# ROSTER UI (LOADS FROM TEAM FILE)
# -----------------------------
st.subheader(f"📝 {selected_team} Roster (Hitters)")

roster_path = roster_path_for_file(selected_file)
default_roster_text = load_roster_text(roster_path)

roster_text = st.text_area(
    "One player per line EXACTLY like GameChanger shows them (e.g., 'J Smith')",
    value=default_roster_text,
    height=220,
)

col_a, col_b = st.columns([1, 3])
with col_a:
    if st.button("💾 Save Roster to File"):
        save_roster_text(roster_path, roster_text)
        st.success("Roster saved.")

current_roster = {line.strip().strip('"') for line in roster_text.split("\n") if line.strip()}
st.write(f"**Hitters loaded:** {len(current_roster)}")

season_team, season_players, games_played = load_season_totals(team_key, current_roster)
sf = season_file_for_team(team_key)

if os.path.exists(sf):
    last_updated_dt = datetime.fromtimestamp(os.path.getmtime(sf))
    last_updated_str = last_updated_dt.strftime("%Y-%m-%d %H:%M")
else:
    last_updated_str = "Never"

st.markdown(
    f"""
    <div class="spray-card">
        <strong>Active team:</strong> {selected_team}<br>
        <strong>Season file:</strong> <code>{sf}</code><br>
        <strong>Games processed:</strong> {games_played}<br>
        <strong>Last updated:</strong> {last_updated_str}
    </div>
    """,
    unsafe_allow_html=True,
)

col_reset, _ = st.columns([1, 3])
with col_reset:
    if st.button(f"❗ Reset SEASON totals for {selected_team}"):
        season_team = empty_stat_dict()
        season_players = {p: empty_stat_dict() for p in current_roster}
        games_played = 0
        save_season_totals(team_key, season_team, season_players, games_played)
        st.warning("Season totals reset for this team.")

# -----------------------------
# PLAY-BY-PLAY INPUT
# -----------------------------
st.subheader("📓 GameChanger Play-by-Play")

raw_text = st.text_area(
    f"Paste the full play-by-play for ONE game involving {selected_team}:",
    height=260,
)

# -----------------------------
# PROCESS GAME
# -----------------------------
if st.button("📥 Process Game (ADD to Season Totals)"):
    if not raw_text.strip():
        st.error("Paste play-by-play first.")
    elif not current_roster:
        st.error("Roster is empty. Add hitters first (and save).")
    else:
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

        game_team = empty_stat_dict()
        game_players = {p: empty_stat_dict() for p in current_roster}
        debug_samples = []

        for line in lines:
            clean_line = line.strip().strip('"')
            clean_line = re.sub(r"\([^)]*\)", "", clean_line)
            clean_line = re.sub(r"\s+", " ", clean_line).strip()
            line_lower = clean_line.lower()

            # ----- RUNNING EVENTS (SB / CS / DI / PO / POCS) -----
            runner, total_key, base_key = parse_running_event(clean_line, current_roster)
            if runner and total_key:
                game_team[total_key] += 1
                game_players[runner][total_key] += 1
                if base_key and base_key in RUN_KEYS:
                    game_team[base_key] += 1
                    game_players[runner][base_key] += 1

            # ----- BIP spray engine -----
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

            total_conf_val = loc_conf + bt_conf
            conf_label = overall_confidence_score(total_conf_val)
            reasons = loc_reasons + bt_reasons

            if len(debug_samples) < 60:
                debug_samples.append(
                    f"{batter} -> loc={loc}, ball={ball_type or 'None'}, conf={conf_label} | "
                    f"{'; '.join(reasons) or 'no explicit phrase match'} | {clean_line}"
                )

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
        games_played += 1
        save_season_totals(team_key, season_team, season_players, games_played)

        st.subheader("🧪 Debug: Recognized Balls in Play")
        if debug_samples:
            for row in debug_samples:
                st.text(row)
        else:
            st.info("No hitters recognized with balls in play. Check roster name matching.")

        st.subheader("📊 Team Spray – THIS GAME (Legacy)")
        st.table([{"Location": loc, "Count": game_team[loc]} for loc in LOCATION_KEYS])

        st.subheader("📌 Team Spray – THIS GAME (GB/FB by Location)")
        st.table([{"Bucket": ck, "Count": game_team.get(ck, 0)} for ck in COMBO_KEYS])

        st.subheader("🏃 Running Events – THIS GAME")
        st.table([
            {"Type": "SB", "Count": game_team.get("SB", 0)},
            {"Type": "CS", "Count": game_team.get("CS", 0)},
            {"Type": "DI", "Count": game_team.get("DI", 0)},
            {"Type": "PO", "Count": game_team.get("PO", 0)},
            {"Type": "POCS", "Count": game_team.get("POCS", 0)},
            {"Type": "SB-2B", "Count": game_team.get("SB-2B", 0)},
            {"Type": "SB-3B", "Count": game_team.get("SB-3B", 0)},
            {"Type": "SB-H", "Count": game_team.get("SB-H", 0)},
            {"Type": "CS-2B", "Count": game_team.get("CS-2B", 0)},
            {"Type": "CS-3B", "Count": game_team.get("CS-3B", 0)},
            {"Type": "CS-H", "Count": game_team.get("CS-H", 0)},
            {"Type": "DI-2B", "Count": game_team.get("DI-2B", 0)},
            {"Type": "DI-3B", "Count": game_team.get("DI-3B", 0)},
            {"Type": "DI-H", "Count": game_team.get("DI-H", 0)},
            {"Type": "PO-1B", "Count": game_team.get("PO-1B", 0)},
            {"Type": "PO-2B", "Count": game_team.get("PO-2B", 0)},
            {"Type": "PO-3B", "Count": game_team.get("PO-3B", 0)},
            {"Type": "PO-H", "Count": game_team.get("PO-H", 0)},
            {"Type": "POCS-2B", "Count": game_team.get("POCS-2B", 0)},
            {"Type": "POCS-3B", "Count": game_team.get("POCS-3B", 0)},
            {"Type": "POCS-H", "Count": game_team.get("POCS-H", 0)},
        ])

        st.subheader("👤 Per-Player Spray – THIS GAME")
        rows = []
        for player in sorted(current_roster):
            stats = game_players[player]
            row = {"Player": player}
            for loc in LOCATION_KEYS:
                row[loc] = stats.get(loc, 0)
            row["GB"] = stats.get("GB", 0)
            row["FB"] = stats.get("FB", 0)
            for ck in COMBO_KEYS:
                row[ck] = stats.get(ck, 0)

            # Running events
            for rk in RUN_KEYS:
                row[rk] = stats.get(rk, 0)

            rows.append(row)
        st.dataframe(rows)

        st.success("✅ Game processed and added to season totals. (Pasting same game twice will double count.)")

# -----------------------------
# SEASON OUTPUTS
# -----------------------------
st.subheader(f"📔 Per-Player Spray – SEASON TO DATE ({selected_team})")
season_rows = []
for player in sorted(season_players.keys()):
    stats = season_players[player]
    row = {"Player": player}
    for loc in LOCATION_KEYS:
        row[loc] = stats.get(loc, 0)
    row["GB"] = stats.get("GB", 0)
    row["FB"] = stats.get("FB", 0)
    for ck in COMBO_KEYS:
        row[ck] = stats.get(ck, 0)

    # Running events
    for rk in RUN_KEYS:
        row[rk] = stats.get(rk, 0)

    season_rows.append(row)
st.dataframe(season_rows)

st.subheader(f"🎯 Individual Spray – SEASON TO DATE ({selected_team})")
selectable_players = sorted(
    [p for p in season_players.keys()
     if any(season_players[p].get(k, 0) > 0 for k in (LOCATION_KEYS + BALLTYPE_KEYS + COMBO_KEYS + RUN_KEYS))]
)

if not selectable_players:
    st.info("No hitters have recorded balls in play yet.")
else:
    selected_player = st.selectbox("Choose a hitter:", selectable_players)
    stats = season_players[selected_player]
    indiv_rows = [{"Type": loc, "Count": stats.get(loc, 0)} for loc in LOCATION_KEYS]
    indiv_rows.append({"Type": "GB (total)", "Count": stats.get("GB", 0)})
    indiv_rows.append({"Type": "FB (total)", "Count": stats.get("FB", 0)})
    for ck in COMBO_KEYS:
        indiv_rows.append({"Type": ck, "Count": stats.get(ck, 0)})

    # Running events
    indiv_rows.append({"Type": "SB", "Count": stats.get("SB", 0)})
    indiv_rows.append({"Type": "CS", "Count": stats.get("CS", 0)})
    indiv_rows.append({"Type": "DI", "Count": stats.get("DI", 0)})
    indiv_rows.append({"Type": "PO", "Count": stats.get("PO", 0)})
    indiv_rows.append({"Type": "POCS", "Count": stats.get("POCS", 0)})
    for rk in RUN_KEYS:
        if rk not in ["SB", "CS", "DI", "PO", "POCS"]:
            indiv_rows.append({"Type": rk, "Count": stats.get(rk, 0)})

    st.table(indiv_rows)






