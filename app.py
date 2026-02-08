# RP Spray Analytics
# Copyright © 2026 Ryan Phillips
# All rights reserved.

import streamlit as st

# ✅ MUST be first Streamlit call (no st.write / st.markdown before this)
st.set_page_config(page_title="RP Spray Analytics", layout="wide", page_icon="⚾")

# ✅ Safe per-session nonce (only AFTER st exists)
st.session_state.setdefault("_rp_run_nonce", 0)
st.session_state["_rp_run_nonce"] += 1
_RP_RUN_NONCE = st.session_state["_rp_run_nonce"]

# ✅ If you want a boot marker, do it AFTER set_page_config
# (You can keep or remove this; it won't break anything now.)
st.write("BOOT MARKER 2026-02-07 A")

# ---- normal imports below this line ----
import os
import json
import base64
import re
import hashlib
import httpx
import time
from datetime import datetime, timezone
import uuid
import traceback
from typing import Optional, Tuple

DEBUG = False

# Unique per-run id for widget keys
RUN_ID = uuid.uuid4().hex
