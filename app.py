# RP Spray Analytics
# Copyright © 2026 Ryan Phillips
# All rights reserved.

import streamlit as st

st.set_page_config(page_title="RP Spray Analytics", layout="wide", page_icon="⚾")
st.write("BOOT MARKER A")   # keep

# nonce
st.session_state.setdefault("_rp_run_nonce", 0)
st.session_state["_rp_run_nonce"] += 1

st.write("BOOT MARKER B")   # ✅ ADD THIS


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


