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

def _write_table_two_blocks(ws, start_row, cols, row_values, split_at=None, gap=2):
    """Write a header + rows into two side-by-side blocks for landscape printing.
    - cols: list of column names
    - row_values: list of lists (each list aligns to cols)
    - split_at: index to split columns. If None, split roughly in half.
    """
    if not cols:
        return start_row

    if split_at is None:
        split_at = max(1, (len(cols) + 1) // 2)

    left_cols = cols[:split_at]
    right_cols = cols[split_at:]

    left_start_col = 1
    right_start_col = 1 + len(left_cols) + gap

    # Header
    for j, c in enumerate(left_cols, start=0):
        ws.cell(row=start_row, column=left_start_col + j, value=c)
    for j, c in enumerate(right_cols, start=0):
        ws.cell(row=start_row, column=right_start_col + j, value=c)

    # Rows
    r = start_row + 1
    for vals in row_values:
        left_vals = vals[:split_at]
        right_vals = vals[split_at:]
        for j, v in enumerate(left_vals, start=0):
            ws.cell(row=r, column=left_start_col + j, value=v)
        for j, v in enumerate(right_vals, start=0):
            ws.cell(row=r, column=right_start_col + j, value=v)
        r += 1

    return r

from typing import Optional, Tuple

import pandas as pd
from io import BytesIO

from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule, FormulaRule, CellIsRule
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
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
        season_team, season_players, games_played, processed_set, archived_players = db_load_season_totals(
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
            TEAM_CODE_SAFE, team_key, season_team, season_players, games_played, archived_players
        )

        st.success("Roster saved + removed players archived (reports will match roster).")
        st.rerun()

current_roster = {line.strip().strip('"') for line in roster_text.split("\n") if line.strip()}
st.write(f"**Hitters loaded:** {len(current_roster)}")


# ✅ LOAD FROM SUPABASE ONLY (source of truth) — includes archived_players
season_team, season_players, games_played, processed_set, archived_players = db_load_season_totals(
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

        season_team, season_players, games_played, processed_set, archived_players = db_load_season_totals(
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
            db_save_season_totals(TEAM_CODE_SAFE, team_key, incoming_team, fixed_players, restored_games_played, restored_archived)

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

        for line in lines:
            clean_line = line.strip().strip('"')
            clean_line = re.sub(r"\([^)]*\)", "", clean_line)
            clean_line = re.sub(r"\s+", " ", clean_line).strip()
            line_lower = clean_line.lower()

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

        # ✅ Save with archived_players too
        db_save_season_totals(TEAM_CODE_SAFE, team_key, season_team, season_players, len(processed_set), archived_players)

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

row_left, row_right = st.columns([8, 2])
with row_left:
    show_archived = st.checkbox("Show archived players (not on current roster)", value=False)
with row_right:
    stat_edit_slot = st.empty()  # filled after df_season is built
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
# If no rows yet, preserve the expected columns so Stat Edit can still work
if df_season.empty and len(df_season.columns) == 0:
    df_season = pd.DataFrame(columns=col_order)

col_order = [c for c in col_order if c in df_season.columns]
df_season = df_season[col_order]

# -----------------------------
# Stat Edit (column visibility) — per selected opponent/team
# -----------------------------
# Hide Streamlit's built-in dataframe download icon (you already have download buttons below)
st.markdown(
    """
    <style>
      /* Try multiple selectors because Streamlit versions vary */
      [data-testid="stDataFrameToolbar"] button[title="Download data as CSV"] { display: none !important; }
      [data-testid="stDataFrameToolbar"] button[aria-label="Download data as CSV"] { display: none !important; }
      [data-testid="stDataFrameToolbar"] button[title="Download data"] { display: none !important; }
      [data-testid="stDataFrameToolbar"] button[aria-label="Download data"] { display: none !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# UI polish for the Stat Edit control (does NOT touch title sizing)
st.markdown(
    """
    <style>
    .stat-edit-wrap {
        display: flex;
        justify-content: flex-end;
        align-items: center;
        margin-top: -6px !important;
        margin-bottom: 6px !important;
    }
    .stat-edit-wrap button { white-space: nowrap; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Keyed by team/opponent so each opponent can have its own preferred view
cols_key = f"season_cols__{TEAM_CODE_SAFE}__{team_key}"

# Default: show everything
if cols_key not in st.session_state:
    st.session_state[cols_key] = list(df_season.columns)

all_cols = list(df_season.columns)
default_cols = list(st.session_state.get(cols_key, []))

# Keep only columns that still exist (safe if you add/remove stats later)
default_cols = [c for c in default_cols if c in all_cols]

# Always keep Player visible
if "Player" in all_cols and "Player" not in default_cols:
    default_cols = ["Player"] + default_cols



# Render Stat Edit in the same row as the archived checkbox (top-right)
with stat_edit_slot.container():
    st.markdown('<div class="stat-edit-wrap">', unsafe_allow_html=True)
    if hasattr(st, "popover"):
        with st.popover("Stat Edit"):
            st.caption("Toggle which stats show in the table")
            flt = st.text_input("Search", value="", placeholder="Type to filter stats...", key=f"{cols_key}__flt")

            c1, c2, c3 = st.columns([1, 1, 2])
            with c1:
                all_clicked = st.button("All", key=f"{cols_key}__all", use_container_width=True)
            with c2:
                none_clicked = st.button("None", key=f"{cols_key}__none", use_container_width=True)
            with c3:
                st.caption(" ")

            # Make All/None actually drive the checkbox states (Streamlit checkboxes are keyed)
            if all_clicked or none_clicked:
                for _col in all_cols:
                    _safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(_col))
                    _k = f"{cols_key}__cb__{_safe}"
                    if _col == "Player":
                        st.session_state[_k] = True
                    else:
                        st.session_state[_k] = True if all_clicked else False

                st.session_state[cols_key] = list(all_cols) if all_clicked else (["Player"] if "Player" in all_cols else [])
                st.rerun()

            picked_set = set(st.session_state.get(cols_key, default_cols))
            if "Player" in all_cols:
                picked_set.add("Player")  # lock Player ON

            view_cols = list(all_cols)
            if flt.strip():
                q = flt.strip().lower()
                view_cols = [c for c in view_cols if q in str(c).lower()]

            # Scroll so the popover stays compact
            with st.container(height=360):
                if "Player" in view_cols:
                    st.checkbox("Player", value=True, disabled=True, key=f"{cols_key}__cb__Player")
                    view_cols = [c for c in view_cols if c != "Player"]

                colA, colB, colC = st.columns(3)
                grid = [colA, colB, colC]
                for i, col in enumerate(view_cols):
                    target = grid[i % 3]
                    safe_col = re.sub(r"[^A-Za-z0-9_]+", "_", str(col))
                    cur_val = col in picked_set
                    new_val = target.checkbox(str(col), value=cur_val, key=f"{cols_key}__cb__{safe_col}")
                    if new_val:
                        picked_set.add(col)
                    else:
                        picked_set.discard(col)

            picked = [c for c in all_cols if c in picked_set]
            st.session_state[cols_key] = picked

    else:
        with st.expander("Stat Edit", expanded=False):
            st.caption("Toggle which stats show in the table")
            flt = st.text_input("Search", value="", placeholder="Type to filter stats...", key=f"{cols_key}__flt")

            c1, c2, c3 = st.columns([1, 1, 2])
            with c1:
                all_clicked = st.button("All", key=f"{cols_key}__all", use_container_width=True)
            with c2:
                none_clicked = st.button("None", key=f"{cols_key}__none", use_container_width=True)
            with c3:
                st.caption(" ")

            # Make All/None actually drive the checkbox states (Streamlit checkboxes are keyed)
            if all_clicked or none_clicked:
                for _col in all_cols:
                    _safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(_col))
                    _k = f"{cols_key}__cb__{_safe}"
                    if _col == "Player":
                        st.session_state[_k] = True
                    else:
                        st.session_state[_k] = True if all_clicked else False

                st.session_state[cols_key] = list(all_cols) if all_clicked else (["Player"] if "Player" in all_cols else [])
                st.rerun()

            picked_set = set(st.session_state.get(cols_key, default_cols))
            if "Player" in all_cols:
                picked_set.add("Player")  # lock Player ON

            view_cols = list(all_cols)
            if flt.strip():
                q = flt.strip().lower()
                view_cols = [c for c in view_cols if q in str(c).lower()]

            # Scroll so the popover stays compact
            with st.container(height=360):
                if "Player" in view_cols:
                    st.checkbox("Player", value=True, disabled=True, key=f"{cols_key}__cb__Player")
                    view_cols = [c for c in view_cols if c != "Player"]

                colA, colB, colC = st.columns(3)
                grid = [colA, colB, colC]
                for i, col in enumerate(view_cols):
                    target = grid[i % 3]
                    safe_col = re.sub(r"[^A-Za-z0-9_]+", "_", str(col))
                    cur_val = col in picked_set
                    new_val = target.checkbox(str(col), value=cur_val, key=f"{cols_key}__cb__{safe_col}")
                    if new_val:
                        picked_set.add(col)
                    else:
                        picked_set.discard(col)

            picked = [c for c in all_cols if c in picked_set]
            st.session_state[cols_key] = picked


    # Ensure Player stays visible (failsafe)
    picked = list(st.session_state.get(cols_key, default_cols))
    if "Player" in all_cols and "Player" not in picked:
        picked = ["Player"] + picked
        st.session_state[cols_key] = picked

    st.markdown("</div>", unsafe_allow_html=True)

# Apply the selection
picked_cols = [c for c in st.session_state.get(cols_key, []) if c in all_cols]
df_show = df_season[picked_cols] if picked_cols else df_season


# -----------------------------
# CURRENT VIEW COLUMNS (Stat Edit -> downloads)
# -----------------------------
# Build visible_cols safely for both table display and downloads.
try:
    _vc = st.session_state.get(cols_key, list(df_season.columns))
except Exception:
    _vc = list(df_season.columns)

if not isinstance(_vc, (list, tuple)):
    _vc = list(df_season.columns)

visible_cols = [c for c in _vc if c in df_season.columns]

# Always keep Player if it exists
if "Player" in df_season.columns and "Player" not in visible_cols:
    visible_cols = ["Player"] + visible_cols

if len(visible_cols) == 0:
    visible_cols = list(df_season.columns)
st.dataframe(df_show, use_container_width=True)

# -----------------------------
# 📝 COACHES SCOUTING NOTES (per selected opponent/team)
# -----------------------------
notes_key = f"coach_notes__{TEAM_CODE_SAFE}__{team_key}"
if notes_key not in st.session_state:
    st.session_state[notes_key] = db_get_coach_notes(TEAM_CODE_SAFE, team_key)

player_notes_key = f"player_notes__{TEAM_CODE_SAFE}__{team_key}"
if player_notes_key not in st.session_state:
    st.session_state[player_notes_key] = db_get_player_notes(TEAM_CODE_SAFE, team_key)

with st.expander("📝 Coaches Scouting Notes (prints on Excel/CSV)", expanded=False):
    st.session_state[notes_key] = st.text_area(
        "Notes for THIS selected opponent/team:",
        value=st.session_state[notes_key],
        height=160,
        key=f"{notes_key}__box",
    )

    if st.button("💾 Save Notes", key=f"{notes_key}__save"):
        db_save_season_totals(
            TEAM_CODE_SAFE,
            team_key,
            season_team,
            season_players,
            games_played,
            archived_players,
            coach_notes=st.session_state[notes_key],
        )
        st.success("Notes saved for this opponent/team.")

notes_box_text = str(st.session_state.get(notes_key, "") or "").strip()

_csv_text = (df_season[visible_cols].to_csv(index=False) if (df_season is not None and not df_season.empty) else '')

# CSV can't merge cells, but we can push notes to the bottom for printing
if notes_box_text:
    import csv as _csv
    import io as _io
    cols = list(df_season.columns)
    blank_row = [""] * len(cols)

    # Build a footer row: COACH NOTES + note text
    if len(cols) == 0:
        cols = ["Player"]
    footer = [""] * len(cols)
    if len(cols) == 1:
        footer[0] = "COACH NOTES: " + notes_box_text.replace("\n", " ")
    else:
        footer[0] = "COACH NOTES:"
        footer[1] = notes_box_text.replace("\n", "  ")

    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow([])  # ensure we start on new line cleanly
    for _ in range(5):
        w.writerow(blank_row)
    w.writerow(footer)

    _csv_text = _csv_text.rstrip("\n") + "\n" + buf.getvalue().lstrip("\n")

csv_bytes = _csv_text.encode("utf-8")
safe_team = re.sub(r"[^A-Za-z0-9_-]+", "_", selected_team).strip("_")

# --- Download should match current Stat Edit view ---
# Build a safe visible_cols list (prevents NameError and handles empty seasons cleanly)
try:
    _vc = st.session_state.get(cols_key, list(df_season.columns))
except Exception:
    _vc = list(df_season.columns)

if not isinstance(_vc, (list, tuple)):
    _vc = list(df_season.columns)

visible_cols = [c for c in _vc if c in df_season.columns]

# Always keep Player if it exists
if "Player" in df_season.columns and "Player" not in visible_cols:
    visible_cols = ["Player"] + visible_cols

no_season_data = (df_season is None) or (getattr(df_season, "empty", True)) or (len(getattr(df_season, "columns", [])) == 0)

if no_season_data:
    st.info("No season stats to download yet. Process at least one game to generate season totals.")
    # Fallback so the app doesn't crash — still allows the page to load.
    df_xl = df_season.copy() if df_season is not None else None
else:
    df_xl = df_season[visible_cols].copy()

out = BytesIO()
with pd.ExcelWriter(out, engine="openpyxl") as writer:
    sheet_name = "Season"
    df_xl.to_excel(writer, index=False, sheet_name=sheet_name)

    ws = writer.book[sheet_name]
    ws.freeze_panes = "A2"


    header_font = Font(bold=True)
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    header_fill = PatternFill("solid", fgColor="D9E1F2")

    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = header_align
        cell.fill = header_fill

    for col_idx, col_name in enumerate(df_xl.columns, start=1):
        col_letter = get_column_letter(col_idx)
        max_len = len(str(col_name))
        sample = df_xl[col_name].astype(str).head(60).tolist()
        for v in sample:
            max_len = max(max_len, len(v))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 8), 22)

    start_row = 2
    start_col = 2  # numeric starts after Player
    end_row = ws.max_row
    end_col = ws.max_column

    if end_row >= start_row and end_col >= start_col:
        start_cell = f"{get_column_letter(start_col)}{start_row}"
        end_cell = f"{get_column_letter(end_col)}{end_row}"
        data_range = f"{start_cell}:{end_cell}"

        # gray out zeros (relative formula so each cell checks itself)
        zero_fill = PatternFill("solid", fgColor="EFEFEF")
        zero_rule = FormulaRule(
            formula=[f"{get_column_letter(start_col)}{start_row}=0"],
            fill=zero_fill,
            stopIfTrue=True,
        )
        if not no_season_data:
                ws.conditional_formatting.add(data_range, zero_rule)

        # heatmap
        heat_rule = ColorScaleRule(
            start_type="num", start_value=1, start_color="FFFFFF",
            mid_type="percentile", mid_value=50, mid_color="FFF2CC",
            end_type="max", end_color="F8CBAD",
        )
        ws.conditional_formatting.add(data_range, heat_rule)

    # highlight UNKNOWN > 0 (only if there are data rows)
    if end_row >= start_row and "UNKNOWN" in df_xl.columns:
        unk_idx = list(df_xl.columns).index("UNKNOWN") + 1
        unk_col = get_column_letter(unk_idx)
        unk_range = f"{unk_col}{start_row}:{unk_col}{end_row}"
        unk_fill = PatternFill("solid", fgColor="FFC7CE")
        unk_rule = CellIsRule(operator="greaterThan", formula=["0"], fill=unk_fill)
        ws.conditional_formatting.add(unk_range, unk_rule)

    # center numbers
    num_align = Alignment(horizontal="center", vertical="center")
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            if isinstance(cell.value, (int, float)):
                cell.alignment = num_align

    # -----------------------------
    # COACH NOTES BOX (EXCEL)
    # -----------------------------
    if notes_box_text:
        from openpyxl.styles import Border, Side

        top_row = ws.max_row + 6  # 5 blank rows after the last player
        left_col = 1
        right_col = ws.max_column
        box_height = 10

        ws.merge_cells(
            start_row=top_row,
            start_column=left_col,
            end_row=top_row + box_height - 1,
            end_column=right_col,
        )

        note_cell = ws.cell(row=top_row, column=left_col)
        note_cell.value = f"COACH NOTES:\n\n{notes_box_text}"
        note_cell.alignment = Alignment(wrap_text=True, vertical="top")

        # Make box rows taller
        for r in range(top_row, top_row + box_height):
            ws.row_dimensions[r].height = 22

        # Thick border around perimeter
        thick = Side(style="thick")
        for r in range(top_row, top_row + box_height):
            for c in range(left_col, right_col + 1):
                cur = ws.cell(row=r, column=c).border
                ws.cell(row=r, column=c).border = Border(
                    left=thick if c == left_col else cur.left,
                    right=thick if c == right_col else cur.right,
                    top=thick if r == top_row else cur.top,
                    bottom=thick if r == top_row + box_height - 1 else cur.bottom,
                )

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

# ✅ Individual archived toggle (separate from season table)
indiv_show_archived = st.checkbox(
    "Show archived players (not on current roster) — Individual",
    value=False,
    key=f"indiv_show_archived__{TEAM_CODE}__{re.sub(r'[^A-Za-z0-9_]+','_', selected_team)}",
)

# ✅ Candidate list for individual picker (include 0-stat players)
if indiv_show_archived:
    indiv_candidates = sorted(set(active_players + archived_list))
else:
    indiv_candidates = list(active_players)

selectable_players = list(indiv_candidates)

# -----------------------------
# INDIV STAT LIST + STAT EDIT
# -----------------------------
INDIV_TYPES = []
INDIV_TYPES += list(LOCATION_KEYS)
INDIV_TYPES += ["GB (total)", "FB (total)"]
INDIV_TYPES += list(COMBO_KEYS)

# running events (include SB/CS/DI + base-specific run keys)
INDIV_TYPES += ["SB", "CS", "DI"]
for _rk in RUN_KEYS:
    if _rk not in INDIV_TYPES:
        INDIV_TYPES.append(_rk)

# de-dupe while preserving order
_seen = set()
INDIV_TYPES = [x for x in INDIV_TYPES if not (x in _seen or _seen.add(x))]

indiv_key = f"indiv_types__{TEAM_CODE}__{re.sub(r'[^A-Za-z0-9_]+','_', selected_team)}"
if indiv_key not in st.session_state:
    st.session_state[indiv_key] = list(INDIV_TYPES)

if not selectable_players:
    st.info("No hitters found for this roster yet.")
else:
    # Tight row: hitter select (left) + Stat Edit (right)
    i_left, i_right = st.columns([8, 2])
    with i_left:
        selected_player = st.selectbox("Choose a hitter:", selectable_players, key=f"indiv_player__{TEAM_CODE}__{re.sub(r'[^A-Za-z0-9_]+','_', selected_team)}")

    with i_right:
        if hasattr(st, "popover"):
            _stat_edit_container = st.popover("Stat Edit")
        else:
            _stat_edit_container = st.expander("Stat Edit", expanded=False)

        with _stat_edit_container:
            st.caption("Toggle which stats show in the individual table + downloads")
            flt = st.text_input("Search", value="", placeholder="Type to filter stats...", key=f"{indiv_key}__flt")

            b1, b2, b3 = st.columns([1, 1, 2])
            with b1:
                all_clicked = st.button("All", key=f"{indiv_key}__all", use_container_width=True)
            with b2:
                none_clicked = st.button("None", key=f"{indiv_key}__none", use_container_width=True)
            with b3:
                st.caption(" ")

            # Make All/None actually drive the checkbox states (Streamlit checkboxes are keyed)
            if all_clicked or none_clicked:
                for _t in INDIV_TYPES:
                    _safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(_t))
                    st.session_state[f"{indiv_key}__cb__{_safe}"] = True if all_clicked else False
                st.session_state[indiv_key] = list(INDIV_TYPES) if all_clicked else []
                st.rerun()

            picked_set = set(st.session_state.get(indiv_key, []))

            view_types = INDIV_TYPES
            if flt.strip():
                q = flt.strip().lower()
                view_types = [t for t in INDIV_TYPES if q in str(t).lower()]

            colA, colB, colC = st.columns(3)
            grid = [colA, colB, colC]
            for i, t in enumerate(view_types):
                target = grid[i % 3]
                safe_t = re.sub(r"[^A-Za-z0-9_]+", "_", str(t))
                cur_val = st.session_state.get(f"{indiv_key}__cb__{safe_t}", t in picked_set)
                new_val = target.checkbox(str(t), value=cur_val, key=f"{indiv_key}__cb__{safe_t}")
                if new_val:
                    picked_set.add(t)
                else:
                    picked_set.discard(t)

            st.session_state[indiv_key] = [t for t in INDIV_TYPES if t in picked_set]

    # Current individual view types
    indiv_types_selected = st.session_state.get(indiv_key, list(INDIV_TYPES))
    if not indiv_types_selected:
        st.warning("No stats selected. Use Stat Edit to choose at least one stat.")
        indiv_types_selected = list(INDIV_TYPES)

    # -----------------------------
    # DISPLAY (selected player)
    # -----------------------------
    stats = season_players[selected_player]
    indiv_rows = []
    for t in indiv_types_selected:
        if t == "GB (total)":
            indiv_rows.append({"Type": t, "Count": stats.get("GB", 0)})
        elif t == "FB (total)":
            indiv_rows.append({"Type": t, "Count": stats.get("FB", 0)})
        else:
            indiv_rows.append({"Type": t, "Count": stats.get(t, 0)})

    st.table(indiv_rows)

    # -----------------------------
    # DOWNLOADS (match Stat Edit)
    #   - Excel: one sheet per player
    #   - CSV: long format (Player, Type, Count)
    # -----------------------------
    dl_a, dl_b = st.columns([1, 1])
    safe_team_ind = re.sub(r"[^A-Za-z0-9_-]+", "_", selected_team).strip("_")

    def _sheet_name(name: str) -> str:
        # Excel sheet name max = 31, cannot contain : \ / ? * [ ]
        s = re.sub(r"[:\\/\?\*\[\]]+", " ", str(name)).strip()
        s = re.sub(r"\s+", " ", s)
        return s[:31] if s else "Player"

    # Excel bytes (one sheet per player)
    excel_out = BytesIO()
    with pd.ExcelWriter(excel_out, engine="openpyxl") as writer:
        # Load per-player notes (stored as JSON string in session_state / DB meta)
        _pn_key = f"player_notes__{TEAM_CODE_SAFE}__{team_key}"
        try:
            _player_notes_dict = json.loads(st.session_state.get(_pn_key, "") or "{}")
            if not isinstance(_player_notes_dict, dict):
                _player_notes_dict = {}
        except Exception:
            _player_notes_dict = {}
        used = set()
        for p in selectable_players:
            st_p = season_players.get(p, {})
            rows_p = []
            for t in indiv_types_selected:
                if t == "GB (total)":
                    rows_p.append({"Type": t, "Count": st_p.get("GB", 0)})
                elif t == "FB (total)":
                    rows_p.append({"Type": t, "Count": st_p.get("FB", 0)})
                else:
                    rows_p.append({"Type": t, "Count": st_p.get(t, 0)})

            df_p = pd.DataFrame(rows_p)
            sn = _sheet_name(p)

            base = sn
            k = 1
            while sn in used:
                suffix = f"_{k}"
                sn = (base[:31 - len(suffix)] + suffix) if len(base) + len(suffix) > 31 else (base + suffix)
                k += 1
            used.add(sn)

            df_p.to_excel(writer, index=False, sheet_name=sn, startrow=12)
            ws = writer.book[sn]
            # Local styles for notes + stat split (avoid NameError due to ordering)
            FONT_NAME = "Arial"  # Excel-safe font
            _thin = Side(style="thin", color="9E9E9E")
            _border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
            _notes_fill = PatternFill("solid", fgColor="D9EAD3")  # light green
            _header_fill = PatternFill("solid", fgColor="D9E1F2")  # light blue-gray
            _header_font = Font(name=FONT_NAME, size=12, bold=True)
            _small_font = Font(name=FONT_NAME, size=11)

            # Coaches Notes (per player) — printable, saved into Excel
            # Header row
            ws.merge_cells("A3:B3")
            ws["A3"].value = "Coaches Notes"
            for c in range(1, 3):
                cell = ws.cell(row=3, column=c)
                cell.font = _header_font
                cell.fill = _header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = _border

            player_note_text = _player_notes_dict.get(p, "")
            # Body (editable note text)
            ws.merge_cells("A4:B10")
            ws["A4"].value = player_note_text or ""
            for r in range(4, 11):
                for c in range(1, 3):
                    cell = ws.cell(row=r, column=c)
                    cell.font = _small_font
                    cell.fill = _notes_fill
                    cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
                    cell.border = _border
            ws["A2"].value = "GB / FB by position"
            ws["A2"].font = _header_font
            ws["A2"].alignment = Alignment(horizontal="left", vertical="center")

            # Split Selected Stat Totals into two columns to reduce white space.
            # (pandas wrote the full table at A13:B...)
            stats_start = 13  # header row
            last = stats_start
            while ws.cell(row=last + 1, column=1).value not in (None, ""):
                last += 1
                if last > 300:
                    break
            n_rows = max(0, last - stats_start)  # number of data rows
            if n_rows > 10:
                split = (n_rows + 1) // 2

                            # Right table (D-H) — two-column layout on one printable page
            split = (n_rows + 1) // 2  # roughly half rows on left, half on right

            # Headers (row stats_start): merge D-F for Type, G-H for Count
            ws.merge_cells(start_row=stats_start, start_column=4, end_row=stats_start, end_column=6)  # D-F
            ws.merge_cells(start_row=stats_start, start_column=7, end_row=stats_start, end_column=8)  # G-H
            ws.cell(row=stats_start, column=4, value="Type")
            ws.cell(row=stats_start, column=7, value="Count")

            for c in range(4, 7):
                cell = ws.cell(row=stats_start, column=c)
                cell.font = _header_font
                cell.fill = _header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = _border
            for c in range(7, 9):
                cell = ws.cell(row=stats_start, column=c)
                cell.font = _header_font
                cell.fill = _header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = _border

            # Move bottom half (from left A/B) into right table (D/G)
            for i in range(split + 1, n_rows + 1):
                src_r = stats_start + i
                dst_r = stats_start + (i - split)

                tval = ws.cell(row=src_r, column=1).value
                cval = ws.cell(row=src_r, column=2).value

                ws.cell(row=dst_r, column=4, value=tval)
                ws.cell(row=dst_r, column=7, value=cval)

                # clear originals on left
                ws.cell(row=src_r, column=1).value = None
                ws.cell(row=src_r, column=2).value = None

            # Format right table rows: merge D-F (Type) and G-H (Count)
            for r in range(stats_start + 1, stats_start + split + 1):
                ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=6)  # D-F
                ws.merge_cells(start_row=r, start_column=7, end_row=r, end_column=8)  # G-H
                for c in range(4, 9):  # D-H
                    cell = ws.cell(row=r, column=c)
                    cell.font = _small_font
                    cell.alignment = Alignment(horizontal="left" if c <= 6 else "center", vertical="center")
                    cell.border = _border
                    cell.fill = PatternFill("solid", fgColor="FFFFFF")
            ws.freeze_panes = "A14"
            _totals = st_p  # per-player season totals dict (includes GB-*/FB-* buckets)

            

            # --- MLB-style field summary (clean, symmetrical, print-ready) ---
            # We draw a simple "field" layout and place each position as a 2-column GB/FB block.
            # Heatmap fill is applied separately to GB and FB cells.
            FIELD_TOP_ROW = 2
            FIELD_LEFT_COL = 3   # Column C
            FIELD_RIGHT_COL = 12 # Column L
            FIELD_BOTTOM_ROW = 11

            # Colors / styles
            title_font = Font(name=FONT_NAME, size=16, bold=True)
            hdr_font = Font(name=FONT_NAME, size=11, bold=True)
            cell_font = Font(name=FONT_NAME, size=11, bold=False)
            small_font = Font(name=FONT_NAME, size=10, color='444444')
            gbfb_font = Font(name=FONT_NAME, size=11, bold=True)
            gray_fill = PatternFill("solid", fgColor="F2F2F2")
            grass_fill = PatternFill("solid", fgColor="D9EAD3")  # lighter green
            white_fill = PatternFill("solid", fgColor="FFFFFF")
            thin = Side(style="thin", color="9E9E9E")
            box_border = Border(left=thin, right=thin, top=thin, bottom=thin)

            # Title row
            ws.merge_cells(start_row=1, start_column=3, end_row=1, end_column=12)
            ws.cell(row=1, column=3, value=f"Individual Spray Summary — {p}").font = title_font
            ws.cell(row=1, column=3).alignment = Alignment(horizontal="center", vertical="center")

            # Section label
            ws.cell(row=2, column=1, value="GB / FB by position").font = hdr_font
            ws.cell(row=2, column=1).alignment = Alignment(horizontal="left", vertical="center")

            # Field background
            for r in range(FIELD_TOP_ROW, FIELD_BOTTOM_ROW + 1):
                for c in range(FIELD_LEFT_COL, FIELD_RIGHT_COL + 1):
                    cell = ws.cell(row=r, column=c)
                    cell.fill = grass_fill
                    # keep background clean (no borders on grass)
                    cell.border = Border()

            # Column widths tuned so the field fits clean on portrait print
            for c in range(1, 20):
                col_letter = get_column_letter(c)
                if 3 <= c <= 12:
                    ws.column_dimensions[col_letter].width = 6.0
                elif c == 1:
                    ws.column_dimensions[col_letter].width = 18.0
                else:
                    ws.column_dimensions[col_letter].width = 10.0

            # Row heights for symmetry
            for r in range(1, 45):
                if 2 <= r <= 11:
                    ws.row_dimensions[r].height = 20
                else:
                    ws.row_dimensions[r].height = 18

            # Helper: heat fill using the same palette logic as team sheet (white->orange->red)
            def _heat_fill(value: int, vmax: int):
                # clamp
                try:
                    v = int(value)
                except Exception:
                    v = 0
                if vmax <= 0:
                    return white_fill
                ratio = max(0.0, min(1.0, v / float(vmax)))
                # simple 3-stop gradient: white -> FCE5CD -> F4CCCC
                # use two segments
                if ratio <= 0.5:
                    # white to light orange
                    t = ratio / 0.5
                    c0 = (255, 255, 255)
                    c1 = (252, 229, 205)
                else:
                    # light orange to light red
                    t = (ratio - 0.5) / 0.5
                    c0 = (252, 229, 205)
                    c1 = (244, 204, 204)
                rgb = tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
                hex_rgb = "%02X%02X%02X" % rgb
                return PatternFill("solid", fgColor=hex_rgb)

            # Build per-position totals (GB and FB separately)
            positions = ["LF", "CF", "RF", "3B", "SS", "2B", "1B", "P"]
            gb_totals = {p: int(_totals.get(p, 0) or 0) if p in _totals else 0 for p in positions}
            fb_totals = {p: int(_totals.get(f"FB-{p}", 0) or 0) if f"FB-{p}" in _totals else 0 for p in positions}

            gb_vmax = max([gb_totals[p] for p in positions] + [0])
            fb_vmax = max([fb_totals[p] for p in positions] + [0])

            def pos_box(pos: str, top_row: int, left_col: int):
                """Draw a symmetric 2-col position block:
                Header merged across 2 cols, then GB (left) + FB (right).
                """
                # Header (merged)
                ws.merge_cells(start_row=top_row, start_column=left_col, end_row=top_row, end_column=left_col + 1)
                h = ws.cell(row=top_row, column=left_col, value=pos)
                h.font = hdr_font
                h.fill = gray_fill
                h.alignment = Alignment(horizontal="center", vertical="center")
                # Data row cells
                gb_cell = ws.cell(row=top_row + 1, column=left_col, value=f"GB {gb_totals.get(pos, 0)}")
                fb_cell = ws.cell(row=top_row + 1, column=left_col + 1, value=f"FB {fb_totals.get(pos, 0)}")
                gb_cell.font = cell_font
                fb_cell.font = cell_font
                gb_cell.alignment = Alignment(horizontal="center", vertical="center")
                fb_cell.alignment = Alignment(horizontal="center", vertical="center")
                gb_cell.fill = _heat_fill(gb_totals.get(pos, 0), gb_vmax)
                fb_cell.fill = _heat_fill(fb_totals.get(pos, 0), fb_vmax)

                # Borders (header + both cells)
                for rr in (top_row, top_row + 1):
                    for cc in (left_col, left_col + 1):
                        ws.cell(row=rr, column=cc).border = box_border

            # Layout map inside C2:L11 (matches a baseball field feel & stays symmetric)
            # (row, col) refers to the HEADER cell's top-left (two-column block)
            layout = {
                "CF": (2, 7),  # G-H
                "LF": (3, 5),  # E-F
                "RF": (3, 9),  # I-J
                "SS": (6, 5),  # E-F
                "2B": (6, 9),  # I-J
                "3B": (8, 3),  # C-D
                "P":  (8, 7),  # G-H
                "1B": (8, 11), # K-L
            }

            for pos, (r0, c0) in layout.items():
                pos_box(pos, r0, c0)


            # Keep grid tidy
            for rr in range(2, 9):
                ws.row_dimensions[rr].height = 22

            ws["A12"] = "Selected Stat Totals"
            # Avoid NameError from scoped font variables: set the font inline
            ws["A12"].font = Font(name=FONT_NAME, size=10, color="444444")
            ws["A12"].alignment = Alignment(horizontal="left", vertical="center")


            header_font = Font(bold=True)
            header_align = Alignment(horizontal="center", vertical="center")
            header_fill = PatternFill("solid", fgColor="EDEDED")
            for cell in ws[13]:
                cell.font = header_font
                cell.alignment = header_align
                cell.fill = header_fill

            ws.column_dimensions["A"].width = 18
            ws.column_dimensions["B"].width = 10

    excel_bytes = excel_out.getvalue()

    # CSV bytes (long format)
    long_rows = []
    for p in selectable_players:
        st_p = season_players.get(p, {})
        for t in indiv_types_selected:
            if t == "GB (total)":
                cnt = st_p.get("GB", 0)
            elif t == "FB (total)":
                cnt = st_p.get("FB", 0)
            else:
                cnt = st_p.get(t, 0)
            long_rows.append({"Player": p, "Type": t, "Count": cnt})

    df_long = pd.DataFrame(long_rows)
    csv_long_bytes = df_long.to_csv(index=False).encode("utf-8")

    with dl_a:
        st.download_button(
            label="📥 Download Individual Spray (Excel - sheets per player)",
            data=excel_bytes,
            file_name=f"{TEAM_CODE}_{safe_team_ind}_Individual_Spray.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with dl_b:
        st.download_button(
            label="📄 Download Individual Spray (CSV)",
            data=csv_long_bytes,
            file_name=f"{TEAM_CODE}_{safe_team_ind}_Individual_Spray.csv",
            mime="text/csv",
        )
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
        © 2026 RP Spray Analytics. All rights reserved.<br>
        Proprietary software. Unauthorized copying, redistribution, or reverse engineering prohibited.
    </div>
    """,
    unsafe_allow_html=True,
)












































































