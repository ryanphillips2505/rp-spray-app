# -----------------------------
# COACH NOTES BOX (EXCEL)
# -----------------------------
if notes_box_text:
    top_row = ws.max_row + 6
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
    note_cell.value = f"COACHES NOTES:\n\n{notes_box_text}"
    note_cell.font = Font(size=12)  # size 12
    note_cell.alignment = Alignment(wrap_text=True, vertical="top")

    for rr in range(top_row, top_row + box_height):
        ws.row_dimensions[rr].height = 22

    thick = Side(style="thick", color="000000")
    for rr in range(top_row, top_row + box_height):
        for cc in range(left_col, right_col + 1):
            cur = ws.cell(row=rr, column=cc).border
            ws.cell(row=rr, column=cc).border = Border(
                left=thick if cc == left_col else cur.left,
                right=thick if cc == right_col else cur.right,
                top=thick if rr == top_row else cur.top,
                bottom=thick if rr == top_row + box_height - 1 else cur.bottom,
            )

# ==========================================================
# INDIVIDUAL PLAYER TABS (ACTIVE ROSTER ONLY)
# - ALWAYS runs (not inside notes_box_text)
# - One sheet per hitter
# - Scouting-sheet layout (NO TABLE)
# - Heatmap matches TEAM (orange → red)
# ==========================================================

from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

def _safe_sheet_name(name: str) -> str:
    name = str(name or "").strip()
    name = re.sub(r'[:\\/?*\[\]]', "", name)
    name = re.sub(r"\s+", " ", name)
    return name[:31] if name else "Player"

def _unique_sheet_name(wb, base: str) -> str:
    if base not in wb.sheetnames:
        return base
    i = 2
    while True:
        suffix = f" {i}"
        cand = (base[:31 - len(suffix)] + suffix)[:31]
        if cand not in wb.sheetnames:
            return cand
        i += 1

def _safe_float(x):
    try:
        return float(x)
    except Exception:
        return 0.0

# --- orange → red bins (match TEAM heat map style) ---
gb_bins = [
    (0.00, 0.05, None),
    (0.05, 0.15, PatternFill("solid", fgColor="FFE5CC")),
    (0.15, 0.30, PatternFill("solid", fgColor="FFCC99")),
    (0.30, 0.45, PatternFill("solid", fgColor="FFB266")),
    (0.45, 0.60, PatternFill("solid", fgColor="FF7A45")),
    (0.60, 1.01, PatternFill("solid", fgColor="B71C1C")),
]
fb_bins = gb_bins  # same palette for FB

def _fill(v, bins):
    x = _safe_float(v)
    if x <= 0:
        return None
    if x > 1:
        x = 1.0
    for lo, hi, f in bins:
        if f is None:
            continue
        if lo <= x < hi:
            return f
    return None

# --- styles ---
center = Alignment(horizontal="center", vertical="center")
thin = Side(style="thin", color="000000")
thick = Side(style="thick", color="000000")
box = Border(left=thin, right=thin, top=thin, bottom=thin)
thick_bottom = Border(bottom=thick)

title_font = Font(bold=True, size=20)
label_font = Font(bold=True, size=12)
val_font = Font(bold=True, size=12)

def _clear_sheet_safely(ws_):
    try:
        for rng in list(ws_.merged_cells.ranges):
            ws_.unmerge_cells(str(rng))
    except Exception:
        pass
    for r in range(1, 80):
        for c in range(1, 30):
            cell = ws_.cell(row=r, column=c)
            if isinstance(cell, MergedCell):
                continue
            cell.value = None
            cell.border = Border()
            cell.fill = PatternFill()
            cell.alignment = Alignment()

def _merge_label(ws_, rng, text):
    ws_.merge_cells(rng)
    c = ws_[rng.split(":")[0]]
    c.value = text
    c.font = label_font
    c.alignment = center
    c.border = box

def _set_pct_cell(ws_, addr, v, is_gb=True):
    c = ws_[addr]
    c.value = _safe_float(v)
    c.number_format = "0%"
    c.font = val_font
    c.alignment = center
    c.border = box
    f = _fill(c.value, gb_bins if is_gb else fb_bins)
    if f:
        c.fill = f

