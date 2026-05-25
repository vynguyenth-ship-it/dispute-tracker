"""
Dispute Management — Streamlit Web UI
Data source: Google Sheets (no local SQLite)
Deploy: streamlit run streamlit_app.py  |  Streamlit Cloud
"""
import base64
import json as _json
import threading
import urllib.parse
from datetime import datetime, date, timedelta

import streamlit.components.v1 as _components

import pandas as pd
import streamlit as st

from dispute_tracker import (
    load_cases, update_case, archive_case, archive_old_completed,
    TZ, CONFIG,
)

# ── Constants ─────────────────────────────────────────────────────────────────
QUEUES   = ["Dispute", "Update Details", "Invoice", "Internal Invoice", "Others"]
STATUSES = ["New", "In Progress", "Completed", "Rejected"]
REJECT_REASONS = ["Duplicated", "Not AR case", "More detail required"]
GROUP_EMAIL = CONFIG["GROUP_EMAIL"]
PAGE_SIZE   = 30

QUEUE_ICONS = {
    "Dispute": "🚨", "Update Details": "📝",
    "Invoice": "🧾", "Internal Invoice": "🏢", "Others": "📨",
}

GRAB_GREEN = "#00B14F"
GRAB_DARK  = "#00802E"
GRAB_LIGHT = "#E8F7EE"

STATUS_COLOR = {
    "New":         "#F5A623",
    "In Progress": "#4A90D9",
    "Completed":   "#00B14F",
    "Rejected":    "#E74C3C",
}


# ── Page config & CSS ─────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Dispute Management",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded",
)

