import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import config
import db
import backend as bk
import actions
import ai_engine
import agent_watchdog as watchdog
from agent import agent, run_autopilot_scan

# ── Self-heal stale imports ────────────────────────────────────────────
# Streamlit hot-reloads app.py on save but keeps previously-imported local
# modules (backend, db, ...) from the moment the server started. After the
# code is updated, the running process can hold an OLD backend that lacks
# newly added functions (AttributeError). Detect that and reload in-place.
if getattr(bk, "OUR_MFR", "") != "EISAI" or not getattr(db, "TIMESTAMPS_UTC", False):
    import importlib
    db = importlib.reload(db)
    bk = importlib.reload(bk)
    actions = importlib.reload(actions)
    watchdog = importlib.reload(watchdog)

st.set_page_config(
    page_title="SIGMA — Seizure Intelligence & Growth Management Agent",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

CHART_H = 380
PLOT_TPL = "plotly_white"
COLORS = ["#2563eb", "#0d9488", "#f59e0b", "#6366f1", "#7c3aed", "#06b6d4", "#ec4899", "#84cc16"]

# ══════════════════════════════════════════
# CSS
# ══════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
section[data-testid="stSidebar"] { background: #f1f5f9; border-right: 1px solid #e2e8f0; }
div[data-testid="stSidebarNav"] { display: none; }

.kpi-row { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
.kpi-card { flex: 1; min-width: 130px; background: #ffffff; border: 1px solid #e2e8f0;
    border-top: 3px solid #2563eb; border-radius: 12px; padding: 18px; text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: box-shadow .15s ease, transform .15s ease; }
.kpi-card:hover { box-shadow: 0 6px 16px rgba(37,99,235,0.10); transform: translateY(-1px); }
.kpi-value { font-size: 24px; font-weight: 700; color: #1e293b; }
.kpi-label { font-size: 11px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.kpi-sub { font-size: 12px; color: #64748b; margin-top: 2px; }
.kpi-sub.red { color: #ef4444; } .kpi-sub.green { color: #059669; }

.page-header { font-size: 24px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }
.page-sub { font-size: 14px; color: #64748b; margin-bottom: 12px; }

/* ── Agent status strip (top of every page) ── */
.agent-strip { display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
    background: linear-gradient(90deg,#f8fafc,#eef2ff); border: 1px solid #e2e8f0;
    border-radius: 10px; padding: 8px 14px; margin-bottom: 18px; font-size: 12.5px; color: #475569; }
.chip { display: inline-block; padding: 3px 10px; border-radius: 999px; font-weight: 600;
    font-size: 11.5px; border: 1px solid transparent; }
.chip.on   { background: #ecfdf5; color: #047857; border-color: #a7f3d0; }
.chip.off  { background: #f1f5f9; color: #64748b; border-color: #e2e8f0; }
.chip.warn { background: #fffbeb; color: #b45309; border-color: #fde68a; }
.chip.info { background: #eff6ff; color: #1d4ed8; border-color: #bfdbfe; }
.chip.value{ background: #f5f3ff; color: #6d28d9; border-color: #ddd6fe; }

.ai-box { background: linear-gradient(135deg,#eef2ff,#f5f3ff); border: 1px solid #c7d2fe;
    border-radius: 12px; padding: 18px 22px; margin: 10px 0; }
.ai-headline { font-weight: 700; font-size: 15px; color: #3730a3; margin-bottom: 8px; }
.ai-body { font-size: 13px; color: #334155; line-height: 1.7; }
.opp-box { background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 10px; padding: 12px 16px;
    margin: 8px 0; font-size: 13px; color: #065f46; }
.risk-box { background: #fef2f2; border: 1px solid #fecaca; border-radius: 10px; padding: 12px 16px;
    margin: 8px 0; font-size: 13px; color: #991b1b; }
.action-card { background: #ffffff; border: 1px solid #e2e8f0; border-left: 4px solid #7c3aed;
    border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; font-size: 13px; color: #334155; }
.action-title { font-weight: 700; color: #1e293b; }

/* st.metric values were being truncated with "..." in narrow columns — let them
   shrink and wrap so Patient ID, Context, Age Group etc. are fully visible. */
div[data-testid="stMetricValue"], div[data-testid="stMetricValue"] * {
    font-size: 22px !important; line-height: 1.25 !important; white-space: normal !important;
    overflow: visible !important; text-overflow: clip !important; overflow-wrap: anywhere; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════
# Startup: agent tables + status
# ══════════════════════════════════════════
@st.cache_resource
def _startup():
    try:
        db.ensure_agent_tables()
        return None
    except Exception as e:
        return str(e)

_startup_err = _startup()

PAGES = [
    ("🏠", "Executive Command Center"),
    ("🧬", "Patient Journey Explorer"),
    ("🔬", "Diagnosis Intelligence"),
    ("💊", "Treatment & Adherence"),
    ("🏥", "Market Access & Payer"),
    ("👨‍⚕️", "Physician & Geo Intelligence"),
    ("🤖", "AI Strategy Agent"),
    ("⚡", "Action Center"),
]

with st.sidebar:
    # ── SIGMA brand: Σ badge in doctor-scrub teal & medical blue, with an EEG pulse line ──
    st.markdown("""
    <div style="display:flex;align-items:center;gap:12px;margin:6px 0 2px;">
      <svg width="48" height="48" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="sigma_g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#2563eb"/><stop offset="1" stop-color="#0d9488"/>
          </linearGradient>
        </defs>
        <rect x="1" y="1" width="46" height="46" rx="13" fill="url(#sigma_g)"/>
        <text x="24" y="30" font-family="Georgia,serif" font-size="23" font-weight="bold"
              fill="#ffffff" text-anchor="middle">&#931;</text>
        <polyline points="7,39 15,39 18,33 21,43 24,36 26,39 41,39" fill="none"
                  stroke="#99f6e4" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M37 7 h4 v3 h3 v4 h-3 v3 h-4 v-3 h-3 v-4 h3 z" fill="#ffffff" opacity="0.9"/>
      </svg>
      <div style="font-size:34px;font-weight:900;letter-spacing:6px;font-family:Inter,sans-serif;
                  font-style:italic;
                  background:linear-gradient(100deg,#1d4ed8 15%,#0d9488 55%,#06b6d4 90%);
                  -webkit-background-clip:text;background-clip:text;
                  -webkit-text-fill-color:transparent;color:transparent;
                  text-shadow:0 2px 8px rgba(13,148,136,0.18);">SIGMA</div>
    </div>
    <div style="font-size:9px;font-weight:600;color:#64748b;letter-spacing:.6px;
                text-transform:uppercase;margin:0 0 2px;">
      Seizure Intelligence &amp; Growth Management Agent</div>
    """, unsafe_allow_html=True)
    st.markdown("---")
    if "current_page" not in st.session_state:
        st.session_state.current_page = PAGES[0][1]
    for icon, name in PAGES:
        btn_type = "primary" if st.session_state.current_page == name else "secondary"
        if st.button(f"{icon}  {name}", key=f"nav_{name}", use_container_width=True, type=btn_type):
            st.session_state.current_page = name
            st.rerun()
    st.markdown("---")
    st.markdown("**🤖 Agent Autonomy**")
    # These toggles are backed by NON-widget session keys (autonomous_mode / auto_insights).
    # Navigation buttons above call st.rerun() before these render, so their widget keys get
    # garbage-collected each nav — using separate persistent keys keeps the choice sticky until
    # the user flips it back. Readers elsewhere still use autonomous_mode / auto_insights.
    st.session_state.setdefault("autonomous_mode", True)
    st.session_state.setdefault("auto_insights", False)

    def _sync_toggle(persistent_key):
        st.session_state[persistent_key] = st.session_state[persistent_key + "_w"]

    st.toggle(
        "Autonomous Mode", value=st.session_state["autonomous_mode"], key="autonomous_mode_w",
        on_change=_sync_toggle, args=("autonomous_mode",),
        help="ON: the Watchdog auto-executes LOW-risk actions (safety follow-ups, claim escalations, "
             "outreach) as 'Autopilot'. HIGH-risk actions (tests, prescriptions, referrals, budget "
             "moves) ALWAYS wait for your approval.")
    st.toggle(
        "Auto-generate AI insights", value=st.session_state["auto_insights"], key="auto_insights_w",
        on_change=_sync_toggle, args=("auto_insights",),
        help="ON: every page generates its Claude insight automatically on first visit "
             "(uses API credits). OFF: click Generate on each page.")
    st.markdown("---")
    if st.button("🔄 Refresh Data from SSMS", use_container_width=True,
                 help="Re-pull everything live from SQL Server. New rows added in SSMS appear immediately."):
        # clear cached page insights + re-run the watchdog against the latest data
        for k in [k for k in st.session_state.keys() if k.startswith("insight_")]:
            del st.session_state[k]
        st.session_state.pop("watchdog_summary", None)
        st.session_state["_refreshed"] = True
        st.rerun()
    if st.session_state.pop("_refreshed", False):
        st.success("Refreshed from SSMS", icon="✅")
    st.markdown("---")
    label, ok, detail = db.connection_status()
    st.caption(("🟢 " if ok else "🔴 ") + label)
    if not ok:
        st.error(detail, icon="🗄️")
    st.caption(("🟢 Claude API key loaded" if config.api_key_is_set()
                else "🟠 Claude API key not set — edit .env (ANTHROPIC_API_KEY)"))
    pend = 0
    try:
        pend = len(actions.get_pending_actions()) + len(actions.get_pending_business_actions())
    except Exception:
        pass
    if pend:
        st.warning(f"⚡ {pend} action(s) awaiting approval", icon="🕓")

page = st.session_state.current_page

if _startup_err:
    st.error(f"Database not reachable: {_startup_err}\n\nCheck DB settings in `.env` "
             "(or set DB_TYPE=sqlite and run `python generate_dummy_data.py`).")
    st.stop()

# ══════════════════════════════════════════
# Autonomous Watchdog — runs by itself once per session
# ══════════════════════════════════════════
if "watchdog_summary" not in st.session_state:
    try:
        st.session_state.watchdog_summary = watchdog.sweep(
            auto_execute=st.session_state.get("autonomous_mode", True))
    except Exception as e:
        st.session_state.watchdog_summary = {"safety": 0, "claims": 0, "untreated": 0,
                                             "auto_executed": 0, "messages": [], "error": str(e)}

# ══════════════════════════════════════════
# Agent status strip — visible on every page
# ══════════════════════════════════════════
def render_status_strip():
    wd = st.session_state.get("watchdog_summary", {})
    found = wd.get("safety", 0) + wd.get("claims", 0) + wd.get("untreated", 0)
    try:
        vm = actions.get_value_metrics()
    except Exception:
        vm = {"pending": 0, "value_captured": 0, "value_pending": 0}
    auto_on = st.session_state.get("autonomous_mode", True)
    chips = [
        f'<span class="chip {"on" if auto_on else "off"}">'
        f'{"🟢 Autonomous Mode ON" if auto_on else "⚪ Autonomous Mode OFF"}</span>',
        f'<span class="chip info">🐕 Watchdog: {found} issue(s) detected'
        + (f' · {wd.get("auto_executed", 0)} auto-handled' if wd.get("auto_executed") else "") + '</span>',
    ]
    if vm.get("pending"):
        chips.append(f'<span class="chip warn">🕓 {vm["pending"]} awaiting approval '
                     f'(≈ ${vm.get("value_pending", 0):,.0f})</span>')
    if vm.get("value_captured"):
        chips.append(f'<span class="chip value">💰 Value captured: ${vm["value_captured"]:,.0f}</span>')
    st.markdown(f'<div class="agent-strip"><strong>🤖 Agent status</strong>{"".join(chips)}</div>',
                unsafe_allow_html=True)


render_status_strip()


def chart_layout(fig, title="", h=CHART_H):
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#1e293b")),
        height=h, template=PLOT_TPL,
        margin=dict(l=10, r=10, t=44, b=10),
        font=dict(family="Inter", size=12, color="#475569"),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
    )
    return fig


# ══════════════════════════════════════════
# Power-BI-style dashboard machinery:
#   • section_date_filter → one date slicer per section, every chart obeys it
#   • chart clicks → cross-filter every other chart in the same section
# ══════════════════════════════════════════
@st.cache_data(ttl=600, show_spinner=False)
def _date_bounds():
    mn, mx = bk.get_date_bounds()
    return (mn.to_pydatetime() if mn is not None else None,
            mx.to_pydatetime() if mx is not None else None)


def section_date_filter(skey):
    """One date-range slicer per section. Returns (d1, d2) ISO strings,
    or (None, None) when the full range is selected (no filtering needed)."""
    dmin, dmax = _date_bounds()
    if not dmin or not dmax or dmin >= dmax:
        return None, None
    lo, hi = st.slider(
        "📅 Date range — drag either handle; every chart & number in this section updates together",
        min_value=dmin, max_value=dmax, value=(dmin, dmax), format="MMM YYYY", key=f"dr_{skey}")
    if lo == dmin and hi == dmax:
        return None, None
    return lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")


def clicked_value(event, field="x"):
    """Value of the first clicked point from a st.plotly_chart(on_select='rerun') event.
    field: 'x', 'y', 'location' (maps) or 'customdata0' (first custom_data column)."""
    try:
        pts = event.selection.points
        if not pts:
            return None
        pt = pts[0]
        if field == "customdata0":
            cd = pt.get("customdata") or []
            return cd[0] if cd else None
        return pt.get(field)
    except Exception:
        return None


def apply_crossfilter(skey, dim_label, value):
    """Remember a chart click as this section's cross-filter and rerun so every chart obeys it."""
    if value is None:
        return
    cur = st.session_state.get(f"xf_{skey}")
    if not cur or cur[1] != value:
        st.session_state[f"xf_{skey}"] = (dim_label, value)
        st.rerun()


def crossfilter_chip(skey):
    """Banner showing the active cross-filter with a Clear button.
    Returns (dim_label, value) or None."""
    xf = st.session_state.get(f"xf_{skey}")
    if not xf:
        st.caption("💡 This section works like a dashboard: click any bar (or state on a map) to "
                   "cross-filter every other chart here; click **✖ Clear** to reset.")
        return None
    c1, c2 = st.columns([5, 1])
    with c1:
        st.markdown(f'<div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;'
                    f'padding:7px 12px;font-size:13px;color:#3730a3;">🔗 <strong>Cross-filter:</strong> '
                    f'{xf[0]} = <strong>{xf[1]}</strong> — all charts in this section are filtered.</div>',
                    unsafe_allow_html=True)
    with c2:
        if st.button("✖ Clear", key=f"clr_{skey}", use_container_width=True):
            del st.session_state[f"xf_{skey}"]
            # bumping the nonce renews chart keys, which wipes their click-selections
            st.session_state[f"nonce_{skey}"] = st.session_state.get(f"nonce_{skey}", 0) + 1
            st.rerun()
    return xf


def _ck(skey, name):
    """Chart key namespaced by the section's clear-nonce."""
    return f"{name}_{st.session_state.get(f'nonce_{skey}', 0)}"


def highlight_bars(fig, values, selected, base_color):
    """Power-BI-style highlight: the clicked bar keeps full colour, the rest fade."""
    if selected is None:
        return fig
    fig.update_traces(marker_color=[base_color if v == selected else "#cbd5e1" for v in values])
    return fig


# ══════════════════════════════════════════
# Reusable agentic AI-insight panel
# ══════════════════════════════════════════
def df_ctx(df, limit=60):
    """Compact a dataframe for the model."""
    if df is None or (hasattr(df, "empty") and df.empty):
        return []
    return df.head(limit).to_dict(orient="records")


def render_ai_panel(page_name, data_context, key):
    """The agentic layer on every page: Claude reads the live page data,
    explains what it means for the business, and proposes executable actions.
    With 'Auto-generate AI insights' ON, this runs by itself on first visit."""
    st.markdown("### 🤖 AI Insight & Next Best Actions")

    def _generate():
        with st.spinner("Claude is analysing this page's live data..."):
            try:
                st.session_state[f"insight_{key}"] = ai_engine.generate_page_insight(page_name, data_context)
                # a fresh analysis brings fresh opportunity cards — clear old dismissals
                for k in [k for k in st.session_state.keys() if k.startswith(f"oppdone_{key}_")]:
                    del st.session_state[k]
            except Exception as e:
                st.session_state[f"insight_{key}"] = {"error": str(e)}

    # agentic: auto-generate on first visit when enabled
    if (st.session_state.get("auto_insights") and config.api_key_is_set()
            and f"insight_{key}" not in st.session_state):
        _generate()

    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("✨ Generate / Refresh", key=f"gen_{key}", type="primary", use_container_width=True):
            if not config.api_key_is_set():
                st.error("Set your Claude API key in `.env` (ANTHROPIC_API_KEY) first.")
            else:
                _generate()
    insight = st.session_state.get(f"insight_{key}")
    if not insight:
        st.caption("Click **Generate** (or switch on *Auto-generate AI insights* in the sidebar) — the AI "
                   "reads the live data above, explains what it means for the business, and proposes "
                   "2–3 alternative actions you can approve & execute in one click.")
        return
    if "error" in insight:
        st.error(insight["error"])
        return

    st.markdown(f'<div class="ai-box"><div class="ai-headline">💡 {insight["headline"]}</div>'
                f'<div class="ai-body">{"<br>".join("• " + i for i in insight["insights"])}</div></div>',
                unsafe_allow_html=True)
    if insight.get("business_opportunity"):
        st.markdown(f'<div class="opp-box">💰 <strong>Business opportunity:</strong> {insight["business_opportunity"]}</div>',
                    unsafe_allow_html=True)
    if insight.get("risks"):
        st.markdown(f'<div class="risk-box">⚠️ <strong>Risks:</strong> {" · ".join(insight["risks"])}</div>',
                    unsafe_allow_html=True)

    # ── Opportunity cards: one finding, 2-3 alternative options — the business decides ──
    opportunities = insight.get("opportunities", [])
    live = [ci for ci in range(len(opportunities))
            if not st.session_state.get(f"oppdone_{key}_{ci}")]
    if opportunities and not live:
        st.caption("✅ All opportunities on this page have been actioned — track them in ⚡ Action Center. "
                   "Click **Generate / Refresh** for a fresh analysis.")
    if live:
        st.markdown("#### 🎯 Opportunities — pick how to act (2–3 options each)")
    for ci, card in enumerate(opportunities):
        # once one of its options is approved/queued, the card disappears from this page
        if st.session_state.get(f"oppdone_{key}_{ci}"):
            continue
        with st.container(border=True):
            st.markdown(f"**📌 Finding:** {card['finding']}")
            st.markdown(f'<div class="opp-box">💰 <strong>Value at stake:</strong> {card["value_at_stake"]}</div>',
                        unsafe_allow_html=True)
            opts = card.get("options", [])[:3]
            cols = st.columns(len(opts)) if opts else []
            for oi, (col, opt) in enumerate(zip(cols, opts)):
                with col:
                    is_playbook = len(opt.get("steps", [])) > 1
                    kind = "🧭 Playbook" if is_playbook else (
                        "🏢 Business" if any(s.get("action_type") in actions.BUSINESS_ACTION_TYPES
                                             for s in opt.get("steps", [])) else "🧑‍⚕️ Patient")
                    opt_value = float(opt.get("estimated_value_usd", 0) or 0)
                    value_txt = f' · 💰 ~${opt_value:,.0f}' if opt_value else ""
                    st.markdown(
                        f'<div class="action-card"><span class="action-title">Option {chr(65+oi)} · '
                        f'{opt["label"]}</span><br>'
                        f'<small>{kind} · Effort: {opt["effort"]} · Cost: {opt["cost"]} · '
                        f'{opt["timeframe"]} · Confidence: {opt["confidence"]}{value_txt}</small></div>',
                        unsafe_allow_html=True)
                    for si, step in enumerate(opt.get("steps", []), 1):
                        a_type = step.get("action_type", "?").replace("_", " ").title()
                        if step.get("patient_ids"):
                            tgt = "patients " + ", ".join(f"#{p}" for p in step["patient_ids"][:5])
                        elif step.get("target"):
                            tgt = f'{step.get("target_type","")} **{step["target"]}**'
                        else:
                            tgt = ""
                        st.markdown(f"{si}. **{a_type}** → {tgt}  \n&nbsp;&nbsp;&nbsp;{step.get('details','')}")
                    st.caption(f"Why this option: {opt['reason']}")
                    st.caption(f"Expected impact: {opt['expected_impact']}")
                    b1, b2 = st.columns(2)
                    with b1:
                        if st.button("✅ Approve & Execute", key=f"exec_{key}_{ci}_{oi}", use_container_width=True):
                            actions.execute_option_steps(
                                opt.get("steps", []), proposed_by="AI Insight", execute_now=True,
                                option_value_usd=opt_value)
                            # dismiss this finding's card from the page — full trail lives in ⚡ Action Center
                            st.session_state[f"oppdone_{key}_{ci}"] = "executed"
                            st.toast(f"✅ Option {chr(65+oi)} executed — see ⚡ Action Center.", icon="✅")
                            st.rerun()
                    with b2:
                        if st.button("🕓 Queue for review", key=f"queue_{key}_{ci}_{oi}", use_container_width=True):
                            actions.execute_option_steps(
                                opt.get("steps", []), proposed_by="AI Insight", execute_now=False,
                                option_value_usd=opt_value)
                            st.session_state[f"oppdone_{key}_{ci}"] = "queued"
                            st.toast(f"🕓 Option {chr(65+oi)} queued — approve it in ⚡ Action Center.", icon="🕓")
                            st.rerun()


# ══════════════════════════════════════════
# PAGE 1 — EXECUTIVE COMMAND CENTER
# ══════════════════════════════════════════
if page == "Executive Command Center":
    st.markdown('<div class="page-header">Executive Command Center</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">The health of the business at a glance — how much we earn, how many '
                'people we treat, and where money is being left on the table. Hover any number for what it means.</div>',
                unsafe_allow_html=True)

    # ── Dashboard controls: date slicer + click-to-cross-filter ──
    lo, hi = section_date_filter("exec")
    xf = crossfilter_chip("exec")          # ("Period", "2023 Q2") | ("Medicine category", label) | None

    def _period_range(val):
        """Clicked chart period → (d1, d2). Handles '2023 Q2' and month datetimes."""
        s = str(val)
        try:
            if " Q" in s:
                yr, q = s.split(" Q")
                start = pd.Timestamp(int(yr), (int(q) - 1) * 3 + 1, 1)
                end = start + pd.offsets.QuarterEnd(0)
            else:
                start = pd.Timestamp(s).replace(day=1)
                end = start + pd.offsets.MonthEnd(0)
            return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        except Exception:
            return lo, hi

    # effective window = slicer range, narrowed further by a clicked period
    eff_d1, eff_d2 = lo, hi
    usc_filter = None
    if xf and xf[0] == "Period":
        eff_d1, eff_d2 = _period_range(xf[1])
    elif xf and xf[0] == "Medicine category":
        usc_filter = xf[1]                 # holds the raw BB_USC_NAME (via customdata)

    kpis = bk.get_kpi_summary(eff_d1, eff_d2)

    def kpi(label, value, sub, tip, sub_class=""):
        return (f'<div class="kpi-card" title="{tip}">'
                f'<div class="kpi-label">{label}</div>'
                f'<div class="kpi-value">{value}</div>'
                f'<div class="kpi-sub {sub_class}">{sub}</div></div>')

    row1 = "".join([
        kpi("Total Patients", f"{kpis['total_patients']:,}", "people in our data",
            "Every unique patient in the database (identified only by an ID, never by name)."),
        kpi("Total Sales",
            (f"${kpis['total_revenue']/1e6:,.2f}M" if kpis['total_revenue'] >= 1e6
             else f"${kpis['total_revenue']:,.0f}"), "gross drug sales",
            "Total money paid for every prescription filled = amount paid by insurance plans PLUS the amount "
            "patients paid out of pocket. This is our gross drug sales across the whole period."),
        kpi("On Active Therapy", f"{kpis['active_treatments']:,}", "filled a script recently",
            "Patients who filled a prescription in the last 120 days — still actively on treatment and generating revenue."),
        kpi("Diagnosed, Not Treated", f"{kpis['untreated_rare']:,}", "missed revenue",
            "Patients diagnosed with epilepsy who have NOT started any anti-seizure medicine. Each is a patient "
            "we could help and revenue we haven't captured.", "red"),
        kpi("Possibly Undiagnosed", f"{kpis['undiagnosed_suspects']:,}", "future patients",
            "Patients showing warning-sign symptoms but with no confirmed diagnosis yet. If diagnosed, they may "
            "need our therapy — a future (pipeline) opportunity.", "green"),
        kpi("Denied Insurance Claims", f"{kpis['denied_claims']:,}", "revenue held up by payers",
            "Prescriptions the insurer refused to pay — money earned but not collected. Many can be recovered by appeal.", "red"),
    ])
    st.markdown(f'<div class="kpi-row">{row1}</div>', unsafe_allow_html=True)
    st.caption("💡 Sales = insurance-plan payments + patient out-of-pocket, across every prescription filled. "
               "Patient counts are distinct people; claim counts are individual insurance transactions.")

    # ── Explore the numbers & take action ──
    st.markdown("#### 🔎 Explore the numbers & take action")
    e1, e2, e3 = st.columns(3)
    with e1:
        with st.expander(f"👁 {kpis['undiagnosed_suspects']} possibly undiagnosed"):
            du = bk.get_undiagnosed_suspects(eff_d1, eff_d2)
            st.caption("Symptoms recorded but no confirmed diagnosis — candidates for a confirmatory test.")
            st.dataframe(du, use_container_width=True, height=240, hide_index=True)
            if not du.empty and st.button("🕓 Queue diagnostic-test outreach", key="act_undx"):
                actions.execute_option_steps([{
                    "action_type": "order_diagnostic_test",
                    "patient_ids": [int(p) for p in du["patient_id"].head(5)],
                    "target_type": "", "target": "",
                    "details": "Order confirmatory diagnostic testing for symptomatic, undiagnosed patient.",
                    "reason": "Symptom signals present with no confirmed diagnosis (Executive review)."}],
                    proposed_by="Executive Page", execute_now=False)
                st.success("Queued for approval — see ⚡ Action Center.")
    with e2:
        with st.expander(f"👁 {kpis['denied_claims']} denied claims & why"):
            dr = bk.get_denial_reasons(d1=eff_d1, d2=eff_d2)
            if not dr.empty:
                figd = px.bar(dr, x="count", y="denial_reason", orientation="h", text="count",
                              color_discrete_sequence=["#f59e0b"], labels={"count": "Claims", "denial_reason": ""})
                figd.update_traces(texttemplate="%{x:,}", textposition="outside", cliponaxis=False)
                figd.update_layout(yaxis=dict(automargin=True), margin=dict(l=10, r=45, t=44, b=10))
                st.plotly_chart(chart_layout(figd, "Why claims were denied", 240), use_container_width=True)
            dcp = bk.get_denied_claims_patients(d1=eff_d1, d2=eff_d2)
            if not dcp.empty:
                st.caption(f"${dcp['claim_amount'].sum():,.0f} in denied claims — much of it recoverable by appeal.")
                st.dataframe(dcp.head(50), use_container_width=True, height=200, hide_index=True)
                if st.button("🕓 Queue escalation of top denied claims", key="act_denied"):
                    for _, r in dcp.head(5).iterrows():
                        actions.propose_action(
                            "escalate_claim", int(r["patient_id"]),
                            f"Appeal denied claim of ${float(r['claim_amount']):,.0f} at {r['payer_name']} "
                            f"(reason: {r['denial_reason']}).", "High-value denial flagged on Executive review.",
                            f"Recoverable up to ${float(r['claim_amount']):,.0f}.",
                            proposed_by="Executive Page", estimated_value_usd=float(r["claim_amount"]))
                    st.success("Queued for approval — see ⚡ Action Center.")
    with e3:
        with st.expander(f"👁 {kpis['untreated_rare']} diagnosed, not treated"):
            ut = bk.get_untreated_rare_patients(eff_d1, eff_d2)
            st.caption("Diagnosed with epilepsy but never started an anti-seizure medicine — direct revenue opportunity.")
            st.dataframe(ut.head(100), use_container_width=True, height=240, hide_index=True)
            if not ut.empty and st.button("🕓 Queue treatment-initiation outreach", key="act_untreated"):
                actions.execute_option_steps([{
                    "action_type": "send_notification",
                    "patient_ids": [int(p) for p in ut["patient_id"].head(5)],
                    "target_type": "", "target": "",
                    "details": "Care-team outreach to discuss starting therapy for a diagnosed, untreated patient.",
                    "reason": "Confirmed diagnosis with no therapy started (Executive review)."}],
                    proposed_by="Executive Page", execute_now=False)
                st.success("Queued for approval — see ⚡ Action Center.")

    st.markdown("---")
    col_a, col_b = st.columns(2)
    with col_a:
        h1, h2, h3 = st.columns([3, 2, 1])
        h1.markdown("**📈 Sales trend**")
        gran = h2.radio("gran", ["Quarterly", "Monthly"], horizontal=True, label_visibility="collapsed", key="rev_gran")
        if h3.button("🔄", key="ref_rev", help="Refresh this chart from SSMS"):
            st.rerun()
        if gran == "Quarterly":
            dfq = bk.get_revenue_by_quarter(lo, hi, usc_name=usc_filter)
            if not dfq.empty:
                fig = px.bar(dfq, x="period", y="revenue", color_discrete_sequence=["#2563eb"],
                             text="revenue", labels={"period": "", "revenue": "Sales (USD)"})
                fig.update_traces(texttemplate="$%{y:,.0f}", textposition="outside", cliponaxis=False,
                                  hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>")
                fig.update_layout(margin=dict(l=10, r=10, t=54, b=10))
                if xf and xf[0] == "Period":
                    highlight_bars(fig, dfq["period"].tolist(), xf[1], "#2563eb")
                ev = st.plotly_chart(chart_layout(fig, "Sales by Quarter"), use_container_width=True,
                                     key=_ck("exec", "rev_q"), on_select="rerun")
                apply_crossfilter("exec", "Period", clicked_value(ev))
        else:
            dfm = bk.get_revenue_by_month(lo, hi, usc_name=usc_filter)
            if not dfm.empty:
                dfm = dfm.copy()
                dfm["month_dt"] = pd.to_datetime(dfm["month"] + "-01", errors="coerce")
                fig = px.area(dfm, x="month_dt", y="revenue", color_discrete_sequence=["#2563eb"],
                              labels={"month_dt": "", "revenue": "Sales (USD)"})
                fig.update_xaxes(dtick="M3", tickformat="%b\n%Y")
                fig.update_traces(hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra></extra>")
                ev = st.plotly_chart(chart_layout(fig, "Sales by Month"), use_container_width=True,
                                     key=_ck("exec", "rev_m"), on_select="rerun")
                apply_crossfilter("exec", "Period", clicked_value(ev))
        st.caption("How much medicine we sold in each period — the money that came in from all prescriptions. "
                   "**Click a bar/point** to focus every number on this page on that period.")
    with col_b:
        h1, h2 = st.columns([5, 1])
        h1.markdown("**👥 Patients by health condition**")
        if h2.button("🔄", key="ref_pop", help="Refresh this chart from SSMS"):
            st.rerun()
        CONDITION_NAMES = {
            "ANTICONVULSANT - FIRST-LINE MAINTENANCE": "Daily Seizure Control (First-Line)",
            "ANTICONVULSANT - LGS / SPECIALTY": "LGS / Specialty Epilepsy Medicines",
            "ANTICONVULSANT - RESCUE (ACUTE SEIZURE)": "Emergency Rescue Medicines",
            "ANTICONVULSANT - ADJUNCT / REFRACTORY": "Add-On (Hard-to-Treat) Medicines"}
        df_dist = bk.get_patient_distribution(eff_d1, eff_d2)
        if not df_dist.empty:
            df_dist = df_dist.copy()
            df_dist["condition"] = df_dist["disease_name"].map(lambda x: CONDITION_NAMES.get(x, str(x).title()))
            df_dist = df_dist.sort_values("patient_count")
            fig = px.bar(df_dist, x="patient_count", y="condition", orientation="h", text="patient_count",
                         custom_data=["disease_name"],
                         color_discrete_sequence=["#0d9488"], labels={"patient_count": "Patients", "condition": ""})
            fig.update_traces(texttemplate="%{x:,}", textposition="outside", cliponaxis=False)
            fig.update_layout(margin=dict(l=10, r=45, t=44, b=10), yaxis=dict(automargin=True))
            if usc_filter:
                highlight_bars(fig, df_dist["disease_name"].tolist(), usc_filter, "#0d9488")
            ev = st.plotly_chart(chart_layout(fig, "Patients by Medicine Category"), use_container_width=True,
                                 key=_ck("exec", "dist"), on_select="rerun")
            apply_crossfilter("exec", "Medicine category", clicked_value(ev, "customdata0"))
        st.caption("How many patients take each category of seizure medicine — daily control, specialty (LGS), "
                   "emergency rescue, and add-on medicines. **Click a bar** to filter the sales trend to that category.")

    st.markdown("---")
    render_ai_panel("Executive Command Center", {
        "active_filters": {"date_range": [eff_d1, eff_d2], "cross_filter": xf},
        "kpis": kpis,
        "revenue_by_quarter": df_ctx(bk.get_revenue_by_quarter(eff_d1, eff_d2)),
        "patients_by_condition": df_ctx(df_dist),
        "denial_reasons": df_ctx(dr),
        "untreated_patients": df_ctx(ut, 40),
        "undiagnosed_suspects": df_ctx(du, 25),
    }, key="exec")

# ══════════════════════════════════════════
# PAGE 2 — PATIENT JOURNEY EXPLORER (time-series, ID-only)
# ══════════════════════════════════════════
elif page == "Patient Journey Explorer":
    st.markdown('<div class="page-header">Patient Journey Explorer — Timeline Storyteller</div>', unsafe_allow_html=True)
    st.markdown("<div class='page-sub'>Follow one epilepsy patient's story from first hospital visit to "
                "diagnosis, treatment and medicine changes — in the exact order it happened. "
                "Identities stay confidential (Patient ID only).</div>", unsafe_allow_html=True)

    # ── 3 filters: context (seizure duration) · sub-context (age group) · patient ──
    f1, f2, f3 = st.columns([1.3, 1.1, 1.8])
    with f1:
        sel_ctx = st.selectbox("Seizure context", ["All"] + list(bk.CTX_LABEL.values()),
                               help="Grouped by how long the seizures last: under 2 minutes, "
                                    "2-5 minutes (prolonged), or 5+ minutes (status epilepticus — a medical emergency).")
    with f2:
        sel_band = st.selectbox("Age group (sub-context)", ["All"] + [b[0] for b in bk.AGE_BANDS])
    jp = bk.get_journey_patients(sel_ctx, sel_band)
    total_match = bk.get_journey_patient_count(sel_ctx, sel_band)
    with f3:
        if jp.empty:
            st.warning("No patients match these filters.")
            st.stop()
        options = [f"#{int(r.patient_id)} · {r.state} · {r.age_band}" for r in jp.itertuples()]
        sel = st.selectbox("Patient ID", options)
        pid = int(sel.split("·")[0].strip().lstrip("#"))

    # live record count — updates whenever the filters change
    ctx_txt = "all contexts" if sel_ctx == "All" else sel_ctx
    band_txt = "all ages" if sel_band == "All" else sel_band
    st.markdown(
        f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:8px 14px;'
        f'margin:4px 0 10px;font-size:13px;color:#1e40af;">👥 <strong>{total_match:,}</strong> epilepsy '
        f'patients match your filters &nbsp;(<strong>{ctx_txt}</strong> · <strong>{band_txt}</strong>)'
        f'{" — showing the 1,500 most detailed journeys in the dropdown" if total_match > 1500 else ""}.'
        f'</div>', unsafe_allow_html=True)

    # ── patient info: updates as the selected patient changes ──
    pc = bk.get_patient_context(pid) or {}
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Patient ID", f"#{pid}")
    c2.metric("Year of Birth", pc.get("yob", "—"))
    c3.metric("Gender", pc.get("gender", "—"))
    c4.metric("State", pc.get("state", "—"))
    c5.metric("Context", pc.get("context", "—"),
              help="Seizure severity group, based on typical seizure duration.")
    c6.metric("Age Group", pc.get("age_band", "—"))

    tl = bk.get_patient_timeline_events(pid)
    if tl.empty:
        st.info("No events recorded for this patient yet.")
    else:
        # ── build the story: chronological narrative ──
        tl = tl.sort_values("event_date").reset_index(drop=True)

        def _story(r):
            d = r.event_date.strftime("%d %b %Y")
            if r.event_type == "Visit/Procedure":
                return f"{d} — Visit / test: {r.details}"
            if r.event_type == "Diagnosis":
                return f"{d} — Doctor recorded: {r.details}"
            if r.event_type == "Prescription":
                return f"{d} — Picked up medicine: {r.details} (${r.extra} paid)"
            if r.event_type == "Side Effect":
                return f"{d} — Side effect reported: {r.details} (severity: {r.extra})"
            return f"{d} — Medicine changed: {r.details} (reason: {r.extra})"

        tl["story"] = tl.apply(_story, axis=1)
        LANES = ["Visit/Procedure", "Diagnosis", "Prescription", "Drug Switch", "Side Effect"]
        LANE_LABEL = {"Visit/Procedure": "Visits & tests", "Diagnosis": "Diagnoses",
                      "Prescription": "Medicines picked up", "Drug Switch": "Switched medicine",
                      "Side Effect": "Side effects"}
        CMAP = {"Visit/Procedure": "#06b6d4", "Diagnosis": "#f59e0b", "Prescription": "#059669",
                "Drug Switch": "#7c3aed", "Side Effect": "#ec4899"}
        tl["lane"] = tl.event_type.map(LANE_LABEL)

        st.markdown(f"**🧭 Timeline Storyteller — Patient #{pid}**")

        # ── Date-range filter: drag EITHER handle; month-level granularity (not just whole years) ──
        dmin = tl.event_date.min().to_pydatetime()
        dmax = tl.event_date.max().to_pydatetime()
        if dmin < dmax:
            sel_range = st.slider(
                "📅 Date range — drag either handle to focus the timeline (down to the month)",
                min_value=dmin, max_value=dmax, value=(dmin, dmax),
                format="MMM YYYY",
                help="Drag the left or right handle to zoom the grid into any window you want — a few "
                     "years, a single year, or even a 6-month stretch for this patient.")
        else:
            sel_range = (dmin, dmax)
        lo = pd.Timestamp(sel_range[0]); hi = pd.Timestamp(sel_range[1])
        tl_view = tl[(tl.event_date >= lo) & (tl.event_date <= hi)]

        # colour key for the squares (proper label per colour, no overlap)
        def _chip(c, lbl):
            return (f'<span style="display:inline-block;width:12px;height:12px;background:{c};'
                    f'border-radius:3px;margin:0 5px 0 12px;vertical-align:middle;"></span>{lbl}')
        st.markdown('<div style="font-size:12.5px;color:#475569;margin:2px 0 2px;"><strong>What each square means:</strong>'
                    + _chip("#06b6d4", "Visits &amp; tests") + _chip("#f59e0b", "Diagnoses")
                    + _chip("#059669", "Medicines picked up") + _chip("#7c3aed", "Switched medicine")
                    + _chip("#ec4899", "Side effects") + '</div>', unsafe_allow_html=True)

        # ── Grid: one row per event type, one square per event, years across the bottom ──
        fig = go.Figure()
        for et in LANES:
            sub = tl_view[tl_view.event_type == et]
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(
                x=sub.event_date, y=sub.lane, mode="markers", name=LANE_LABEL[et],
                marker=dict(size=15, color=CMAP[et], symbol="square",
                            line=dict(width=1, color="#ffffff")),
                text=sub.story, hovertemplate="%{text}<extra></extra>"))
        fig.update_yaxes(categoryorder="array",
                         categoryarray=[LANE_LABEL[e] for e in LANES[::-1]],
                         showgrid=True, gridcolor="#cbd5e1", gridwidth=1, zeroline=False)
        # gridlines: bold vertical line every year + lighter line every 3 months
        fig.update_xaxes(showgrid=True, gridcolor="#94a3b8", gridwidth=1.2, dtick="M12",
                         tickformat="%Y", ticks="outside", zeroline=False,
                         minor=dict(showgrid=True, gridcolor="#e2e8f0", gridwidth=1, dtick="M3"))
        fig.update_layout(showlegend=False, margin=dict(l=10, r=10, t=20, b=10),
                          plot_bgcolor="#ffffff")

        if tl_view.empty:
            st.info("No events in the selected date range. Widen the range above.")
        else:
            st.plotly_chart(chart_layout(fig, "", 420), use_container_width=True)
        rng_txt = f"{lo.strftime('%b %Y')} – {hi.strftime('%b %Y')}"
        st.caption(f"Each row is a type of event; each square is one event placed on the month it happened "
                   f"(currently showing {rng_txt}). Bold vertical lines mark each year, lighter ones every "
                   f"three months. Read left to right to follow the patient's story in time order — the first "
                   f"visit, the tests, the confirmed diagnosis, the medicines picked up, side effects that "
                   f"appeared after starting them, and any switch to a different medicine. Hover any square "
                   f"for the full detail.")

        # narrative recap (plain-language story under the graph)
        with st.expander("📖 Read this journey as a story"):
            for s in tl.story.tolist():
                st.markdown(f"- {s}")
        with st.expander("📋 Full event table"):
            show = tl[["event_date", "event_type", "details", "extra"]].copy()
            show["event_date"] = show["event_date"].dt.date
            st.dataframe(show.rename(columns={"event_date": "Date", "event_type": "Event",
                                              "details": "Details", "extra": "Code/Severity/$"}),
                         use_container_width=True, hide_index=True)

    render_ai_panel("Patient Journey Explorer", {
        "patient_id": pid,
        "profile": pc,
        "timeline_events": df_ctx(tl, 80),
    }, key=f"journey_{pid}")


# ══════════════════════════════════════════
# PAGE 3 — DIAGNOSIS INTELLIGENCE
# ══════════════════════════════════════════
elif page == "Diagnosis Intelligence":
    st.markdown('<div class="page-header">Diagnosis Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Where diagnosis is slow, wrong or missing — and who to act on next.</div>',
                unsafe_allow_html=True)

    # ── Dashboard controls: date slicer + click-a-state cross-filter ──
    lo, hi = section_date_filter("dx")
    xf = crossfilter_chip("dx")
    sel_state = xf[1] if xf else None

    tab_a, tab_b, tab_c = st.tabs(["⏱ Time to Treatment", "🔍 Undiagnosed Suspects", "🚫 Diagnosed but Untreated"])

    with tab_a:
        c1, c2 = st.columns(2)
        df_d = bk.get_diagnosis_delay_by_region(lo, hi)
        with c1:
            if not df_d.empty:
                fig = px.bar(df_d, x="state", y="avg_delay_days",
                             color_discrete_sequence=["#2563eb"], text="avg_delay_days",
                             labels={"state": "State", "avg_delay_days": "Avg Days: Diagnosis → Treatment"})
                fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside", cliponaxis=False)
                highlight_bars(fig, df_d["state"].tolist(), sel_state, "#2563eb")
                ev = st.plotly_chart(chart_layout(fig, "Avg Time from Diagnosis to Treatment by State"),
                                     use_container_width=True, key=_ck("dx", "delay"), on_select="rerun")
                apply_crossfilter("dx", "State", clicked_value(ev))
                st.caption("After a doctor identifies the illness, how many days pass before the patient "
                           "actually starts medicine. **Click a state's bar** to focus every chart and table "
                           "in this section on that state.")
        with c2:
            df_m = bk.get_common_misdiagnoses(lo, hi, sel_state)
            if not df_m.empty:
                # the raw ICD descriptions are very long — shorten to a clean, readable label
                def _clean_dx(desc):
                    desc = str(desc)
                    main, _, tag = desc.partition(" [")
                    tag = tag.rstrip("]")
                    dur = {"TYPICAL SEIZURE UNDER 2 MIN": "under 2 min",
                           "PROLONGED SEIZURE 2-5 MIN": "2-5 min",
                           "STATUS EPILEPTICUS 5+ MIN": "5+ min"}.get(tag, "")
                    hard = "INTRACTABLE" in main and "NOT INTRACTABLE" not in main
                    typ = main.split(", NOT INTRACTABLE")[0].split(", INTRACTABLE")[0].title()
                    typ = (typ.replace("Focal Epilepsy With Simple Partial Seizures", "Focal – simple partial")
                              .replace("Focal Epilepsy With Complex Partial Seizures", "Focal – complex partial")
                              .replace("Epilepsy, Unspecified", "Epilepsy (unspecified)")
                              .replace("Absence Epileptic Syndrome", "Absence seizures")
                              .replace("Lennox-Gastaut Syndrome", "Lennox-Gastaut (LGS)"))
                    parts = [typ] + ([dur] if dur else []) + (["hard-to-treat"] if hard else [])
                    return " · ".join(parts)

                df_m = df_m.copy()
                df_m["label"] = df_m["disease_name"].map(_clean_dx)
                df_m = df_m.sort_values("misdiagnosis_count")
                fig = px.bar(df_m, y="label", x="misdiagnosis_count", orientation="h", text="misdiagnosis_count",
                             color_discrete_sequence=["#f59e0b"], custom_data=["disease_name"],
                             labels={"label": "", "misdiagnosis_count": "Patients"})
                fig.update_traces(texttemplate="%{x:,}", textposition="outside", cliponaxis=False,
                                  hovertemplate="%{customdata[0]}<br>%{x:,} patients<extra></extra>")
                fig.update_layout(yaxis=dict(automargin=True), margin=dict(l=10, r=55, t=44, b=10))
                st.plotly_chart(chart_layout(fig, "Top Diagnoses Recorded" +
                                             (f" — {sel_state}" if sel_state else " in Population")),
                                use_container_width=True)
                st.caption("The most common epilepsy types doctors recorded across all our patients. "
                           "\"Hard-to-treat\" means seizures that don't respond well to standard medicines; "
                           "the time (under 2 min / 2-5 min / 5+ min) is how long the seizures typically last. "
                           "Hover a bar for the full medical description.")

    df_und = bk.get_undiagnosed_suspects(lo, hi)
    df_und_v = df_und[df_und["state"] == sel_state] if sel_state and not df_und.empty else df_und
    with tab_b:
        c1, c2 = st.columns([2, 1])
        with c1:
            st.dataframe(df_und_v, use_container_width=True, height=CHART_H, hide_index=True)
        with c2:
            df_ur = bk.get_undiagnosed_by_region(lo, hi)
            if not df_ur.empty:
                fig = px.bar(df_ur, x="state", y="suspect_count", color_discrete_sequence=["#7c3aed"],
                             text="suspect_count", labels={"state": "State", "suspect_count": "Suspects"})
                fig.update_traces(texttemplate="%{y:,}", textposition="outside", cliponaxis=False)
                highlight_bars(fig, df_ur["state"].tolist(), sel_state, "#7c3aed")
                ev = st.plotly_chart(chart_layout(fig, "Suspects by State"), use_container_width=True,
                                     key=_ck("dx", "susp"), on_select="rerun")
                apply_crossfilter("dx", "State", clicked_value(ev))
                st.caption("Where the possibly-undiagnosed patients live — tells us where to run "
                           "testing campaigns. **Click a bar** to filter the whole section to that state.")

    df_untreated = bk.get_untreated_rare_patients(lo, hi)
    df_untreated_v = (df_untreated[df_untreated["state"] == sel_state]
                      if sel_state and not df_untreated.empty else df_untreated)
    with tab_c:
        st.markdown(f"**{len(df_untreated_v):,}** patients have a confirmed epilepsy diagnosis but never "
                    f"started therapy{f' in **{sel_state}**' if sel_state else ''}.")
        c1, c2 = st.columns([2, 1])
        with c1:
            st.dataframe(df_untreated_v, use_container_width=True, height=CHART_H, hide_index=True)
        with c2:
            # derive the by-state counts from the table above (no extra DB call needed)
            if not df_untreated.empty and "state" in df_untreated.columns:
                df_utr = (df_untreated.groupby("state").size()
                          .reset_index(name="untreated_count")
                          .sort_values("untreated_count", ascending=False))
                fig = px.bar(df_utr, x="state", y="untreated_count", color_discrete_sequence=["#f59e0b"],
                             text="untreated_count", labels={"state": "State", "untreated_count": "Untreated patients"})
                fig.update_traces(texttemplate="%{y:,}", textposition="outside", cliponaxis=False)
                highlight_bars(fig, df_utr["state"].tolist(), sel_state, "#f59e0b")
                ev = st.plotly_chart(chart_layout(fig, "Untreated patients by State"), use_container_width=True,
                                     key=_ck("dx", "untr"), on_select="rerun")
                apply_crossfilter("dx", "State", clicked_value(ev))
                st.caption("Where the diagnosed-but-untreated patients live — tells us which states to "
                           "target for treatment-initiation outreach. **Click a bar** to filter the section.")

    render_ai_panel("Diagnosis Intelligence", {
        "active_filters": {"date_range": [lo, hi], "state": sel_state},
        "diagnosis_delay_by_state": df_ctx(df_d),
        "common_misdiagnoses": df_ctx(df_m),
        "undiagnosed_suspects": df_ctx(df_und_v, 40),
        "diagnosed_but_untreated": df_ctx(df_untreated_v, 40),
    }, key="dx")

# ══════════════════════════════════════════
# PAGE 4 — TREATMENT & ADHERENCE
# ══════════════════════════════════════════
elif page == "Treatment & Adherence":
    st.markdown('<div class="page-header">Treatment Initiation & Adherence</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">For our epilepsy patients: conversion from diagnosis to therapy, '
                'drop-off, and drug switching.</div>', unsafe_allow_html=True)

    # ── Dashboard controls: date slicer + click-a-reason cross-filter ──
    lo, hi = section_date_filter("tx")
    xf = crossfilter_chip("tx")
    sel_reason = xf[1] if xf else None

    funnel = bk.get_treatment_funnel(lo, hi)
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric("Diagnosed", f"{funnel['Diagnosed']:,}")
        st.metric("Started Treatment", f"{funnel['Treated']:,}")
        st.metric("Currently Adherent", f"{funnel['Adherent']:,}")
        st.metric("⚠ Untreated Gap", f"{funnel['Diagnosed'] - funnel['Treated']:,}")
    with c2:
        fig = go.Figure(go.Funnel(
            y=["Diagnosed", "Treated", "Adherent"],
            x=[funnel["Diagnosed"], funnel["Treated"], funnel["Adherent"]],
            marker=dict(color=["#2563eb", "#0d9488", "#06b6d4"]),
            texttemplate="%{value:,} (%{percentInitial:.0%})"))
        st.plotly_chart(chart_layout(fig, "Epilepsy Treatment Funnel"), use_container_width=True)
        st.caption("This funnel is for our **epilepsy** patients (all seizure types, including "
                   "Lennox-Gastaut Syndrome). Of everyone diagnosed with epilepsy, how many actually "
                   "started an anti-seizure medicine, and how many are still taking it today. Each drop "
                   "is patients (and revenue) we lose along the way.")

    df_reasons = bk.get_discontinuation_by_reason(lo, hi)
    if not df_reasons.empty:
        fig = px.bar(df_reasons, x="reason", y="count", color_discrete_sequence=["#7c3aed"],
                     text="count", labels={"reason": "Reason", "count": "Patients"})
        fig.update_traces(texttemplate="%{y:,}", textposition="outside", cliponaxis=False)
        highlight_bars(fig, df_reasons["reason"].tolist(), sel_reason, "#7c3aed")
        ev = st.plotly_chart(chart_layout(fig, "Why Patients Switch / Discontinue", 300),
                             use_container_width=True, key=_ck("tx", "reasons"), on_select="rerun")
        apply_crossfilter("tx", "Switch reason", clicked_value(ev))
        st.caption("The reasons patients gave for changing or stopping their medicine — cost, side effects, "
                   "insurance problems, or the medicine not working well enough. **Click a bar** to filter "
                   "the switching flow and tables below to just that reason.")

    # ── Drug switching flow: strict left→right (bipartite) so there are never loops ──
    df_flow = bk.get_switch_flow(lo, hi)
    if sel_reason and not df_flow.empty:
        df_flow = df_flow[df_flow["switch_reason"] == sel_reason]
    if not df_flow.empty:
        agg = df_flow.groupby(["source", "target"])["value"].sum().reset_index()
        agg = agg.sort_values("value", ascending=False).head(15)   # only the biggest flows
        # separate "from" nodes (left) from "to" nodes (right) — a drug can appear on both
        # sides but as two distinct nodes, which makes loop-backs impossible.
        sources = list(pd.unique(agg["source"]))
        targets = list(pd.unique(agg["target"]))
        left_idx = {n: i for i, n in enumerate(sources)}
        right_idx = {n: i + len(sources) for i, n in enumerate(targets)}
        labels = sources + targets
        OURS = {"BANZEL", "FYCOMPA"}
        RIVALS = {"EPIDIOLEX", "ONFI", "XCOPRI", "KEPPRA", "VIMPAT", "BRIVIACT", "FINTEPLA", "NAYZILAM"}
        node_color = ["#2563eb" if n in OURS else "#f59e0b" if n in RIVALS else "#94a3b8" for n in labels]
        fig = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(pad=24, thickness=18, label=labels, color=node_color,
                      line=dict(color="#e2e8f0", width=0.5)),
            link=dict(source=agg["source"].map(left_idx), target=agg["target"].map(right_idx),
                      value=agg["value"], color="rgba(37,99,235,0.20)")))
        fig.update_layout(font=dict(size=13))
        st.markdown("**Where Patients Move: Old Medicine → New Medicine**"
                    + (f" — filtered to reason: *{sel_reason}*" if sel_reason else ""))
        # colour key as a labelled line above the chart (no overlap with the diagram)
        st.markdown(
            '<div style="font-size:12.5px;color:#475569;margin:2px 0 4px;">'
            '<span style="display:inline-block;width:11px;height:11px;background:#2563eb;'
            'border-radius:2px;margin-right:5px;"></span>Our brands (Banzel, Fycompa)'
            '&nbsp;&nbsp;&nbsp;<span style="display:inline-block;width:11px;height:11px;'
            'background:#f59e0b;border-radius:2px;margin-right:5px;"></span>Competitor brands'
            '&nbsp;&nbsp;&nbsp;<span style="display:inline-block;width:11px;height:11px;'
            'background:#94a3b8;border-radius:2px;margin-right:5px;"></span>Other medicines</div>',
            unsafe_allow_html=True)
        st.plotly_chart(chart_layout(fig, "", 540), use_container_width=True)
        st.caption("Read left to right: the **left column** is the medicine patients were taking, the "
                   "**right column** is the medicine they switched to. Each flowing ribbon is a group of "
                   "patients making that switch — the thicker the ribbon, the more patients moved. "
                   "Only the 15 biggest switches are shown so it stays readable.")

    # ── AI Switch Analyst: agentic — no manual filtering. Ask, or one-click a play. ──
    st.markdown("### 🤖 AI Switch Analyst")
    st.caption("No filters to fiddle with. Click a play (or ask in plain English) and the agent finds the exact "
               "patients behind the switches and proposes win-back actions for your approval.")

    def _run_switch_agent(question):
        if not config.api_key_is_set():
            st.session_state["switch_ans"] = "⚠️ Set your Claude API key in `.env` to use the AI analyst."
            return
        with st.spinner("Agent analysing drug switches and preparing win-back actions..."):
            ans, proposed = agent.handle_query(question)
            st.session_state["switch_ans"] = ans
            st.session_state["switch_proposed"] = proposed

    p1, p2, p3 = st.columns(3)
    with p1:
        if st.button("🎯 Win back our biggest losses", use_container_width=True):
            _run_switch_agent(
                "Find patients who switched AWAY from one of our EISAI medicines (Fact_Drug_Switches where "
                "from_drug_id is a drug with MANUFACTURER='EISAI'), showing patient_id, which drug they left, "
                "which drug they moved to, the reason, and the lost annual revenue (estimate from that drug's "
                "average PLAN_PAY+PATIENT_PAY x 12). Rank by lost revenue, list the top 5 patient_ids, and "
                "propose a win-back action for each with its estimated_value_usd.")
    with p2:
        if st.button("💸 Rescue cost-driven switchers", use_container_width=True):
            _run_switch_agent(
                "Find patients who switched for reason 'Cost' or 'Insurance' in Fact_Drug_Switches (join to get "
                "drug names and patient state). List the top 5 patient_ids and, for each, propose enrolling them "
                "in a patient support / copay-assistance program to reduce out-of-pocket cost.")
    with p3:
        if st.button("⚠️ Follow up side-effect switchers", use_container_width=True):
            _run_switch_agent(
                "Find patients who switched for reason 'Side Effect' in Fact_Drug_Switches. List the top 5 "
                "patient_ids with the drug they left, and propose a clinical safety follow-up appointment for each.")

    q = st.chat_input("Ask about drug switching… e.g. 'who left Keppra for cost in Texas?'")
    if q:
        _run_switch_agent(
            q + " (This is about drug switching. Use Fact_Drug_Switches joined to [DRUG DIM] for drug names and "
            "[PTNT DIM] for patient state. List patient_ids and propose the appropriate win-back action for each.)")

    if st.session_state.get("switch_ans"):
        st.markdown(st.session_state["switch_ans"])
        if st.session_state.get("switch_proposed"):
            st.success(f"🕓 {st.session_state['switch_proposed']} action(s) proposed — approve them in ⚡ Action Center.")

    df_disc = bk.get_discontinued_our_drug(lo, hi)
    if sel_reason and not df_disc.empty:
        df_disc = df_disc[df_disc["switch_reason"] == sel_reason]
    with st.expander(f"📉 Patients who switched away from OUR medicines ({len(df_disc):,})"
                     + (f" — reason: {sel_reason}" if sel_reason else "")):
        st.dataframe(df_disc, use_container_width=True, hide_index=True)

    render_ai_panel("Treatment & Adherence", {
        "active_filters": {"date_range": [lo, hi], "switch_reason": sel_reason},
        "treatment_funnel": funnel,
        "adherence_breakdown": df_ctx(bk.get_adherence_breakdown()),
        "switch_reasons": df_ctx(df_reasons),
        "switch_flow": df_ctx(df_flow, 40),
        "discontinued_our_drug_patients": df_ctx(df_disc, 40),
    }, key="tx")

# ══════════════════════════════════════════
# PAGE 5 — MARKET ACCESS & PAYER
# ══════════════════════════════════════════
elif page == "Market Access & Payer":
    st.markdown('<div class="page-header">Payer Intelligence (Insurance Companies)</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">A payer is the insurance company that pays for a patient\'s medicine. '
                'This page shows which insurers pay smoothly, which refuse claims, and how much of our money '
                'is stuck with them.</div>', unsafe_allow_html=True)

    # ── Dashboard controls: date slicer + insurer focus (selectbox OR click a bar) ──
    lo, hi = section_date_filter("payer")
    sel_payer = st.selectbox("🔍 Focus on one insurer (all charts below update together)",
                             ["All"] + bk.get_payer_list())
    xf = crossfilter_chip("payer")
    eff_payer = xf[1] if xf else sel_payer          # a clicked bar wins over the dropdown

    df_p_all = bk.get_payer_denial_rates(None, lo, hi)      # every insurer (for the clickable chart)
    df_p = df_p_all if eff_payer == "All" else df_p_all[df_p_all["payer_name"] == eff_payer]
    df_r = bk.get_denial_reasons(eff_payer, lo, hi)
    df_dc = bk.get_denied_claims_patients(eff_payer, lo, hi)
    df_auth = bk.get_auth_delay_by_state(eff_payer, lo, hi)

    # payer scorecard
    if not df_p.empty:
        tot_billed = df_p["total_billed"].sum(); tot_paid = df_p["total_paid"].sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Claims submitted", f"{int(df_p['total_claims'].sum()):,}")
        c2.metric("We billed", f"${tot_billed/1e6:,.2f}M")
        c3.metric("Insurer paid", f"${tot_paid/1e6:,.2f}M")
        c4.metric("Stuck in denials", f"${df_p['denied_value'].sum()/1e6:,.2f}M",
                  delta=f"-{int(df_p['denied'].sum()):,} claims", delta_color="normal")
        st.caption("How much we invoiced insurers, how much they actually paid, and the value of claims they refused.")

    c1, c2 = st.columns(2)
    with c1:
        if not df_p_all.empty:
            dfp = df_p_all.sort_values("denial_pct").tail(15)
            fig = px.bar(dfp, y="payer_name", x="denial_pct", orientation="h",
                         text=dfp["denial_pct"].map(lambda v: f"{v:.0f}%"),
                         color_discrete_sequence=["#2563eb"],
                         hover_data=["total_claims", "avg_auth_days"],
                         labels={"payer_name": "", "denial_pct": "% of claims refused"})
            fig.update_traces(textposition="outside", cliponaxis=False)
            fig.update_layout(yaxis=dict(automargin=True), margin=dict(l=10, r=50, t=44, b=10))
            if eff_payer != "All":
                highlight_bars(fig, dfp["payer_name"].tolist(), eff_payer, "#2563eb")
            ev = st.plotly_chart(chart_layout(fig, "Which insurers refuse to pay most often"),
                                 use_container_width=True, key=_ck("payer", "refuse"), on_select="rerun")
            apply_crossfilter("payer", "Insurer", clicked_value(ev, "y"))
            st.caption("Out of every 100 claims sent to this insurer, how many they refused to pay. "
                       "Longer bar = harder to get paid. **Click a bar** to focus the whole page on that insurer.")
    with c2:
        if not df_r.empty:
            fig = px.bar(df_r, y="denial_reason", x="count", orientation="h", text="count",
                         color_discrete_sequence=["#f59e0b"],
                         labels={"denial_reason": "", "count": "Refused claims"})
            fig.update_traces(texttemplate="%{x:,}", textposition="outside", cliponaxis=False)
            fig.update_layout(yaxis=dict(automargin=True), margin=dict(l=10, r=45, t=44, b=10))
            st.plotly_chart(chart_layout(fig, "Why they refused to pay"), use_container_width=True)
            st.caption("The excuses insurers gave for not paying — e.g. 'prior authorization' means they "
                       "demanded extra approval paperwork before covering the medicine.")

    if not df_auth.empty:
        fig = px.bar(df_auth, x="state", y="avg_auth_days", color_discrete_sequence=["#7c3aed"],
                     text="avg_auth_days", labels={"state": "State", "avg_auth_days": "Avg waiting days"})
        fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside", cliponaxis=False)
        st.plotly_chart(chart_layout(fig, "How long patients wait for insurer approval, by state", 300),
                        use_container_width=True)
        st.caption("Average days a patient waits before the insurer approves their medicine. "
                   "Long waits delay treatment and revenue.")

    st.markdown(f"#### 💸 Refused claims — {len(df_dc)} patients, ${df_dc['claim_amount'].sum():,.0f} recoverable"
                if not df_dc.empty else "#### No refused claims 🎉")
    if not df_dc.empty:
        st.caption("Every refused claim with the patient, insurer, amount and reason — these can often be "
                   "won back by appealing.")
        st.dataframe(df_dc, use_container_width=True, height=300, hide_index=True)

    render_ai_panel("Market Access & Payer", {
        "active_filters": {"date_range": [lo, hi], "focused_payer": eff_payer},
        "payer_scorecard": df_ctx(df_p),
        "denial_reasons": df_ctx(df_r),
        "auth_delay_by_state": df_ctx(df_auth),
        "denied_claims_patients": df_ctx(df_dc, 40),
    }, key="payer")

# ══════════════════════════════════════════
# PAGE 6 — PHYSICIAN & GEO
# ══════════════════════════════════════════
elif page == "Physician & Geo Intelligence":
    st.markdown('<div class="page-header">Physician (Provider) Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">A provider is the doctor who treats patients and writes prescriptions. '
                'This page shows which doctors treat our patients, their specialties, and the sales each one '
                'generates — so we know who to build relationships with.</div>', unsafe_allow_html=True)

    # ── Dashboard controls: date slicer + click-to-cross-filter (specialty bar / map state) ──
    lo, hi = section_date_filter("geo")
    df_phys_all = bk.get_physician_performance(lo, hi)

    # one filter set drives EVERY chart & table on this page
    f1, f2 = st.columns(2)
    with f1:
        sel_spec = st.selectbox("🔍 Specialty (all charts below update together)",
                                ["All"] + sorted(df_phys_all["specialty"].dropna().unique().tolist()))
    with f2:
        sel_state = st.selectbox("State", ["All"] + sorted(df_phys_all["region"].dropna().unique().tolist()))
    xf = crossfilter_chip("geo")                    # ("Specialty", v) or ("State", v) from a chart click
    if xf and xf[0] == "Specialty":
        sel_spec = xf[1]
    if xf and xf[0] == "State":
        sel_state = xf[1]
    df_phys = df_phys_all.copy()
    if sel_spec != "All":
        df_phys = df_phys[df_phys["specialty"] == sel_spec]
    if sel_state != "All":
        df_phys = df_phys[df_phys["region"] == sel_state]

    c1, c2 = st.columns(2)
    with c1:
        if not df_phys.empty:
            fig = px.scatter(df_phys, x="patient_volume", y="total_revenue", size="active_patients",
                             color="specialty", hover_name="doctor_name",
                             hover_data={"clinic_name": True, "region": True, "patient_volume": ":,",
                                         "total_revenue": ":$,.0f", "active_patients": ":,"},
                             color_discrete_sequence=COLORS,
                             labels={"patient_volume": "Patients treated", "total_revenue": "Sales generated ($)"})
            st.plotly_chart(chart_layout(fig, "Doctors: patients treated vs sales generated"), use_container_width=True)
            st.caption("Each bubble is one doctor. Right = treats more of our patients; higher = generates "
                       "more sales; bigger bubble = more patients still actively on treatment.")
    with c2:
        if not df_phys.empty:
            spec = (df_phys.groupby("specialty", as_index=False)
                    .agg(doctors=("doctor_name", "count"), patients=("patient_volume", "sum"),
                         revenue=("total_revenue", "sum")).sort_values("revenue"))
            fig = px.bar(spec, y="specialty", x="revenue", orientation="h", text="doctors",
                         color_discrete_sequence=["#0d9488"],
                         labels={"specialty": "", "revenue": "Sales generated ($)"})
            fig.update_traces(texttemplate="%{text} drs", textposition="outside", cliponaxis=False)
            fig.update_layout(yaxis=dict(automargin=True), margin=dict(l=10, r=60, t=44, b=10))
            if sel_spec != "All":
                highlight_bars(fig, spec["specialty"].tolist(), sel_spec, "#0d9488")
            ev = st.plotly_chart(chart_layout(fig, "Sales by doctor specialty"), use_container_width=True,
                                 key=_ck("geo", "spec"), on_select="rerun")
            apply_crossfilter("geo", "Specialty", clicked_value(ev, "y"))
            st.caption("Which type of doctor drives our sales — e.g. heart specialists vs family doctors. "
                       "The number on each bar = how many doctors of that specialty. **Click a bar** to "
                       "focus the whole page on that specialty.")

    # ── Manufacturer loyalty: whose medicines does each doctor prescribe? ──
    MFR_COLORS = {"EISAI": "#2563eb"}   # our company always in brand blue
    _mfr_palette = ["#0d9488", "#f59e0b", "#7c3aed", "#06b6d4", "#ec4899", "#84cc16", "#6366f1", "#94a3b8"]
    df_mix_all = bk.get_physician_manufacturer_mix(lo, hi)
    df_mix = df_mix_all[df_mix_all["practitioner_id"].isin(df_phys["practitioner_id"])] \
        if not df_mix_all.empty else df_mix_all
    if not df_mix.empty:
        top_docs = (df_mix.groupby("doctor_name")["revenue"].sum()
                    .sort_values(ascending=False).head(12).index.tolist())
        df_top = df_mix[df_mix["doctor_name"].isin(top_docs)].copy()
        # 20+ manufacturers exist — keep EISAI (us) + the 6 biggest others, group the rest
        big = (df_top.groupby("manufacturer")["revenue"].sum()
               .sort_values(ascending=False).head(7).index.tolist())
        keep = set(big) | {"EISAI"}
        df_top["manufacturer"] = df_top["manufacturer"].map(lambda m: m if m in keep else "OTHERS")
        df_top = (df_top.groupby(["practitioner_id", "doctor_name", "manufacturer"], as_index=False)
                  .agg(fills=("fills", "sum"), revenue=("revenue", "sum")))
        # stable colour per manufacturer: UCB blue, competitors from the palette, OTHERS gray
        MFR_COLORS.setdefault("OTHERS", "#94a3b8")
        for i, m in enumerate(sorted(df_top["manufacturer"].unique())):
            MFR_COLORS.setdefault(m, _mfr_palette[i % len(_mfr_palette)])
        order = (df_top.groupby("doctor_name")["revenue"].sum().sort_values().index.tolist())
        fig = px.bar(df_top, y="doctor_name", x="revenue", color="manufacturer",
                     orientation="h", color_discrete_map=MFR_COLORS,
                     category_orders={"doctor_name": order},
                     hover_data={"fills": ":,", "revenue": ":$,.0f"},
                     labels={"doctor_name": "", "revenue": "Sales ($)", "manufacturer": "Manufacturer",
                             "fills": "Prescriptions"})
        fig.update_layout(yaxis=dict(automargin=True), legend=dict(orientation="h", y=-0.15),
                          margin=dict(l=10, r=10, t=44, b=10))
        st.plotly_chart(chart_layout(fig, "Top doctors — whose medicines do they prescribe?", 460),
                        use_container_width=True)
        st.caption("Each bar is one of the top prescribing doctors; the colored segments show how much "
                   "of their prescribing money goes to each drug **manufacturer** (drug maker). "
                   "**Blue = EISAI (us)** — the bigger the blue part, the more loyal the doctor is to our "
                   "medicines (Banzel, Fycompa). A doctor with a long bar but a small blue slice prescribes "
                   "a lot overall but mostly competitors' drugs — exactly who our sales team should visit. "
                   "Hover a segment for the exact sales and prescription count.")

    # ── Doctor profile: everything about one provider ──
    st.markdown("#### 👨‍⚕️ Doctor profile — pick a doctor for full details")
    if not df_phys.empty:
        opts = {f"{r.doctor_name} · {r.specialty} · {r.region}": r.practitioner_id
                for r in df_phys.itertuples()}
        sel_doc = st.selectbox("Doctor", list(opts.keys()))
        row = df_phys[df_phys["practitioner_id"] == opts[sel_doc]].iloc[0]
        doc_mix = (df_mix_all[df_mix_all["practitioner_id"] == int(row["practitioner_id"])]
                   .copy() if not df_mix_all.empty else pd.DataFrame())
        our_share = None
        if not doc_mix.empty and doc_mix["revenue"].sum() > 0:
            our_share = 100.0 * doc_mix.loc[doc_mix["manufacturer"] == "EISAI", "revenue"].sum() / doc_mix["revenue"].sum()
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Specialty", str(row["specialty"]).title())
        m2.metric("Patients treated", f"{int(row['patient_volume']):,}")
        m3.metric("Still on treatment", f"{int(row['active_patients']):,}",
                  help="Patients of this doctor who filled a prescription in the last 120 days (ongoing).")
        m4.metric("Stopped treatment", f"{int(row['discontinued']):,}")
        m5.metric("Sales generated", f"${row['total_revenue']:,.0f}")
        m6.metric("EISAI share (us)", f"{our_share:.0f}%" if our_share is not None else "—",
                  help="Of every $ this doctor prescribes, the share going to OUR (EISAI) medicines "
                       "Banzel & Fycompa. Low share + high volume = a doctor worth engaging.")
        p1, p2 = st.columns([3, 2])
        with p1:
            dd = bk.get_doctor_drugs(int(row["practitioner_id"]), lo, hi)
            if not dd.empty:
                st.caption(f"Medicines Dr. {str(row['doctor_name']).title()} prescribes, and the sales each brings us:")
                st.dataframe(dd, use_container_width=True, hide_index=True)
        with p2:
            if not doc_mix.empty:
                doc_mix["share"] = (100 * doc_mix["revenue"] / doc_mix["revenue"].sum()).round(1)
                doc_mix = doc_mix.sort_values("share")
                for i, m in enumerate(sorted(doc_mix["manufacturer"].unique())):
                    MFR_COLORS.setdefault(m, _mfr_palette[i % len(_mfr_palette)])
                fig = px.bar(doc_mix, y="manufacturer", x="share", orientation="h", text="share",
                             color="manufacturer", color_discrete_map=MFR_COLORS,
                             labels={"manufacturer": "", "share": "% of this doctor's prescribing ($)"})
                fig.update_traces(texttemplate="%{x:.0f}%", textposition="outside", cliponaxis=False)
                fig.update_layout(showlegend=False, yaxis=dict(automargin=True),
                                  margin=dict(l=10, r=40, t=44, b=10))
                st.plotly_chart(chart_layout(fig, "This doctor's prescribing by manufacturer", 300),
                                use_container_width=True)
                st.caption("How this doctor's prescription money splits across drug makers — "
                           "blue is us (EISAI), everything else is a competitor.")
    else:
        st.info("No doctors match this filter.")

    st.markdown("---")
    c1, c2 = st.columns(2)
    df_geo = bk.get_geo_combined(lo, hi)
    df_geo_v = df_geo[df_geo["state"] == sel_state] if sel_state != "All" and not df_geo.empty else df_geo
    with c1:
        if not df_geo_v.empty:
            fig = px.choropleth(
                df_geo_v, locations="state", locationmode="USA-states", scope="usa",
                color="total_revenue", color_continuous_scale="Blues",
                hover_name="state",
                hover_data={"state": False, "total_revenue": ":$,.0f",
                            "treated_patients": True, "total_marketing": ":$,.0f"},
                labels={"total_revenue": "Sales ($)", "treated_patients": "Patients treated",
                        "total_marketing": "Marketing spend ($)"})
            fig.update_geos(bgcolor="rgba(0,0,0,0)", lakecolor="#ffffff")
            ev = st.plotly_chart(chart_layout(fig, "Sales heatmap — where our medicine sells", 380),
                                 use_container_width=True, key=_ck("geo", "map_rev"), on_select="rerun")
            apply_crossfilter("geo", "State", clicked_value(ev, "location"))
            st.caption("A US map where each state is colored by how much medicine we sold there — "
                       "darker blue = more sales. Hover over a state to see its sales, patients "
                       "treated and marketing spend. Gray states have no data. **Click a state** to "
                       "focus the whole page on it.")
    with c2:
        if not df_geo_v.empty:
            fig = px.choropleth(
                df_geo_v, locations="state", locationmode="USA-states", scope="usa",
                color="total_marketing", color_continuous_scale="Oranges",
                hover_name="state",
                hover_data={"state": False, "total_marketing": ":$,.0f",
                            "total_revenue": ":$,.0f", "treated_patients": True},
                labels={"total_marketing": "Marketing spend ($)", "total_revenue": "Sales ($)",
                        "treated_patients": "Patients treated"})
            fig.update_geos(bgcolor="rgba(0,0,0,0)", lakecolor="#ffffff")
            ev = st.plotly_chart(chart_layout(fig, "Marketing spend heatmap — where our money goes", 380),
                                 use_container_width=True, key=_ck("geo", "map_mkt"), on_select="rerun")
            apply_crossfilter("geo", "State", clicked_value(ev, "location"))
            st.caption("The same map, but colored by how much we spent on marketing in each state — "
                       "darker orange = more spend. Compare it with the sales map on the left: a dark "
                       "orange state that is pale blue on the left is spending a lot but selling "
                       "little (rethink); a pale orange state that is dark blue is selling well "
                       "cheaply (invest more). **Click a state** to focus the whole page on it.")

    df_centers = bk.get_rare_disease_centers(lo, hi)
    if sel_state != "All" and not df_centers.empty:
        df_centers = df_centers[df_centers["region"] == sel_state]
    if sel_spec != "All" and not df_centers.empty:
        docs = set(df_phys["doctor_name"])
        df_centers = df_centers[df_centers["doctor_name"].isin(docs)]
    with st.expander("🏥 Top epilepsy-treatment centers (doctors treating seizure patients)"):
        st.dataframe(df_centers, use_container_width=True, hide_index=True)

    render_ai_panel("Physician & Geo Intelligence", {
        "active_filters": {"date_range": [lo, hi], "specialty": sel_spec, "state": sel_state},
        "physician_performance": df_ctx(df_phys, 30),
        "physician_manufacturer_mix": df_ctx(df_mix, 60),
        "geo_marketing_roi": df_ctx(df_geo_v),
        "specialty_centers": df_ctx(df_centers),
    }, key="geo")

# ══════════════════════════════════════════
# PAGE 7 — AI STRATEGY AGENT (conversational, tool-using)
# ══════════════════════════════════════════
elif page == "AI Strategy Agent":
    st.markdown('<div class="page-header">Claude — Pharma Strategy Agent</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Ask anything. The agent writes its own SQL, queries the live database, '
                'and proposes real actions (appointments, notifications, prescriptions) for your approval.</div>',
                unsafe_allow_html=True)

    st.markdown("""
    <div class="ai-box"><div class="ai-headline">💡 Try asking</div><div class="ai-body">
    • "Which patients have epilepsy but never started medicine? Set up outreach for the most urgent ones."<br>
    • "Who stopped taking Keppra and why? Propose win-back actions."<br>
    • "Which insurer refuses the most claims — appeal the biggest ones."<br>
    • "Where should we shift marketing spend next quarter?"<br>
    • "What did we discuss in our last conversation?"
    </div></div>""", unsafe_allow_html=True)

    # chat history lives in the Analyser Agent database (Agent_Chat_History) — survives restarts
    if "messages" not in st.session_state:
        try:
            hist = db.load_chat(30)
            st.session_state.messages = [
                {"role": r.role, "content": r.content} for r in hist.itertuples()]
        except Exception:
            st.session_state.messages = []
    if st.session_state.messages:
        st.caption(f"💾 {len(st.session_state.messages)} past messages loaded from the database "
                   "(table: Agent_Chat_History). Ask the agent about earlier conversations anytime.")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask the Strategy Agent..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Agent querying the database and reasoning..."):
                history = [{"role": m["role"], "content": m["content"]}
                           for m in st.session_state.messages[:-1]][-8:]
                answer, proposed = agent.handle_query(prompt, history=history)
                st.markdown(answer)
                if proposed:
                    st.success(f"🕓 {proposed} action(s) proposed — review & approve them in ⚡ Action Center.")
        st.session_state.messages.append({"role": "assistant", "content": answer})
        try:
            db.save_chat("user", prompt)
            db.save_chat("assistant", answer)
        except Exception:
            pass  # chat still works even if history write fails

# ══════════════════════════════════════════
# PAGE 8 — ACTION CENTER
# ══════════════════════════════════════════
elif page == "Action Center":
    st.markdown('<div class="page-header">⚡ Action Center</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Human-in-the-loop control room: approve AI-proposed actions, run the '
                'autonomous scan, and audit everything the system did.</div>', unsafe_allow_html=True)

    # ── Value Captured / ROI loop ──
    vm = actions.get_value_metrics()
    st.markdown(f"""
    <div class="kpi-row">
        <div class="kpi-card" style="border-top-color:#059669;"><div class="kpi-label">Value Captured</div>
            <div class="kpi-value">${vm['value_captured']:,.0f}</div><div class="kpi-sub green">from executed actions</div></div>
        <div class="kpi-card" style="border-top-color:#f59e0b;"><div class="kpi-label">Value Pending</div>
            <div class="kpi-value">${vm['value_pending']:,.0f}</div><div class="kpi-sub">awaiting your approval</div></div>
        <div class="kpi-card"><div class="kpi-label">Actions Executed</div>
            <div class="kpi-value">{vm['executed']:,}</div><div class="kpi-sub">{vm['auto_executed']} by Autopilot</div></div>
        <div class="kpi-card"><div class="kpi-label">Approval Rate</div>
            <div class="kpi-value">{vm['approval_rate']}%</div><div class="kpi-sub">rejections teach the agent</div></div>
        <div class="kpi-card"><div class="kpi-label">Patient Touchpoints</div>
            <div class="kpi-value">{vm['notifications'] + vm['appointments']:,}</div>
            <div class="kpi-sub">{vm['appointments']} appointments · {vm['notifications']} notifications</div></div>
    </div>""", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.2, 1.2, 2.6])
    with c1:
        if st.button("🚀 Deep Agent Scan (Claude)", type="primary", use_container_width=True):
            if not config.api_key_is_set():
                st.error("Set your Claude API key in `.env` first.")
            else:
                with st.spinner("Agent scanning the whole database for risks & opportunities..."):
                    report, proposed = run_autopilot_scan()
                    st.session_state["autopilot_report"] = report
                    st.session_state["autopilot_count"] = proposed
    with c2:
        if st.button("🐕 Re-run Watchdog Sweep", use_container_width=True):
            st.session_state.watchdog_summary = watchdog.sweep(
                auto_execute=st.session_state.get("autonomous_mode", True))
            st.rerun()
    with c3:
        st.caption("**Watchdog** (instant, runs itself on every app start): safety events, denied claims, "
                   "untreated patients → low-risk actions auto-execute in Autonomous Mode. "
                   "**Deep Scan** (Claude): full strategic analysis that also proposes business actions.")

    wd = st.session_state.get("watchdog_summary", {})
    if wd.get("messages"):
        with st.expander(f"🐕 Watchdog log — {len(wd['messages'])} detection(s), "
                         f"{wd.get('auto_executed', 0)} auto-executed"):
            for m in wd["messages"]:
                st.markdown(f"- {m}")

    if st.session_state.get("autopilot_report"):
        with st.expander(f"🧠 Latest scan report ({st.session_state.get('autopilot_count', 0)} actions proposed)",
                         expanded=True):
            st.markdown(st.session_state["autopilot_report"])

    tab1, tab_biz, tab2, tab3, tab4, tab5 = st.tabs(
        ["🕓 Patient Actions", "🏢 Business Actions", "📜 History", "📅 Appointments", "📧 Notifications", "🔍 Audit Log"])

    with tab1:
        pending = actions.get_pending_actions()
        if pending.empty:
            st.info("No pending actions. Run the agent scan or ask the AI Strategy Agent to find work.")
        else:
            ca, cb, _ = st.columns([1, 1, 3])
            with ca:
                if st.button("✅ Approve ALL", type="primary"):
                    for _, a in pending.iterrows():
                        actions.approve_action(int(a["action_id"]))
                    st.rerun()
            with cb:
                if st.button("❌ Reject ALL"):
                    for _, a in pending.iterrows():
                        actions.reject_action(int(a["action_id"]))
                    st.rerun()
            for _, a in pending.iterrows():
                val = float(a.get("estimated_value_usd", 0) or 0)
                val_txt = f' · <strong style="color:#059669;">~${val:,.0f}</strong>' if val else ""
                st.markdown(
                    f'<div class="action-card"><span class="action-title">#{a["action_id"]} · '
                    f'{a["action_type"].replace("_", " ").title()}</span> — patient #{a["patient_id"]} '
                    f'<em>(by {a["proposed_by"]})</em>{val_txt}<br>{a["details"]}<br>'
                    f'<em>Why: {a["reason"]}</em><br><em>Impact: {a["expected_impact"]}</em></div>',
                    unsafe_allow_html=True)
                b1, b2, _ = st.columns([1, 1, 4])
                with b1:
                    if st.button("✅ Approve & Execute", key=f"ac_ok_{a['action_id']}"):
                        st.success(actions.approve_action(int(a["action_id"])))
                        st.rerun()
                with b2:
                    if st.button("❌ Reject", key=f"ac_no_{a['action_id']}"):
                        actions.reject_action(int(a["action_id"]))
                        st.rerun()

    with tab_biz:
        biz_pending = actions.get_pending_business_actions()
        if biz_pending.empty:
            st.info("No pending business actions. The AI proposes these from marketing/payer/geo findings — "
                    "generate insights on those pages or run the agent scan.")
        else:
            for _, a in biz_pending.iterrows():
                val = float(a.get("estimated_value_usd", 0) or 0)
                val_txt = f' · <strong style="color:#059669;">~${val:,.0f}</strong>' if val else ""
                st.markdown(
                    f'<div class="action-card" style="border-left-color:#0d9488;">'
                    f'<span class="action-title">#{a["business_action_id"]} · '
                    f'{a["action_type"].replace("_", " ").title()}</span> — {a["target_type"]} '
                    f'<strong>{a["target"]}</strong> <em>(by {a["proposed_by"]})</em>{val_txt}<br>{a["details"]}<br>'
                    f'<em>Why: {a["reason"]}</em><br><em>Impact: {a["expected_impact"]}</em></div>',
                    unsafe_allow_html=True)
                b1, b2, _ = st.columns([1, 1, 4])
                with b1:
                    if st.button("✅ Approve & Execute", key=f"biz_ok_{a['business_action_id']}"):
                        st.success(actions.approve_business_action(int(a["business_action_id"])))
                        st.rerun()
                with b2:
                    if st.button("❌ Reject", key=f"biz_no_{a['business_action_id']}"):
                        actions.reject_business_action(int(a["business_action_id"]))
                        st.rerun()

    utc_note = ("🕒 **All dates & times below (created_at / resolved_at / sent_at) are in "
                "UTC** — the standard time zone, so timestamps are consistent no matter where "
                "the action was performed or who performed it (human or agent).")

    def _utc_headers(df):
        """Append ' (UTC)' to any timestamp column so the header itself says so."""
        if df is None or df.empty:
            return df
        ren = {c: f"{c} (UTC)" for c in ("created_at", "resolved_at", "sent_at") if c in df.columns}
        return df.rename(columns=ren) if ren else df

    with tab2:
        st.info(utc_note, icon="🕒")
        st.markdown("**Patient actions**")
        st.dataframe(_utc_headers(actions.get_action_history()), use_container_width=True, hide_index=True)
        st.markdown("**Business actions**")
        st.dataframe(_utc_headers(actions.get_business_action_history()), use_container_width=True, hide_index=True)
    with tab3:
        st.info(utc_note, icon="🕒")
        st.dataframe(_utc_headers(actions.get_appointments()), use_container_width=True, hide_index=True)
    with tab4:
        st.info(utc_note, icon="🕒")
        st.dataframe(_utc_headers(actions.get_notifications()), use_container_width=True, hide_index=True)
        st.caption("Notifications use de-identified aliases (patient_<id>@notify.pharma). "
                   "Configure SMTP in .env to send real email.")
    with tab5:
        st.info(utc_note, icon="🕒")
        st.dataframe(_utc_headers(actions.get_audit_log()), use_container_width=True, hide_index=True)