def _build_player_scout_sheet(ws_, player_name, stats):
    gb = int(stats.get("GB", 0) or 0)
    fb = int(stats.get("FB", 0) or 0)
    bip = gb + fb

    vals = {}
    for ck in COMBO_KEYS:
        raw = float(stats.get(ck, 0) or 0)
        vals[ck] = (raw / bip) if bip else 0.0
    vals["BIP"] = int(bip)

    _clear_sheet_safely(ws_)

    # widths
    ws_.column_dimensions["A"].width = 5
    ws_.column_dimensions["B"].width = 5
    for col in ["C","D","E","F","G","H","I"]:
        ws_.column_dimensions[col].width = 13

    # heights
    ws_.row_dimensions[1].height = 30
    for rr in range(2, 19):
        ws_.row_dimensions[rr].height = 20
    ws_.row_dimensions[16].height = 10

    # title
    ws_.merge_cells("A1:I1")
    t = ws_["A1"]
    t.value = str(player_name)
    t.font = title_font
    t.alignment = center
    t.border = thick_bottom

    # CF
    _merge_label(ws_, "E3:F3", "CF")
    _set_pct_cell(ws_, "E4", vals.get("GB-CF", 0), is_gb=True)
    _set_pct_cell(ws_, "F4", vals.get("FB-CF", 0), is_gb=False)

    # LF
    _merge_label(ws_, "C5:D5", "LF")
    _set_pct_cell(ws_, "C6", vals.get("GB-LF", 0), is_gb=True)
    _set_pct_cell(ws_, "D6", vals.get("FB-LF", 0), is_gb=False)

    # RF
    _merge_label(ws_, "G5:H5", "RF")
    _set_pct_cell(ws_, "G6", vals.get("GB-RF", 0), is_gb=True)
    _set_pct_cell(ws_, "H6", vals.get("FB-RF", 0), is_gb=False)

    # SS (MERGED E7:F7)
    _merge_label(ws_, "E7:F7", "SS")
    _set_pct_cell(ws_, "E8", vals.get("GB-SS", 0), is_gb=True)
    _set_pct_cell(ws_, "F8", vals.get("FB-SS", 0), is_gb=False)

    # 2B
    ws_["G7"].value = "2B"
    ws_["G7"].font = label_font
    ws_["G7"].alignment = center
    ws_["G7"].border = box
    _set_pct_cell(ws_, "G8", vals.get("GB-2B", 0), is_gb=True)
    _set_pct_cell(ws_, "H8", vals.get("FB-2B", 0), is_gb=False)

    # 3B
    _merge_label(ws_, "C9:D9", "3B")
    _set_pct_cell(ws_, "C10", vals.get("GB-3B", 0), is_gb=True)
    _set_pct_cell(ws_, "D10", vals.get("FB-3B", 0), is_gb=False)

    # 1B
    _merge_label(ws_, "G9:H9", "1B")
    _set_pct_cell(ws_, "G10", vals.get("GB-1B", 0), is_gb=True)
    _set_pct_cell(ws_, "H10", vals.get("FB-1B", 0), is_gb=False)

    # P
    _merge_label(ws_, "E11:F11", "P")
    _set_pct_cell(ws_, "E12", vals.get("GB-P", 0), is_gb=True)
    _set_pct_cell(ws_, "F12", vals.get("FB-P", 0), is_gb=False)

    # divider row 16
    for col in ["A","B","C","D","E","F","G","H","I"]:
        cell = ws_[f"{col}16"]
        cell.fill = PatternFill("solid", fgColor="000000")
        cell.border = Border(top=thick, bottom=thick)

    # BIP box
    ws_.merge_cells("C17:D17")
    b1 = ws_["C17"]
    b1.value = "BIP"
    b1.font = Font(bold=True, size=12)
    b1.alignment = center
    b1.fill = PatternFill("solid", fgColor="E5E7EB")
    b1.border = box

    ws_.merge_cells("C18:D18")
    b2 = ws_["C18"]
    b2.value = int(vals.get("BIP", 0) or 0)
    b2.font = Font(bold=True, size=14)
    b2.alignment = center
    b2.border = box

    # print setup
    ws_.print_area = "A1:I40"
    ws_.page_setup.orientation = ws_.ORIENTATION_PORTRAIT
    ws_.page_setup.fitToWidth = 1
    ws_.page_setup.fitToHeight = 1
    ws_.sheet_properties.pageSetUpPr.fitToPage = True
    ws_.print_options.horizontalCentered = True
    ws_.page_margins.left = 0.25
    ws_.page_margins.right = 0.25
    ws_.page_margins.top = 0.35
    ws_.page_margins.bottom = 0.35
    ws_.page_margins.header = 0.15
    ws_.page_margins.footer = 0.15
    ws_.page_setup.paperSize = ws_.PAPERSIZE_LETTER

# roster fallback: if current_roster is empty, use players from df_export
try:
    roster_source = list(current_roster) if current_roster else list(df_export["Player"].astype(str).tolist())
except Exception:
    roster_source = list(df_export["Player"].astype(str).tolist()) if "Player" in df_export.columns else []

active_for_tabs = sorted(set(roster_source), key=lambda x: str(x).lower())

for player_name in active_for_tabs:
    stats = season_players.get(player_name, empty_stat_dict())
    base = _safe_sheet_name(player_name)
    sheet = _unique_sheet_name(writer.book, base)

    ws_player = writer.book.create_sheet(title=sheet)
    _build_player_scout_sheet(ws_player, player_name, stats)