THEME_CSS = f"""
<style>
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"] {{
    background-color: #ffffff !important;
    font-family: "Google Sans", -apple-system, sans-serif;
}}
[data-testid="stHeader"] {{ background-color: {GRAB_GREEN}; }}
[data-testid="stSidebar"] {{
    background-color: #ffffff !important;
    border-right: 2px solid {GRAB_GREEN};
}}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p {{ color: {GRAB_DARK} !important; font-weight: 500 !important; }}
[data-testid="stSidebar"] [data-baseweb="input"] input,
[data-testid="stSidebar"] [data-baseweb="select"] > div {{
    border-color: {GRAB_GREEN} !important; border-radius: 6px !important; background: #ffffff !important;
}}
[data-testid="stSidebar"] [data-baseweb="tag"] {{
    background-color: {GRAB_GREEN} !important; color: white !important; border-radius: 4px !important;
}}
[data-testid="stSidebar"] .stDateInput [data-baseweb="input"] {{
    border-color: {GRAB_GREEN} !important; border-radius: 6px !important;
}}
[data-testid="stMetric"] {{
    background: #ffffff; border: 1px solid #d4edda;
    border-left: 4px solid {GRAB_GREEN}; border-radius: 8px; padding: 12px 16px !important;
}}
[data-testid="stMetricValue"] {{ color: {GRAB_GREEN} !important; font-size: 28px !important; }}
[data-testid="stMetricLabel"] {{ color: #555 !important; font-size: 13px !important; }}
div[data-testid="stButton"] > button {{
    height: 38px !important; font-size: 13px !important; font-weight: 600 !important;
    background: #ffffff !important; border: 1.5px solid {GRAB_GREEN} !important;
    color: {GRAB_GREEN} !important; border-radius: 8px !important; transition: all 0.15s;
}}
div[data-testid="stButton"] > button:hover {{
    background: {GRAB_LIGHT} !important; border-color: {GRAB_DARK} !important;
}}
div[data-testid="stButton"] > button[kind="primary"] {{
    background: {GRAB_GREEN} !important; color: white !important; border: none !important;
}}
div[data-testid="stButton"] > button[kind="primary"]:hover {{ background: {GRAB_DARK} !important; }}
[data-testid="stLinkButton"] > a {{
    background: #ffffff !important; color: {GRAB_GREEN} !important;
    border: 1.5px solid {GRAB_GREEN} !important; border-radius: 8px !important;
    padding: 8px 16px !important; font-weight: 600 !important; font-size: 13px !important;
    text-decoration: none !important; display: inline-block !important;
    height: 38px !important; line-height: 22px !important;
}}
[data-testid="stLinkButton"] > a:hover {{ background: {GRAB_LIGHT} !important; }}
[data-testid="stDataFrame"] th {{
    background-color: {GRAB_LIGHT} !important; color: {GRAB_GREEN} !important; font-weight: 600 !important;
}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
    color: {GRAB_GREEN} !important; border-bottom: 2px solid {GRAB_GREEN} !important; font-weight: 600 !important;
}}
[data-testid="stVerticalBlockBorderWrapper"] {{
    border: 1px solid #e8e8e8 !important; border-radius: 8px !important; background: #ffffff !important;
}}
hr {{ border-color: #e8e8e8 !important; }}

</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

# ── Auth ──────────────────────────────────────────────────────────────────────
GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_SCOPES      = "openid email profile"


def _get_oauth_config():
    s = st.secrets
    return {
        "client_id":     s["google_oauth"]["client_id"],
        "client_secret": s["google_oauth"]["client_secret"],
        "redirect_uri":  s["google_oauth"]["redirect_uri"],
    }


def _require_login():
    import requests as req
    if st.session_state.get("email"):
        return st.session_state["email"]

    cfg    = _get_oauth_config()
    params = st.query_params

    if "code" in params:
        try:
            resp = req.post(GOOGLE_TOKEN_URL, data={
                "code":          params["code"],
                "client_id":     cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "redirect_uri":  cfg["redirect_uri"],
                "grant_type":    "authorization_code",
            }, timeout=10)
            parts = resp.json().get("id_token", "").split(".")
            if len(parts) >= 2:
                info = _json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
                st.session_state["email"] = info.get("email", "unknown@grabtaxi.com")
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.error(f"Login failed: {e}")
            st.stop()

    auth_url = (
        f"{GOOGLE_AUTH_URL}?"
        + urllib.parse.urlencode({
            "client_id":     cfg["client_id"],
            "redirect_uri":  cfg["redirect_uri"],
            "response_type": "code",
            "scope":         AUTH_SCOPES,
            "access_type":   "online",
            "prompt":        "select_account",
        })
    )
    st.markdown(f"""
    <div style="max-width:420px;margin:80px auto;text-align:center;">
        <div style="background:{GRAB_GREEN};border-radius:16px;padding:40px 32px;">
            <div style="font-size:48px;margin-bottom:8px;">🟢</div>
            <h1 style="color:white;font-size:24px;margin:0 0 4px;">Dispute Management</h1>
            <p style="color:rgba(255,255,255,0.85);font-size:14px;margin:0 0 28px;">
                account.receivable.vn@grabtaxi.com
            </p>
        </div>
        <p style="color:#555;margin-top:24px;font-size:14px;">
            Sign in with your <strong>@grabtaxi.com</strong> account to continue.
        </p>
    </div>
    """, unsafe_allow_html=True)
    col = st.columns([1, 2, 1])[1]
    col.link_button("Sign in with Google", auth_url, use_container_width=True)
    st.stop()


# ── Optimistic update helpers ─────────────────────────────────────────────────
def _apply_action(cid: str, **fields):
    """Update session state immediately, write to sheet in background thread."""
    if "optimistic" not in st.session_state:
        st.session_state["optimistic"] = {}
    st.session_state["optimistic"][cid] = {
        **st.session_state["optimistic"].get(cid, {}),
        **fields,
    }
    threading.Thread(target=update_case, args=(cid,), kwargs=fields, daemon=True).start()
    st.cache_data.clear()
    st.rerun()


def _get_active_cases() -> pd.DataFrame:
    """Load cases from sheet (cached) then overlay any optimistic updates."""
    df = _load_active_cases_cached()
    optimistic = st.session_state.get("optimistic", {})
    if optimistic:
        df = df.copy()
        for cid, fields in optimistic.items():
            mask = df["case_id"] == cid
            for k, v in fields.items():
                df.loc[mask, k] = v
    return df


# ── Data helpers ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def _load_active_cases_cached() -> pd.DataFrame:
    try:
        rows = load_cases(CONFIG["SHEET_TAB_NAME"])
    except Exception as e:
        st.error(f"Sheet load error: {e}")
        rows = []
    if not rows:
        return pd.DataFrame(columns=[
            "case_id", "date_received", "sender", "subject", "queue",
            "status", "assigned_to", "assigned_at", "completed_at", "email_link",
        ])
    return pd.DataFrame(rows)


@st.cache_data(ttl=60)
def load_archived_cases() -> pd.DataFrame:
    try:
        rows = load_cases(CONFIG["ARCHIVE_TAB_NAME"])
    except Exception as e:
        st.error(f"Sheet load error: {e}")
        rows = []
    if not rows:
        return pd.DataFrame(columns=[
            "case_id", "date_received", "sender", "subject", "queue",
            "status", "assigned_to", "assigned_at", "completed_at", "email_link",
        ])
    return pd.DataFrame(rows)


def _parse_date(date_str: str):
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except Exception:
            continue
    return None


# ── Page 1: Dashboard ─────────────────────────────────────────────────────────
def page_home():
    logged_in_user = _require_login()

    st.markdown(f"""
    <div style="background:{GRAB_GREEN};border-radius:10px;padding:14px 20px;
                display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <div>
            <span style="color:white;font-size:20px;font-weight:700;">📊 Dispute Management</span>
            <span style="color:rgba(255,255,255,0.8);font-size:12px;margin-left:12px;">{GROUP_EMAIL}</span>
        </div>
        <span style="color:rgba(255,255,255,0.9);font-size:13px;">👤 {logged_in_user}</span>
    </div>
    """, unsafe_allow_html=True)

    df     = _get_active_cases()
    df_arc = load_archived_cases()

    today_str    = datetime.now(TZ).strftime("%d/%m/%Y")
    n_total      = len(df)
    n_new        = int((df["status"] == "New").sum()) if not df.empty else 0
    n_inprog     = int((df["status"] == "In Progress").sum()) if not df.empty else 0
    n_done_today = int(df[
        (df["status"] == "Completed") &
        df["completed_at"].str.startswith(today_str, na=False)
    ].shape[0]) if not df.empty else 0
    n_archived   = len(df_arc)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("🗂 Total Active",    n_total)
    k2.metric("🆕 New",             n_new)
    k3.metric("⏳ In Progress",     n_inprog)
    k4.metric("✅ Completed Today", n_done_today)
    k5.metric("📦 Total Archived",  n_archived)

    st.markdown("<div style='margin:12px 0 4px'></div>", unsafe_allow_html=True)

    st.markdown(
        f"<p style='font-size:15px;font-weight:600;color:{GRAB_DARK};margin:0 0 8px'>Cases by Queue"
        " <span style='font-size:12px;color:#888;font-weight:400'>— click a card to filter</span></p>",
        unsafe_allow_html=True,
    )

    queue_counts = df["queue"].value_counts().to_dict() if not df.empty else {}
    queue_status = {}
    if not df.empty:
        for q in QUEUES:
            qdf = df[df["queue"] == q]
            queue_status[q] = {
                "New":         int((qdf["status"] == "New").sum()),
                "In Progress": int((qdf["status"] == "In Progress").sum()),
                "Completed":   int((qdf["status"] == "Completed").sum()),
                "Rejected":    int((qdf["status"] == "Rejected").sum()),
            }
    else:
        queue_status = {q: {"New": 0, "In Progress": 0, "Completed": 0, "Rejected": 0} for q in QUEUES}

    # Summary rejected card (all queues combined)
    n_rejected = int((df["status"] == "Rejected").sum()) if not df.empty else 0

    cases_page = st.Page(page_cases, title="Cases", icon="📋")

    # Row 1: first 3 queue cards
    row1 = st.columns(3)
    for i, q in enumerate(QUEUES[:3]):
        total_q = queue_counts.get(q, 0)
        qs = queue_status[q]
        with row1[i]:
            if st.button(
                f"{QUEUE_ICONS.get(q, '')}\n{total_q}\n{q}\n🆕 {qs['New']}  ⏳ {qs['In Progress']}  ✅ {qs['Completed']}",
                key=f"qcard_{q}",
                use_container_width=True,
            ):
                st.query_params["queue"] = q
                st.switch_page(cases_page)

    # Row 2: last 2 queue cards + Rejected summary card
    row2 = st.columns(3)
    for i, q in enumerate(QUEUES[3:]):
        total_q = queue_counts.get(q, 0)
        qs = queue_status[q]
        with row2[i]:
            if st.button(
                f"{QUEUE_ICONS.get(q, '')}\n{total_q}\n{q}\n🆕 {qs['New']}  ⏳ {qs['In Progress']}  ✅ {qs['Completed']}",
                key=f"qcard_{q}",
                use_container_width=True,
            ):
                st.query_params["queue"] = q
                st.switch_page(cases_page)
    with row2[2]:
        if st.button(
            f"🚫\n{n_rejected}\nRejected\n(all queues)",
            key="qcard_Rejected",
            use_container_width=True,
        ):
            st.query_params["status"] = "Rejected"
            st.switch_page(cases_page)

    # Inject styles for queue card buttons via JS
    _all_card_labels = QUEUES + ["Rejected"]
    _card_labels_js  = str(_all_card_labels).replace("'", '"')
    _components.html(f"""
    <script>
    (function() {{
        const LABELS = {_card_labels_js};
        function styleCards() {{
            const buttons = window.parent.document.querySelectorAll('button');
            buttons.forEach(btn => {{
                const txt = btn.innerText || '';
                if (!LABELS.some(l => txt.includes(l))) return;
                // Only rebuild once
                if (btn.dataset.styled) return;
                btn.dataset.styled = '1';
                const lines = txt.trim().split('\\n').map(s => s.trim()).filter(Boolean);
                // lines: [icon, count, name, stats...]
                if (lines.length < 3) return;
                const icon  = lines[0];
                const count = lines[1];
                const name  = lines[2];
                const stats = lines.slice(3).join('  ');
                btn.innerHTML =
                    '<span style="display:block;font-size:24px;line-height:1;margin-bottom:6px">' + icon + '</span>' +
                    '<span style="display:block;font-size:42px;font-weight:800;line-height:1;color:#00802E;margin-bottom:6px">' + count + '</span>' +
                    '<span style="display:block;font-size:13px;font-weight:600;margin-bottom:8px">' + name + '</span>' +
                    '<span style="display:block;font-size:11px;color:#888">' + stats + '</span>';
                btn.style.height = '220px';
                btn.style.minHeight = '220px';
                btn.style.display = 'flex';
                btn.style.flexDirection = 'column';
                btn.style.alignItems = 'center';
                btn.style.justifyContent = 'center';
                btn.style.borderRadius = '12px';
                btn.style.padding = '20px 8px';
            }});
        }}
        styleCards();
        setTimeout(styleCards, 200);
        setTimeout(styleCards, 800);
    }})();
    </script>
    """, height=0)

    st.divider()

    ctrl_l, ctrl_r = st.columns(2)
    with ctrl_l:
        selected_queues = st.multiselect("Compare queues", options=QUEUES, default=QUEUES, key="queue_slicer")
    with ctrl_r:
        cutoff_days = st.selectbox("Trend period", [7, 14, 30, 60, 90], index=2,
                                   format_func=lambda x: f"Last {x} days", key="trend_days")

    chart_l, chart_r = st.columns(2)
    CHART_H = 300

    with chart_l:
        tab1, tab2 = st.tabs(["📊 Cases by Queue", "📈 Daily Trend"])
        with tab1:
            if not df.empty and selected_queues:
                qdf = (
                    df[df["queue"].isin(selected_queues)]["queue"]
                    .value_counts()
                    .reindex(selected_queues, fill_value=0)
                    .reset_index()
                )
                qdf.columns = ["Queue", "Count"]
                st.bar_chart(qdf.set_index("Queue"), color=GRAB_GREEN, height=CHART_H)
            else:
                st.info("No data.")
        with tab2:
            if not df.empty and selected_queues:
                dfd = df[df["queue"].isin(selected_queues)].copy()
                dfd["_date"] = dfd["date_received"].apply(_parse_date)
                dfd = dfd.dropna(subset=["_date"])
                cutoff = date.today() - timedelta(days=cutoff_days)
                dfd = dfd[dfd["_date"] >= cutoff]
                if not dfd.empty:
                    pivot = (
                        dfd.groupby(["_date", "queue"])
                        .size().unstack(fill_value=0)
                        .reindex(columns=selected_queues, fill_value=0)
                    )
                    pivot = pivot.reindex(pd.date_range(cutoff, date.today()).date, fill_value=0)
                    st.line_chart(pivot, height=CHART_H)
                else:
                    st.info("No data in selected period.")
            else:
                st.info("No data.")

    with chart_r:
        tab3, tab4 = st.tabs(["📈 Case Trends", "⏱ Avg Age (days)"])
        with tab3:
            if not df.empty or not df_arc.empty:
                all_cases = pd.concat([df, df_arc], ignore_index=True) if not df_arc.empty else df.copy()
                all_cases["_date"] = all_cases["date_received"].apply(_parse_date)
                all_cases = all_cases.dropna(subset=["_date"])
                cutoff_t = date.today() - timedelta(days=cutoff_days)
                all_cases = all_cases[all_cases["_date"] >= cutoff_t]
                date_range = pd.date_range(cutoff_t, date.today()).date
                if not all_cases.empty:
                    new_by_day = all_cases.groupby("_date").size().reindex(date_range, fill_value=0).rename("New Cases")
                    resolved_by_day = (
                        all_cases[all_cases["status"] == "Completed"]
                        .groupby("_date").size().reindex(date_range, fill_value=0).rename("Resolved Cases")
                    )
                    active_by_day = (
                        all_cases[all_cases["status"].isin(["New", "In Progress"])]
                        .groupby("_date").size().reindex(date_range, fill_value=0).rename("Active Cases")
                    )
                    st.line_chart(pd.concat([new_by_day, resolved_by_day, active_by_day], axis=1), height=CHART_H)
                else:
                    st.info("No data in selected period.")
            else:
                st.info("No data.")
        with tab4:
            if not df.empty:
                dfa = df.copy()
                dfa["_date"] = dfa["date_received"].apply(_parse_date)
                dfa = dfa.dropna(subset=["_date"])
                dfa["age"] = dfa["_date"].apply(lambda d: (date.today() - d).days)
                avg_df = (
                    dfa.groupby("queue")["age"].mean()
                    .reindex(QUEUES).fillna(0).round(1).reset_index()
                )
                avg_df.columns = ["Queue", "Avg Days Open"]
                st.bar_chart(avg_df.set_index("Queue"), color=GRAB_GREEN, height=CHART_H)
            else:
                st.info("No active cases.")

    st.divider()
    st.markdown(f"<p style='font-size:15px;font-weight:600;color:{GRAB_DARK}'>🕐 Latest 5 New Cases</p>",
                unsafe_allow_html=True)
    if not df.empty:
        recent = df[df["status"] == "New"].head(5)[
            ["case_id", "date_received", "sender", "subject", "queue"]
        ]
        if recent.empty:
            st.info("No new cases.")
        else:
            st.dataframe(
                recent.rename(columns={
                    "case_id": "Case ID", "date_received": "Received",
                    "sender": "From", "subject": "Subject", "queue": "Queue",
                }),
                use_container_width=True, hide_index=True,
            )
    else:
        st.info("No active cases yet.")


# ── Page 2: Cases ─────────────────────────────────────────────────────────────
def page_cases():
    logged_in_user = _require_login()

    st.markdown(f"""
    <div style="background:{GRAB_GREEN};border-radius:10px;padding:12px 20px;
                display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <span style="color:white;font-size:18px;font-weight:700;">📋 Cases</span>
        <span style="color:rgba(255,255,255,0.9);font-size:13px;">👤 {logged_in_user}</span>
    </div>
    """, unsafe_allow_html=True)

    df = _get_active_cases()

    if "f_date_from" not in st.session_state:
        st.session_state["f_date_from"] = date(2020, 1, 1)
    if "f_date_to" not in st.session_state:
        st.session_state["f_date_to"] = date.today()

    with st.sidebar:
        st.markdown(f"<h3 style='color:{GRAB_GREEN};margin-bottom:12px'>🔍 Filters</h3>",
                    unsafe_allow_html=True)
        search        = st.text_input("Search", placeholder="Case ID, sender, subject...")
        default_status = STATUSES
        sp = st.query_params.get("status")
        if sp and sp in STATUSES:
            default_status = [sp]
        status_filter = st.multiselect("Status", STATUSES, default=default_status)
        default_queue = []
        qp = st.query_params.get("queue")
        if qp and qp in QUEUES:
            default_queue = [qp]
        queue_filter = st.multiselect("Queue", QUEUES, default=default_queue)
        st.markdown(f"<p style='color:{GRAB_DARK};font-weight:600;margin:8px 0 0'>Date Received</p>",
                    unsafe_allow_html=True)
        date_from = st.date_input("From", key="f_date_from")
        date_to   = st.date_input("To",   key="f_date_to")
        sort_by   = st.selectbox("Sort by", ["Newest First", "Oldest First", "By Status", "By Queue"])
        if st.button("↺ Reset Filters", use_container_width=True):
            st.session_state["f_date_from"] = date(2020, 1, 1)
            st.session_state["f_date_to"]   = date.today()
            st.rerun()

    filtered = df.copy() if not df.empty else df

    if not filtered.empty:
        if search:
            q = search.lower()
            mask = (
                filtered["case_id"].str.lower().str.contains(q, na=False) |
                filtered["sender"].str.lower().str.contains(q, na=False) |
                filtered["subject"].str.lower().str.contains(q, na=False)
            )
            filtered = filtered[mask]
        if status_filter:
            filtered = filtered[filtered["status"].isin(status_filter)]
        if queue_filter:
            filtered = filtered[filtered["queue"].isin(queue_filter)]
        filtered = filtered.copy()
        filtered["_date"] = filtered["date_received"].apply(_parse_date)
        filtered = filtered[filtered["_date"].apply(
            lambda d: d is not None and date_from <= d <= date_to
        )]
        if sort_by == "Newest First":
            filtered = filtered.sort_values("case_id", ascending=False)
        elif sort_by == "Oldest First":
            filtered = filtered.sort_values("case_id", ascending=True)
        elif sort_by == "By Status":
            order = {"New": 0, "In Progress": 1, "Completed": 2}
            filtered = filtered.assign(_so=filtered["status"].map(order)).sort_values(
                ["_so", "case_id"], ascending=[True, False])
        elif sort_by == "By Queue":
            filtered = filtered.sort_values(["queue", "case_id"], ascending=[True, False])

    total = len(filtered)

    if filtered.empty:
        st.info("No cases match the current filters.")
        return

    n_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if "cases_page" not in st.session_state:
        st.session_state["cases_page"] = 1
    if st.session_state["cases_page"] > n_pages:
        st.session_state["cases_page"] = n_pages
    page  = st.session_state["cases_page"]
    start = (page - 1) * PAGE_SIZE
    page_df = filtered.iloc[start: start + PAGE_SIZE]

    hdr_l, hdr_r = st.columns([2, 1])
    hdr_l.markdown(
        f"<p style='margin:0;padding-top:10px'><b>{total} case(s)</b> — "
        f"showing {start+1}–{min(start+PAGE_SIZE, total)}</p>",
        unsafe_allow_html=True,
    )
    with hdr_r:
        pg_prev, pg_info, pg_next = st.columns([2, 1, 2])
        if pg_prev.button("← Prev", disabled=(page == 1), key="pg_prev", use_container_width=True):
            st.session_state["cases_page"] -= 1
            st.rerun()
        pg_info.markdown(
            f"<div style='text-align:center;padding-top:6px;font-size:13px;font-weight:600'>"
            f"{page}/{n_pages}</div>",
            unsafe_allow_html=True,
        )
        if pg_next.button("Next →", disabled=(page == n_pages), key="pg_next", use_container_width=True):
            st.session_state["cases_page"] += 1
            st.rerun()

    st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

    for _, row in page_df.iterrows():
        cid          = row["case_id"]
        status       = row["status"]
        sc           = STATUS_COLOR.get(status, "#ccc")
        subject_safe = str(row["subject"]).replace("<", "&lt;").replace(">", "&gt;")
        sender_safe  = str(row["sender"]).replace("<", "&lt;").replace(">", "&gt;")
        date_short   = str(row["date_received"])[:10] if row.get("date_received") else ""

        def _esc(v): return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        assigned_html = ""
        if row.get("assigned_to"):
            assigned_html = (
                f'<span style="font-size:11px;color:#666;margin-left:auto;">'
                f'👤 {_esc(row["assigned_to"])} · {_esc(row.get("assigned_at",""))}</span>'
            )

        reject_html = ""
        if status == "Rejected" and row.get("reject_reason"):
            reject_html = (
                f'<span style="font-size:11px;color:#E74C3C;margin-left:4px;">'
                f'🚫 {_esc(row["reject_reason"])}</span>'
            )

        st.markdown(
            f'<div style="border-left:4px solid {sc};background:white;border-radius:0 8px 8px 0;'
            f'padding:12px 16px 8px;border:1px solid #e8e8e8;margin-bottom:2px;">'
            f'<p style="font-size:11px;color:#999;font-weight:500;letter-spacing:.3px;margin:0">{_esc(cid)}</p>'
            f'<p style="font-size:14px;font-weight:600;color:#1a1a1a;margin:3px 0 2px;line-height:1.3">{subject_safe}</p>'
            f'<p style="font-size:12px;color:#666;margin:0 0 7px">{sender_safe}</p>'
            f'<p style="margin:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">'
            f'<span style="background:{GRAB_LIGHT};color:{GRAB_DARK};padding:2px 10px;border-radius:12px;font-size:11px;font-weight:500">{_esc(row["queue"])}</span>'
            f'<span style="background:{sc}22;color:{sc};padding:2px 10px;border-radius:12px;font-size:11px;font-weight:500">{status}</span>'
            f'<span style="color:#aaa;font-size:11px">{date_short}</span>'
            f'{reject_html}{assigned_html}'
            f'</p></div>',
            unsafe_allow_html=True,
        )

        # ── Action buttons ────────────────────────────────────────────────────
        b1, b2, b3, b4 = st.columns([2, 2, 1.5, 1.5])

        # Queue reclassify
        with b1:
            qi = QUEUES.index(row["queue"]) if row["queue"] in QUEUES else 0
            nq = st.selectbox("Queue", QUEUES, index=qi, key=f"rq_{cid}", label_visibility="collapsed")
            if nq != row["queue"]:
                _apply_action(cid, queue=nq)

        # Toggle: Assign to Me → Mark Complete (single button)
        with b2:
            now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
            if status == "New":
                if st.button("👤 Assign to Me", key=f"act_{cid}", type="primary", use_container_width=True):
                    _apply_action(cid, status="In Progress", assigned_to=logged_in_user, assigned_at=now)
            elif status == "In Progress":
                if st.button("✅ Mark Complete", key=f"act_{cid}", type="primary", use_container_width=True):
                    _apply_action(cid, status="Completed", completed_at=now)
            else:
                st.button("✅ Done", key=f"act_{cid}", disabled=True, use_container_width=True)

        # Reject button with reason dropdown
        with b3:
            show_reject_key = f"show_rej_{cid}"
            if st.button("🚫 Reject", key=f"rej_{cid}", use_container_width=True):
                st.session_state[show_reject_key] = not st.session_state.get(show_reject_key, False)
            if st.session_state.get(show_reject_key):
                reason = st.selectbox(
                    "Reason", REJECT_REASONS,
                    key=f"rej_reason_{cid}",
                    label_visibility="collapsed",
                )
                if st.button("Confirm", key=f"rej_confirm_{cid}", type="primary", use_container_width=True):
                    _apply_action(cid, status="Rejected", reject_reason=reason)
                    st.session_state[show_reject_key] = False

        # View email
        with b4:
            if row.get("email_link"):
                st.link_button("📧 Email", row["email_link"],
                               use_container_width=True, key=f"ve_{cid}")

        st.markdown("<div style='margin-bottom:12px'></div>", unsafe_allow_html=True)


# ── Page 3: Archive ───────────────────────────────────────────────────────────
def page_archive():
    logged_in_user = _require_login()

    st.markdown(f"""
    <div style="background:{GRAB_GREEN};border-radius:10px;padding:12px 20px;
                display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <span style="color:white;font-size:18px;font-weight:700;">🗂️ Archive</span>
        <span style="color:rgba(255,255,255,0.9);font-size:13px;">👤 {logged_in_user}</span>
    </div>
    """, unsafe_allow_html=True)

    df = load_archived_cases()
    st.metric("Total Archived Cases", len(df))

    if st.button("🗂 Archive Old Completed Cases Now", use_container_width=False):
        count = archive_old_completed()
        st.success(f"Archived {count} case(s).")
        st.cache_data.clear()
        st.rerun()

    if df.empty:
        st.info("No archived cases yet.")
        return

    display_cols = ["case_id", "date_received", "sender", "subject", "queue", "completed_at"]
    out = df[display_cols].copy()
    out["completed_at"] = out["completed_at"].apply(
        lambda v: str(v)[:10] if pd.notna(v) and str(v).strip() else ""
    )
    st.dataframe(
        out.rename(columns={
            "case_id": "Case ID", "date_received": "Date Received",
            "sender": "Sender", "subject": "Subject",
            "queue": "Queue", "completed_at": "Completed At",
        }),
        use_container_width=True,
        hide_index=True,
    )


# ── Entry point ───────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page(page_home,    title="Dashboard", icon="📊"),
    st.Page(page_cases,   title="Cases",     icon="📋"),
    st.Page(page_archive, title="Archive",   icon="🗂️"),
])
pg.run()
