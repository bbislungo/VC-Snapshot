# VC Snapshot — Streamlit MVP v11 (VC Preset Database + Auto‑Fill Fit Scorer)
# Focus of this update:
# - Preloaded DB of small/early‑stage European VC firms with public thesis fields (sector, stage, geo, check size)
# - "Load preset" button that auto‑fills the VC Fit form (you can still edit after loading)
# - Works with existing Save‑to‑Report flow
#
# How to use:
# 1) Replace your GitHub file VC_Snapshot.py with this version.
# 2) Streamlit Cloud will rebuild; open your app → "🎯 Target by VC — Measure Fit" → choose a preset → Load preset → tweak if needed → Score → Save Fit → Export.

import io, base64, json, re, os
from typing import Dict, List, Optional
from dataclasses import dataclass

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.ticker import FuncFormatter, PercentFormatter, MaxNLocator

import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import altair as alt

# Optional deps for extractor / parsing
try:
    import requests
    from bs4 import BeautifulSoup
except Exception:
    requests = None
    BeautifulSoup = None
try:
    import PyPDF2
except Exception:
    PyPDF2 = None
try:
    import docx  # python-docx
except Exception:
    docx = None
# ==== NEW IMPORTS (place after your current imports) ====
from datetime import datetime

# Simple guard to read env or sidebar secret box
def _env_or(value: str, fallback: str | None = None):
    v = os.environ.get(value)
    return v if (v and v.strip()) else fallback

# --- Colors & helpers for charts ---

COLOR_NAMES = {
    "mrr": "blue",
    "arr": "orange",
    "rev_nrr_m": "green",
    "rev_grr_m": "teal",
    "burn_multiple": "red"
}

def fmt_currency(x): 
    return "—" if pd.isna(x) else f"{currency}{x:,.0f}"

def fmt_pct1(x):
    return "—" if pd.isna(x) else f"{x*100:.1f}%"

# ---------------- Branding / Header ----------------
AUTHOR_NAME = "Emanuele Borsellino"
AUTHOR_EMAIL = "emabors@gmail.com"
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/emanuele-borsellino-712288348/"
AUTHOR_LOGO_URL = ""  # optional image URL

st.set_page_config(page_title="VC Snapshot — Analyze the Possible Funding of a Startup", layout="wide")
st.title("📊 VC Snapshot")
st.caption("Analyze Startups' Metrics, Sector Benchmark Valuation, Competitor Analysis, and VC Goodness of Fit. Built by Emanuele Borsellino.")
st.markdown(f"**About:** Hi, I’m {AUTHOR_NAME}, aspiring VC Analyst. I created, and I am still working on it, this tool as a project to get into VC Funding and Analysis. With more information that I can gain only by actually entering the world of VC, I can better this tool in order to make it a must-have one day.")

# --- Badge styles for benchmarks ---
st.markdown("""
<style>
.badge { display:inline-block; padding:6px 10px; border-radius:999px; font-size:12px; font-weight:600; }
.badge.green { background:#E8F5E9; color:#1B5E20; border:1px solid #A5D6A7; }
.badge.amber { background:#FFF8E1; color:#7C4A03; border:1px solid #FFE082; }
.badge.red   { background:#FFEBEE; color:#B71C1C; border:1px solid #EF9A9A; }
.bench-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:10px; }
.card { border:1px solid #eee; border-radius:12px; padding:12px; }
</style>
""", unsafe_allow_html=True)

# ---------------- Sidebar: Demo & Uploads ----------------
st.sidebar.markdown("### 📂 Demo Data")
demo_companies = "company,sector,stage,country\nDemoStartup,Fintech,Seed,Italy\nOtherCo,Developer Tools,Seed,Germany\n"
demo_kpis = (
    "date,company,mrr,new_mrr,expansion_mrr,churned_mrr,cash_balance,net_burn,customers,new_customers,churned_customers,gross_margin_pct,cac\n"
    "2025-04-01,DemoStartup,5000,1200,300,200,100000,15000,100,10,5,0.75,3000\n"
    "2025-05-01,DemoStartup,5600,1300,350,150,96000,15500,106,8,4,0.75,2900\n"
    "2025-06-01,DemoStartup,6200,1200,400,200,92000,16000,112,7,5,0.75,2800\n"
    "2025-07-01,DemoStartup,6800,1300,450,200,88000,16500,118,7,6,0.75,2700\n"
    "2025-04-01,OtherCo,7000,1600,500,300,120000,18000,90,8,6,0.78,2400\n"
    "2025-05-01,OtherCo,7600,1500,550,250,116000,18500,94,7,5,0.78,2350\n"
    "2025-06-01,OtherCo,8200,1400,600,300,112000,19000,98,6,6,0.78,2300\n"
    "2025-07-01,OtherCo,9000,1600,700,250,108000,19500,104,7,4,0.78,2250\n"
)
st.sidebar.download_button("Download companies.csv", demo_companies, file_name="companies.csv")
st.sidebar.download_button("Download kpis.csv", demo_kpis, file_name="kpis.csv")

st.sidebar.header("🔧 Data Uploads")
st.sidebar.caption("Keep uploads <10MB. Use headers matching the demo files.")
up_companies = st.sidebar.file_uploader("companies.csv", type=["csv"], accept_multiple_files=False)
up_kpis = st.sidebar.file_uploader("kpis.csv", type=["csv"], accept_multiple_files=False)

@st.cache_data
def load_csv(uploaded: Optional[io.BytesIO], fallback_csv: str) -> pd.DataFrame:
    try:
        if uploaded is not None:
            if getattr(uploaded, 'size', 0) > 10*1024*1024:
                st.error("File too large (>10MB). Please upload a smaller file.")
                return pd.DataFrame()
            try:
                return pd.read_csv(uploaded)
            except Exception:
                uploaded.seek(0)
                return pd.read_csv(uploaded, sep=';')
        return pd.read_csv(io.StringIO(fallback_csv))
    except Exception as e:
        st.error(f"Failed to load CSV: {e}")
        return pd.DataFrame()

@st.cache_data
def normalize_dates(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    if col in df.columns:
        try:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.to_period('M').dt.to_timestamp()
        except Exception:
            pass
    return df
# ==== NEW: Integrations (API keys or demo files) ====
st.sidebar.markdown("### 🔗 Integrations")

# Crunchbase
CB_DEFAULT = _env_or("CRUNCHBASE_API_KEY", "")
cb_key = st.sidebar.text_input("Crunchbase API Key", value=CB_DEFAULT, type="password", help="Needed for competitor auto-search")

# Stripe (live) or export files
STRIPE_DEFAULT = _env_or("STRIPE_API_KEY", "")
stripe_key = st.sidebar.text_input("Stripe Secret Key (live or test)", value=STRIPE_DEFAULT, type="password", help="Pull MRR/ARR from Stripe subscriptions")
stripe_export = st.sidebar.file_uploader("Stripe export (subscriptions.json / invoices.csv) — optional", type=["json","csv"])

# QuickBooks: we’ll accept a simple Cash+Burn CSV (date,cash_balance,net_burn) as a lightweight path
qb_export = st.sidebar.file_uploader("QuickBooks export (cash_burn.csv)", type=["csv"], help="Columns: date,cash_balance,net_burn")

# ---------------- Multiples (editable JSON) ----------------
DEFAULT_MULTIPLES: Dict[str, Dict[str, List[float]]] = {
    "Developer Tools": {"Pre-Seed":[8,12,18],"Seed":[6,10,15],"Series A":[5,8,12],"Series B+":[4,6,9]},
    "Fintech":         {"Pre-Seed":[10,15,22],"Seed":[8,12,18],"Series A":[6,10,14],"Series B+":[5,8,12]},
    "Consumer Subscriptions": {"Pre-Seed":[5,8,12],"Seed":[4,6,9],"Series A":[3,5,8],"Series B+":[2.5,4,6]},
}

st.sidebar.markdown("### 💲 Multiples (editable)")
mult_text = st.sidebar.text_area(
    "JSON (sector → stage → [low, mid, high])",
    value=json.dumps(DEFAULT_MULTIPLES, indent=2),
    height=150,
)
try:
    USER_MULTIPLES = json.loads(mult_text)
except Exception:
    USER_MULTIPLES = DEFAULT_MULTIPLES

# ---------------- Privacy & Public URL ----------------
st.sidebar.info("🔒 Privacy: Data stays in your Streamlit session. Not stored server-side.")
PUBLIC_URL = os.environ.get("PUBLIC_APP_URL", "https://vc-snapshot-bbislungo.streamlit.app")
st.sidebar.text_input("Public demo link", value=PUBLIC_URL)

# ---------------- Load Data ----------------
companies = load_csv(up_companies, demo_companies)
companies['stage'] = companies.get('stage', pd.Series(dtype='object')).astype('category')
companies['sector'] = companies.get('sector', pd.Series(dtype='object')).astype('category')

kpis = load_csv(up_kpis, demo_kpis)
kpis = normalize_dates(kpis, 'date')

# Defaults so later blocks never crash on first render
if 'competitors_df' not in st.session_state:
    st.session_state['competitors_df'] = pd.DataFrame()

# ---------------- Sidebar: Accounting (optional) ----------------
st.sidebar.header("📥 Accounting (optional)")
qb_file = st.sidebar.file_uploader("QuickBooks export (CSV)", type=["csv"], accept_multiple_files=False)

@st.cache_data
def load_qb(uploaded) -> pd.DataFrame:
    if not uploaded:
        return pd.DataFrame()
    try:
        df = pd.read_csv(uploaded)
    except Exception:
        uploaded.seek(0)
        df = pd.read_csv(uploaded, sep=';')

    # Normalize a date column to month-start timestamps
    date_col = None
    for cand in ['date', 'txn_date', 'Txn Date', 'Period', 'Month']:
        if cand in df.columns:
            date_col = cand
            break
    if date_col:
        df['date'] = pd.to_datetime(df[date_col], errors='coerce').dt.to_period('M').dt.to_timestamp()
    else:
        df['date'] = pd.NaT  # keep schema consistent

    return df

# Narrow to selected company later, but we can already enrich the table (Stripe needs a company row to exist)
# We’ll enrich after we pick the company to avoid confusion

# ---------------- KPI Engine ----------------

def safe_div(n, d):
    if isinstance(n, (pd.Series, np.ndarray)) or isinstance(d, (pd.Series, np.ndarray)):
        n_series = pd.Series(n)
        d_series = pd.Series(d).replace(0, np.nan)
        return n_series.astype(float).div(d_series)
    if d is None or (isinstance(d, float) and pd.isna(d)) or d == 0:
        return np.nan
    return n / d

REQUIRED = ['mrr','new_mrr','expansion_mrr','churned_mrr','cash_balance','net_burn','customers','new_customers','churned_customers','gross_margin_pct','cac']

def compute_metrics(df_in: pd.DataFrame) -> pd.DataFrame:
    if df_in.empty: return df_in
    df = df_in.sort_values('date').copy()
    for c in REQUIRED:
        if c not in df.columns:
            df[c] = np.nan
    df['arr'] = df['mrr'] * 12.0
    df['mrr_prev'] = df['mrr'].shift(1)
    df['customers_prev'] = df['customers'].shift(1)
    df['mrr_growth_mom'] = safe_div(df['mrr'] - df['mrr_prev'], df['mrr_prev'])
    df['cust_churn_rate_m'] = safe_div(df['churned_customers'], df['customers_prev'])
    df['rev_grr_m'] = 1.0 - safe_div(df['churned_mrr'], df['mrr_prev'])
    df['rev_nrr_m'] = safe_div(df['mrr_prev'] + df['expansion_mrr'] - df['churned_mrr'], df['mrr_prev'])
    df['arpu'] = safe_div(df['mrr'], df['customers'])
    df['contr_margin'] = df['arpu'] * df['gross_margin_pct']
    df['cac_payback_m'] = df.apply(lambda r: np.nan if pd.isna(r['contr_margin']) or r['contr_margin'] <= 0 else safe_div(r['cac'], r['contr_margin']), axis=1)
    df['ltv_simple'] = df.apply(lambda r: np.nan if pd.isna(r['cust_churn_rate_m']) or r['cust_churn_rate_m'] <= 0 else safe_div(r['contr_margin'], r['cust_churn_rate_m']), axis=1)
    df['net_new_arr'] = (df['arr'] - df['arr'].shift(1))
    df['burn_multiple'] = df.apply(lambda r: np.nan if pd.isna(r['net_burn']) or pd.isna(r['net_new_arr']) or r['net_new_arr'] <= 0 else r['net_burn'] / r['net_new_arr'], axis=1)
    df['runway_m'] = df.apply(lambda r: np.nan if pd.isna(r['cash_balance']) or pd.isna(r['net_burn']) or r['net_burn'] <= 0 else r['cash_balance'] / r['net_burn'], axis=1)
    df['rule_of_40'] = (df['mrr_growth_mom'] * 100.0) + ( - safe_div(df['net_burn'], df['mrr'].replace(0, np.nan)) * 100.0 )
    return df

# ==== NEW: Stripe & QuickBooks ingestion helpers ====
def _merge_qb_overrides(kpis_df: pd.DataFrame, qb_csv) -> pd.DataFrame:
    """If user provided a QuickBooks cash/burn CSV, override those columns by date."""
    if qb_csv is None:
        return kpis_df
    try:
        qb = pd.read_csv(qb_csv)
        qb['date'] = pd.to_datetime(qb['date'], errors='coerce').dt.to_period('M').dt.to_timestamp()
        base = kpis_df.copy()
        base['date'] = pd.to_datetime(base['date'], errors='coerce').dt.to_period('M').dt.to_timestamp()
        base = base.merge(qb[['date','cash_balance','net_burn']], on='date', how='left', suffixes=("","_qb"))
        # prefer QB values if present
        for c in ['cash_balance','net_burn']:
            base[c] = np.where(base[f"{c}_qb"].notna(), base[f"{c}_qb"], base[c])
            if f"{c}_qb" in base.columns:
                base.drop(columns=[f"{c}_qb"], inplace=True)
        return base
    except Exception as e:
        st.sidebar.warning(f"QuickBooks override failed: {e}")
        return kpis_df

def _ingest_stripe_to_kpis(kpis_df: pd.DataFrame, api_key: str | None, export_file) -> pd.DataFrame:
    """
    Update (append/override) MRR for the selected company using either:
    - Stripe API (live): sum active subscriptions amount for the last statement month
    - Stripe export: parse JSON/CSV snaphots to produce MRR by month
    We keep it simple: if ingestion produces rows for the current company+month, we overwrite mrr there.
    """
    company = kpis_df['company'].iloc[0] if not kpis_df.empty else None
    if company is None:
        return kpis_df

    # 1) Export first (cheapest)
    if export_file is not None:
        try:
            name = export_file.name.lower()
            if name.endswith(".json"):
                data = json.load(io.TextIOWrapper(export_file, encoding='utf-8'))
                # naive: sum "plan.amount" of active subs; Stripe exports vary widely, so we keep robust
                # map to a single recent month
                total = 0.0
                for sub in (data if isinstance(data, list) else data.get('data', [])):
                    if str(sub.get("status","")).lower() == "active":
                        amt = sub.get("plan",{}).get("amount")
                        if amt is None:
                            amt = sub.get("items",{}).get("data",[{}])[0].get("plan",{}).get("amount")
                        if amt is not None:
                            total += float(amt)/100.0
                if total > 0:
                    recent = (pd.Timestamp.today().to_period('M')).to_timestamp()
                    # Upsert a row for recent month
                    row = {
                        'date': recent, 'company': company, 'mrr': total,
                        'new_mrr': np.nan, 'expansion_mrr': np.nan, 'churned_mrr': np.nan
                    }
                    kpis_df = pd.concat([kpis_df, pd.DataFrame([row])], ignore_index=True)
            else:
                # invoices.csv route (very rough MRR monthly sum on 'amount_paid' for recent month)
                export_file.seek(0)
                inv = pd.read_csv(export_file)
                if 'created' in inv.columns:
                    inv['created'] = pd.to_datetime(inv['created'], errors='coerce')
                    month_mask = inv['created'].dt.to_period('M') == pd.Timestamp.today().to_period('M')
                    total = inv.loc[month_mask, 'amount_paid'].fillna(0).sum() / 100.0
                    if total > 0:
                        recent = (pd.Timestamp.today().to_period('M')).to_timestamp()
                        row = {'date': recent, 'company': company, 'mrr': total}
                        kpis_df = pd.concat([kpis_df, pd.DataFrame([row])], ignore_index=True)
        except Exception as e:
            st.sidebar.warning(f"Stripe export parse failed: {e}")

    # 2) Live API (optional)
    if api_key:
        try:
            import requests as _rq
            subs = _rq.get(
                "https://api.stripe.com/v1/subscriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"limit": 100},
                timeout=12,
            )
            subs.raise_for_status()
            data = subs.json().get("data", [])
            total = 0.0
            for s in data:
                if s.get("status") == "active":
                    items = (s.get("items") or {}).get("data", [])
                    if items:
                        amt = items[0].get("price", {}).get("unit_amount")
                        q = items[0].get("quantity") or 1
                        if amt is not None:
                            total += (float(amt) / 100.0) * float(q)
            if total > 0:
                recent = (pd.Timestamp.today().to_period('M')).to_timestamp()
                row = {'date': recent, 'company': company, 'mrr': total}
                kpis_df = pd.concat([kpis_df, pd.DataFrame([row])], ignore_index=True)
                st.sidebar.success(f"Stripe live pull: MRR approx {total:,.0f}")
        except Exception as e:
            st.sidebar.warning(f"Stripe live pull failed: {e}")

    return kpis_df

# Defaults so later blocks never crash on first render
if 'competitors_df' not in st.session_state:
    st.session_state['competitors_df'] = pd.DataFrame()

# Apply optional QuickBooks overrides and Stripe ingestion BEFORE computing metrics
kpis = _merge_qb_overrides(kpis, qb_export)
# ---------------- Valuation helpers ----------------

def get_multiple_band(sector: str, stage: str, multiples: Dict[str, Dict[str, List[float]]]):
    sec = multiples.get(sector) or {}
    band = sec.get(stage)
    if not band:
        band = [4.0, 8.0, 12.0] if stage in ("Seed", "Series A") else [6.0, 10.0, 15.0]
    return band

def quality_adjustment(row: pd.Series) -> float:
    if row is None or isinstance(row, float) and pd.isna(row):
        return 1.0
    adj = 1.0
    if pd.notna(row.get('rev_nrr_m')) and row['rev_nrr_m'] >= 1.05: adj *= 1.05
    if pd.notna(row.get('mrr_growth_mom')) and row['mrr_growth_mom'] >= 0.07: adj *= 1.05
    if pd.notna(row.get('burn_multiple')) and row['burn_multiple'] > 2.0: adj *= 0.90
    if pd.notna(row.get('rule_of_40')) and row['rule_of_40'] < 0: adj *= 0.95
    return adj

def _merge_qb_overrides(kpis_df: pd.DataFrame, qb_df: pd.DataFrame) -> pd.DataFrame:
    """
    If QuickBooks export is provided, override cash_balance and net_burn by date when available.
    This is a *best-effort* merge that works even without a company column in QB data.
    """
    if kpis_df is None or kpis_df.empty or qb_df is None or qb_df.empty:
        return kpis_df

    qb = qb_df.copy()

    # Try to detect and standardize columns
    def _find(colnames, options):
        for opt in options:
            if opt in colnames:
                return opt
        return None

    cols = [c.strip() for c in qb.columns]
    cash_src = _find(cols, ['cash_balance', 'Ending Cash Balance', 'Ending cash balance', 'Cash', 'Cash Balance'])
    burn_src = _find(cols, ['net_burn', 'Net Burn', 'Net burn', 'Net Operating Cash Flow', 'Net cash flow from ops'])

    if cash_src and cash_src != 'cash_balance':
        qb.rename(columns={cash_src: 'cash_balance'}, inplace=True)
    if burn_src and burn_src != 'net_burn':
        qb.rename(columns={burn_src: 'net_burn'}, inplace=True)

    keep_cols = ['date'] + [c for c in ['cash_balance', 'net_burn'] if c in qb.columns]
    qb = qb[keep_cols].copy()

    # If burn is negative outflow, convert to positive burn
    if 'net_burn' in qb.columns:
        qb['net_burn'] = pd.to_numeric(qb['net_burn'], errors='coerce')
        if qb['net_burn'].dropna().mean() < 0:
            qb['net_burn'] = -qb['net_burn']

    # Merge on date (SAFE: if you track multiple companies per file and QB has no company column,
    # this will override all companies for that date—so we only fill where QB has non-null values).
    merged = pd.merge(kpis_df, qb, on='date', how='left', suffixes=('', '_qb'))

    for c in ['cash_balance', 'net_burn']:
        qb_col = f'{c}_qb'
        if qb_col in merged.columns:
            merged[c] = merged[qb_col].combine_first(merged[c])
            merged.drop(columns=[qb_col], inplace=True)

    return merged

# --- SaaS Benchmarks (typical ranges; you can tweak later) ---
BENCHMARKS = {
    # metric_key: {kind, good, warn}  -> kind = 'gte' (higher is better) or 'lte' (lower is better)
    "rev_nrr_m":      {"label": "NRR (monthly)",         "kind": "gte", "good": 1.10, "warn": 1.00},   # ≥110% green, 100–109% amber
    "rev_grr_m":      {"label": "GRR (monthly)",         "kind": "gte", "good": 0.95, "warn": 0.90},   # ≥95% green
    "mrr_growth_mom": {"label": "MoM MRR Growth",        "kind": "gte", "good": 0.05, "warn": 0.03},   # ≥5% green, 3–5% amber
    "burn_multiple":  {"label": "Burn Multiple",         "kind": "lte", "good": 2.00, "warn": 3.00},   # ≤2 green, 2–3 amber
    "runway_m":       {"label": "Runway (months)",       "kind": "gte", "good": 12.0, "warn": 9.0},    # ≥12 green, 9–12 amber
    "cac_payback_m":  {"label": "CAC Payback (months)",  "kind": "lte", "good": 12.0, "warn": 18.0},   # ≤12 green, 12–18 amber
    "rule_of_40":     {"label": "Rule of 40",            "kind": "gte", "good": 40.0, "warn": 20.0},   # ≥40 green, 20–40 amber
}

# --- Helpers: benchmark evaluation + badge rendering ---
def bench_status(value, kind: str, good: float, warn: float):
    """
    Returns ('green'|'amber'|'red', is_valid_value)
    kind == 'gte' : green if value >= good, amber if >= warn, else red
    kind == 'lte' : green if value <= good, amber if <= warn, else red
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ("red", False)
    try:
        v = float(value)
    except Exception:
        return ("red", False)

    if kind == "gte":
        if v >= good: return ("green", True)
        if v >= warn: return ("amber", True)
        return ("red", True)
    else:  # 'lte'
        if v <= good: return ("green", True)
        if v <= warn: return ("amber", True)
        return ("red", True)

def fmt_value_for(metric_key: str, val):
    # Format % vs months vs raw number
    if metric_key in ("rev_nrr_m","rev_grr_m","mrr_growth_mom"):
        return "—" if pd.isna(val) else f"{val*100:.1f}%"
    elif metric_key in ("runway_m", "cac_payback_m", "rule_of_40"):
        return "—" if pd.isna(val) else f"{val:.1f}" if metric_key != "rule_of_40" else f"{val:.0f}"
    elif metric_key == "burn_multiple":
        return "—" if pd.isna(val) else f"{val:.2f}"
    else:
        return "—" if pd.isna(val) else f"{val}"

# ---------------- VC Preset Database ----------------
# NOTE: These presets reflect common public statements (sectors, stages, geos, check sizes) for small/early European funds.
# They are a convenience for auto-filling the form; always verify on the fund's website.
VC_PRESETS: Dict[str, Dict] = {
    "16VC": {"sectors":["AI","Tech","Developer Tools"], "stages":["Pre-Seed","Seed"], "geos":["Europe"], "min_check":250_000, "max_check":2_000_000},
    "Kima Ventures": {"sectors":["Any"], "stages":["Pre-Seed","Seed"], "geos":["Europe","France"], "min_check":150_000, "max_check":150_000},
    "Crew Capital": {"sectors":["SaaS","Infra","Fintech"], "stages":["Seed","Series A"], "geos":["Europe","US"], "min_check":500_000, "max_check":5_000_000},
    "Seedcamp": {"sectors":["SaaS","Fintech","AI","Marketplaces"], "stages":["Pre-Seed","Seed"], "geos":["Europe"], "min_check":100_000, "max_check":400_000},
    "Point Nine": {"sectors":["SaaS","Developer Tools"], "stages":["Seed","Series A"], "geos":["Europe"], "min_check":500_000, "max_check":3_000_000},
    "Backed VC": {"sectors":["Consumer","SaaS","AI"], "stages":["Pre-Seed","Seed"], "geos":["Europe"], "min_check":200_000, "max_check":1_500_000},
    "LocalGlobe": {"sectors":["Any"], "stages":["Pre-Seed","Seed"], "geos":["UK","Europe"], "min_check":200_000, "max_check":1_000_000},
    "Cherry Ventures": {"sectors":["SaaS","Consumer","AI"], "stages":["Pre-Seed","Seed"], "geos":["Europe"], "min_check":300_000, "max_check":2_000_000},
    "btov": {"sectors":["SaaS","Industrial Tech"], "stages":["Seed","Series A"], "geos":["DACH","Europe"], "min_check":500_000, "max_check":4_000_000},
    "Speedinvest": {"sectors":["Fintech","Industrial Tech","SaaS"], "stages":["Pre-Seed","Seed"], "geos":["Europe"], "min_check":300_000, "max_check":3_000_000},
}

# Build options from a base list + everything found in VC_PRESETS
BASE_SECTOR_OPTIONS = [
    "Fintech","Developer Tools","Consumer Subscriptions","AI","Infra",
    "SaaS","Marketplaces","Industrial Tech","Consumer","Tech","Health","Future of Work","Any"
]
BASE_STAGE_OPTIONS = ["Pre-Seed","Seed","Series A","Series B+"]

# Expand with any extra labels found in presets (keeps things future-proof)
preset_sectors = sorted({s for p in VC_PRESETS.values() for s in p.get("sectors", [])})
preset_stages  = sorted({s for p in VC_PRESETS.values() for s in p.get("stages", [])})

SECTOR_OPTIONS = sorted(set(BASE_SECTOR_OPTIONS) | set(preset_sectors))
STAGE_OPTIONS  = sorted(set(BASE_STAGE_OPTIONS)  | set(preset_stages))

@dataclass
class VCProfile:
    name: str
    sectors: List[str]
    stages: List[str]
    geos: List[str]
    min_check: Optional[float]
    max_check: Optional[float]

SECTOR_ALIASES = {
    'DevTools': ['Developer Tools','Developer Platform','Developer Infrastructure'],
    'Fintech': ['Fintech','Finance','Payments','Banking'],
    'Consumer': ['Consumer Subscriptions','Consumer','B2C'],
}
ALL_STAGES = ["Pre-Seed","Seed","Series A","Series B+"]

# ---------------- Fit scoring helpers ----------------

def _norm(s: str) -> str:
    return (s or '').strip().lower()

def sector_score(company_sector: str, vc_sectors: List[str]) -> (int, str):
    cs = _norm(company_sector)
    v = [_norm(x) for x in vc_sectors]
    if cs in v or 'any' in v:
        return 100, "Sector matches VC focus"
    for k, vals in SECTOR_ALIASES.items():
        if cs in [_norm(x) for x in vals] and _norm(k) in v:
            return 85, f"Sector close to {k}"
    return 55, "Sector outside explicit focus"

def stage_score(company_stage: str, vc_stages: List[str]) -> (int, str):
    s = ALL_STAGES
    idx = {s[i]: i for i in range(len(s))}
    if company_stage in vc_stages:
        return 100, "Stage matches VC focus"
    if company_stage in idx:
        for st in vc_stages:
            if st in idx and abs(idx[st]-idx[company_stage])==1:
                return 70, "Adjacent stage"
    return 40, "Stage misaligned"

def geo_score(country: str, vc_geos: List[str]) -> (int, str):
    c = _norm(country)
    v = [_norm(x) for x in vc_geos]
    if any(c==x for x in v):
        return 100, "Country match"
    if 'europe' in v and c:
        return 80, "Within Europe"
    return 50, "Geo unclear or outside focus"

def traction_score(latest: pd.Series) -> (int, List[str]):
    notes, parts = [], []
    nrr = latest.get('rev_nrr_m')
    if pd.notna(nrr):
        if nrr >= 1.10: parts.append(95); notes.append("NRR ≥110% (excellent)")
        elif nrr >= 1.00: parts.append(80); notes.append("NRR ~100% (good)")
        else: parts.append(55); notes.append("NRR <100% (watch churn)")
    mom = latest.get('mrr_growth_mom')
    if pd.notna(mom):
        if mom >= 0.08: parts.append(90); notes.append("MoM ≥8% (fast)")
        elif mom >= 0.03: parts.append(75); notes.append("MoM 3–8% (steady)")
        else: parts.append(55); notes.append("MoM <3% (slow)")
    bm = latest.get('burn_multiple')
    if pd.notna(bm):
        if bm < 1: parts.append(95); notes.append("Burn multiple <1 (elite)")
        elif bm <= 2: parts.append(80); notes.append("Burn multiple 1–2 (good)")
        else: parts.append(55); notes.append("Burn multiple >2 (inefficient)")
    rw = latest.get('runway_m')
    if pd.notna(rw):
        if rw >= 18: parts.append(85); notes.append("Runway ≥18m")
        elif rw >= 12: parts.append(75); notes.append("Runway 12–18m")
        else: parts.append(60); notes.append("Runway <12m")
    if not parts:
        return 60, ["Insufficient KPI data; using neutral baseline"]
    return int(np.mean(parts)), notes

def check_size_score(arr: float, vc_min: Optional[float], vc_max: Optional[float]) -> (int, str):
    if vc_min is None and vc_max is None:
        return 80, "No check size provided"
    est_round = max(0.5, min(10.0, arr/100000.0))  # rough € proxy
    if vc_min and est_round < (vc_min/1_000_000)*0.5:
        return 55, "Likely too early for VC check size"
    if vc_max and est_round > (vc_max/1_000_000)*2.0:
        return 60, "May be beyond preferred check size"
    return 85, "Check size compatible"

def compute_fit(company_meta: Dict, latest: pd.Series, vc: VCProfile) -> Dict:
    sec_s, sec_note = sector_score(company_meta.get('sector',''), vc.sectors)
    stg_s, stg_note = stage_score(company_meta.get('stage','Seed'), vc.stages)
    geo_s, geo_note = geo_score(company_meta.get('country',''), vc.geos)
    tr_s, tr_notes = traction_score(latest)
    cs_s, cs_note = check_size_score(float(latest.get('arr') or 0.0), vc.min_check, vc.max_check)
    weights = {"sector":0.25, "stage":0.2, "traction":0.4, "geo":0.05, "check":0.10}
    overall = int(sec_s*weights['sector'] + stg_s*weights['stage'] + tr_s*weights['traction'] + geo_s*weights['geo'] + cs_s*weights['check'])
    reasons = [sec_note, stg_note, geo_note, cs_note] + tr_notes
    return {"overall": overall, "breakdown": {"Sector": sec_s, "Stage": stg_s, "Traction": tr_s, "Geography": geo_s, "Check size": cs_s}, "reasons": reasons}

# ---------------- VC Fit Radar (helper) ----------------
def fit_radar_b64(breakdown: Dict[str, int], title: str = "VC Fit"):
    # Order axes consistently
    labels = ["Sector", "Stage", "Traction", "Geography", "Check size"]
    vals = [float(breakdown.get(k, 0)) for k in labels]
    # close the polygon
    labels_cycle = labels + [labels[0]]
    vals_cycle = vals + [vals[0]]

    import matplotlib.pyplot as plt
    import numpy as np, io, base64
    angles = np.linspace(0, 2*np.pi, len(labels), endpoint=False).tolist()
    angles += angles[:1]

    fig = plt.figure(figsize=(4.8, 4.2))
    ax = plt.subplot(111, polar=True)
    ax.set_theta_offset(np.pi / 2.0)
    ax.set_theta_direction(-1)

    ax.set_thetagrids(np.degrees(angles[:-1]), labels)
    ax.set_ylim(0, 100)
    ax.plot(angles, vals_cycle, color="#6c5ce7", linewidth=2)
    ax.fill(angles, vals_cycle, color="#6c5ce7", alpha=0.15)
    ax.set_title(title, pad=14)
    ax.grid(alpha=0.35)
    buf = io.BytesIO(); plt.tight_layout(); plt.savefig(buf, format="png", dpi=160); plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()

# ---------------- Main: Company selection & KPIs ----------------
st.markdown("---")
st.subheader("Company Overview")
col1, col2, col3 = st.columns([2,1,1])
with col1:
    selected_company = st.selectbox("Choose a company", companies['company'].unique().tolist())
with col2:
    currency = st.selectbox("Currency", ["€","$","£"], index=0)
with col3:
    pass

ck = kpis[kpis['company'] == selected_company].copy().sort_values('date')
metrics_df = compute_metrics(ck)
# Ensure these exist no matter what
if metrics_df is not None and len(metrics_df):
    latest = metrics_df.iloc[-1]
else:
    latest = None
meta = companies[companies['company'] == selected_company].iloc[0].to_dict() if not companies.empty else {}
sector = meta.get('sector', 'Generic')
stage = meta.get('stage', 'Seed')
country = meta.get('country', '')
latest = metrics_df.iloc[-1] if len(metrics_df) else None

fmt_pct = lambda x: "—" if pd.isna(x) else f"{x*100:.1f}%"
fmt_money = lambda x: "—" if pd.isna(x) else f"{currency}{x:,.0f}"

# If Stripe/exports present, enrich ONLY this company's KPI rows, then recompute metrics
if stripe_key or stripe_export is not None:
    ck = _ingest_stripe_to_kpis(ck, stripe_key, stripe_export)

metrics_df = compute_metrics(ck)

st.markdown("---")
left, right = st.columns(2)   # <-- define the columns first

with left:
    st.markdown("#### MRR & ARR over time")
    if not metrics_df.empty:
        base = alt.Chart(metrics_df).encode(x=alt.X('date:T', title='Date'))

        mrr_line = base.mark_line(strokeWidth=2, color=COLOR_NAMES["mrr"]).encode(
            y=alt.Y('mrr:Q', title=f'MRR ({currency})'),
            tooltip=[alt.Tooltip('date:T'), alt.Tooltip('mrr:Q', title='MRR', format=',')]
        )

        arr_line = base.mark_line(strokeDash=[4,3], strokeWidth=2, color=COLOR_NAMES["arr"]).encode(
            y=alt.Y('arr:Q', title=f'ARR ({currency})', axis=alt.Axis(titleColor=COLOR_NAMES["arr"], orient='right')),
            tooltip=[alt.Tooltip('date:T'), alt.Tooltip('arr:Q', title='ARR', format=',')]
        )

        chart = alt.layer(mrr_line, arr_line).resolve_scale(y='independent').properties(height=260)
        st.altair_chart(chart, use_container_width=True)
        st.caption(f"**Color key:** MRR = {COLOR_NAMES['mrr']}, ARR = {COLOR_NAMES['arr']}")
    else:
        st.write("No data")

with right:
    st.markdown("#### Retention & Burn metrics")
    if not metrics_df.empty:
        base = alt.Chart(metrics_df).encode(x=alt.X('date:T', title='Date'))

        nrr = base.mark_line(color=COLOR_NAMES["rev_nrr_m"], strokeWidth=2).encode(
            y=alt.Y('rev_nrr_m:Q', title='NRR (monthly)', axis=alt.Axis(format='%')),
            tooltip=[alt.Tooltip('date:T'), alt.Tooltip('rev_nrr_m:Q', title='NRR', format='.1%')]
        )

        grr = base.mark_line(color=COLOR_NAMES["rev_grr_m"], strokeWidth=2).encode(
            y=alt.Y('rev_grr_m:Q', title='GRR (monthly)', axis=alt.Axis(format='%')),
            tooltip=[alt.Tooltip('date:T'), alt.Tooltip('rev_grr_m:Q', title='GRR', format='.1%')]
        )

        retention = alt.layer(nrr, grr).resolve_scale(y='independent').properties(height=180)

        burn = base.mark_line(color=COLOR_NAMES["burn_multiple"], strokeWidth=2).encode(
            y=alt.Y('burn_multiple:Q', title='Burn multiple'),
            tooltip=[alt.Tooltip('date:T'), alt.Tooltip('burn_multiple:Q', title='Burn multiple', format='.2f')]
        ).properties(height=120)

        st.altair_chart(retention & burn, use_container_width=True)
        st.caption(
    f"**Color key:** NRR = {COLOR_NAMES['rev_nrr_m']}, GRR = {COLOR_NAMES['rev_grr_m']}, Burn multiple = {COLOR_NAMES['burn_multiple']}"
        )
    else:
        st.write("No data")

import textwrap

# --- Benchmarks vs SaaS norms ---
if latest is not None:
    st.markdown("#### ✅ Benchmarks vs. SaaS norms")
    items = []
    for key, spec in BENCHMARKS.items():
        label = spec["label"]
        kind  = spec["kind"]
        good  = spec["good"]
        warn  = spec["warn"]
        val   = latest.get(key)
        status, ok = bench_status(val, kind, good, warn)
        shown_val = fmt_value_for(key, val)

        # Threshold text
        if kind == "gte" and key in ("rev_nrr_m","rev_grr_m","mrr_growth_mom"):
            rule_txt = f"≥ {good*100:.0f}%"
        elif kind == "gte":
            rule_txt = f"≥ {good:.0f}"
        else:
            rule_txt = f"≤ {good:.0f}"

        # Use dedent to strip leading spaces, so Markdown doesn’t render as code
        html = textwrap.dedent(f"""
        <div class='card'>
          <div style='display:flex;justify-content:space-between;align-items:center;'>
            <div><b>{label}</b><br><span style='color:#666'>Target: {rule_txt}</span></div>
            <span class='badge {status}'>{shown_val}</span>
          </div>
        </div>
        """)
        items.append(html)

    st.markdown(f"<div class='bench-grid'>{''.join(items)}</div>", unsafe_allow_html=True)
else:
    st.info("Benchmarks unavailable: no KPI rows yet.")

# ==== Crunchbase: similar companies helper (place above UI) ====
CB_BASE = "https://api.crunchbase.com/api/v4"

def cb_search_orgs(query: str, api_key: str, limit: int = 8):
    """Return a list of similar orgs from Crunchbase (name, country, url, features...)."""
    if not (requests and api_key and query.strip()):
        return []
    try:
        # 1) Find the queried org to get its UUID
        url = f"{CB_BASE}/entities/organizations"
        params = {
            "query": query.strip(),
            "field_ids": "identifier,short_description,categories,location_identifiers,rank_org_company",
            "limit": 1,
            "user_key": api_key,
        }
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        if not data.get("entities"):
            return []

        ent = data["entities"][0]
        eid = ent["identifier"]["uuid"]

        # 2) Fetch similar companies
        sim_url = f"{CB_BASE}/entities/organizations/{eid}/similar_companies"
        params = {"user_key": api_key, "limit": limit}
        rs = requests.get(sim_url, params=params, timeout=12)
        rs.raise_for_status()
        sim = rs.json().get("similar_companies", [])

        out = []
        for s in sim:
            org = s.get("similar_organization", {})
            ident = org.get("identifier", {})
            out.append({
                "name": ident.get("value", ""),
                "country": ";".join([l.get("value", "") for l in org.get("location_identifiers", [])]) or "",
                "stage": "",
                "funding_usd": "",
                "url": ident.get("permalink", ""),
                "price": "",
                "features": ";".join([c.get("value", "") for c in org.get("categories", [])]),
                "notes": s.get("similarity_reason", "")
            })
        return out
    except Exception:
        return []

# ---------------- Competitor Landscape ----------------
st.markdown("---")
st.subheader("🧭 Competitor Landscape")

st.sidebar.markdown("### 🧩 Competitors (optional)")
comp_file = st.sidebar.file_uploader("competitors.csv", type=["csv"], accept_multiple_files=False)
comp_demo = (
    "name,country,stage,funding_usd,url,price,features,notes\n"
    "CompA,Germany,Seed,3000000,https://compa.example,€49/mo,API;Dashboard;SSO,Good EU logos\n"
    "CompB,France,Pre-Seed,0,https://compb.example,€19/mo,Dashboard;Reports,Early traction\n"
)
st.sidebar.download_button("Download competitors.csv template", comp_demo, file_name="competitors.csv")

@st.cache_data
def load_competitors(uploaded, fallback):
    try:
        if uploaded is not None:
            return pd.read_csv(uploaded)
        return pd.read_csv(io.StringIO(fallback))
    except Exception as e:
        st.error(f"Could not load competitors: {e}")
        return pd.DataFrame(columns=["name","country","stage","funding_usd","url","price","features","notes"])

competitors_df = load_competitors(comp_file, comp_demo)

# NEW: Auto-search (Crunchbase)
with st.expander("Auto-search competitors (Crunchbase)", expanded=False):
    qcol1, qcol2 = st.columns([2,1])
    with qcol1:
        q = st.text_input("Query (company name or category)", value=selected_company or "")
    with qcol2:
        hits = 0
        if st.button("Find similar companies", disabled=not bool(cb_key)):
            rows = cb_search_orgs(q, cb_key, limit=8)
            if rows:
                cb_df = pd.DataFrame(rows)
                competitors_df = pd.concat([competitors_df, cb_df], ignore_index=True)
                hits = len(rows)
            st.success(f"Added {hits} competitors from Crunchbase.") if hits else st.warning("No competitors found or API limit.")

with st.expander("Manage competitors", expanded=False):
    st.caption("Upload CSV or add rows below. Use `features` as semicolon-separated list (e.g., `API;Dashboard;SSO`).")
    with st.form("add_comp"):
        c1, c2 = st.columns(2)
        with c1:
            _name = st.text_input("Name")
            _country = st.text_input("Country/Region", value="")
            _stage = st.selectbox("Stage", ["Pre-Seed","Seed","Series A","Series B+"], index=0)
            _fund = st.number_input("Funding (USD)", min_value=0, value=0, step=100000)
        with c2:
            _url = st.text_input("Website URL", value="")
            _price = st.text_input("Entry price", value="")
            _features = st.text_input("Features (semicolon-separated)", value="")
            _notes = st.text_input("Notes", value="")
        if st.form_submit_button("Add competitor"):
            new_row = {"name": _name, "country": _country, "stage": _stage, "funding_usd": _fund,
                       "url": _url, "price": _price, "features": _features, "notes": _notes}
            competitors_df = pd.concat([competitors_df, pd.DataFrame([new_row])], ignore_index=True)
            st.success(f"Added {_name}")

if not competitors_df.empty:
    # Show table
    st.dataframe(competitors_df, use_container_width=True, hide_index=True)

    # Quick feature matrix & differentiation
    st.markdown("#### Differentiation matrix")
    # Canonical feature list from all rows
    all_feats = sorted({f.strip() for row in competitors_df['features'].fillna('').tolist() for f in row.split(';') if f.strip()})
    if all_feats:
        ticks = {}
        grid_cols = st.columns( min(4, max(2, len(all_feats)//3 + 1)) )
        for i, feat in enumerate(all_feats):
            with grid_cols[i % len(grid_cols)]:
                ticks[feat] = st.checkbox(feat, value=(feat.lower() in ['api','dashboard']))
        # Summarize
        chosen = [k for k,v in ticks.items() if v]
        if chosen:
            st.info("**Differentiation summary** — Your startup highlights: " + ", ".join(chosen))
        else:
            st.info("Select a few features you believe are differentiators.")

else:
    st.caption("No competitors yet. Upload a CSV or add one above.")

# Keep in session for export
st.session_state['competitors_df'] = competitors_df
st.session_state['diff_features'] = [f for f in (all_feats if 'all_feats' in locals() else []) if 'ticks' in locals() and ticks.get(f, False)]

# ---------------- Valuation ----------------
st.markdown("---")
st.subheader("💰 Valuation Benchmarker")
arr = float(latest['arr']) if (latest is not None and pd.notna(latest['arr'])) else 0.0
band = [*get_multiple_band(sector, stage, USER_MULTIPLES)]
adj = quality_adjustment(latest) if latest is not None else 1.0
adj_band = [b * adj for b in band]
val_low, val_mid, val_high = [arr * m for m in adj_band]
colL, colR = st.columns([2,1])
with colL:
    st.write(f"**Sector**: {sector}  |  **Stage**: {stage}  |  **Quality adj**: ×{adj:.2f}")
    st.write(f"**Multiple Band (raw)**: {band}  →  **Adjusted**: {[round(x,2) for x in adj_band]}")
    vcols = st.columns(3)
    with vcols[0]: st.metric("Implied Valuation — Low", fmt_money(val_low))
    with vcols[1]: st.metric("Implied Valuation — Mid", fmt_money(val_mid))
    with vcols[2]: st.metric("Implied Valuation — High", fmt_money(val_high))
with colR:
    if not metrics_df.empty:
        st.area_chart(metrics_df[['date','arr']].set_index('date'))

# ---------------- VC Fit: Preset + Auto‑Fill + Save to Report ----------------
if 'saved_fit' not in st.session_state:
    st.session_state['saved_fit'] = None
if 'vc_form' not in st.session_state:
    st.session_state['vc_form'] = {
        'name': 'Example VC',
        'sectors': [sector] if sector else [],
        'stages': [stage] if stage else ['Seed'],
        'geos': ['Europe', country] if country else ['Europe'],
        'min_check': 250000.0,
        'max_check': 2000000.0,
    }

def load_preset_into_form(preset_name: str):
    p = VC_PRESETS.get(preset_name)
    if not p: return
    st.session_state['vc_form'] = {
        'name': preset_name,
        'sectors': p.get('sectors', []),
        'stages': p.get('stages', []),
        'geos': p.get('geos', []),
        'min_check': float(p.get('min_check') or 0.0),
        'max_check': float(p.get('max_check') or 0.0),
    }

# Helper to sanitize defaults (must live at left margin, not inside a with:)
def _subset(default_list, options_list):
    return [x for x in (default_list or []) if x in options_list]

# ---------------- Scenario Modeling ----------------
st.markdown("---")
st.subheader("🧪 Scenario Modeling")

with st.expander("What-if analysis (growth & burn)", expanded=True):
    if latest is None or metrics_df.empty:
        st.info("Add at least one KPI row to model scenarios.")
    else:
        # --- Inputs
        default_g = float(latest.get('mrr_growth_mom') or 0.05)
        months = st.slider("Projection horizon (months)", 3, 24, 12)
        growth_mom = st.slider("Assumed MoM MRR growth", 0.00, 0.20, round(default_g, 3), step=0.005, format="%.3f")
        burn_change = st.slider("Change in monthly net burn", -0.50, 0.50, 0.00, step=0.05,
                                help="−50% means cut burn in half; +50% means burn increases by half")

        # --- Baselines from latest actuals
        mrr0 = float(latest.get('mrr') or 0.0)
        cash0 = float(latest.get('cash_balance') or 0.0)
        burn0 = float(latest.get('net_burn') or 0.0)  # monthly (positive number = burn)
        burn_new = max(0.0, burn0 * (1.0 + burn_change))  # do not allow negative burn in this simple model

        # --- Forecast MRR/ARR & cash runway
        dates = pd.date_range((latest['date'] + pd.offsets.MonthBegin(1)), periods=months, freq='MS')
        mrr_fore = [mrr0]
        for _ in range(months):
            mrr_fore.append(mrr_fore[-1] * (1.0 + growth_mom))
        mrr_fore = mrr_fore[1:]  # drop starting point
        arr_fore = [m * 12.0 for m in mrr_fore]

        cash_track = [cash0]
        for _ in range(months):
            cash_track.append(max(0.0, cash_track[-1] - burn_new))
        cash_track = cash_track[1:]
        runway_new = np.nan if burn_new <= 0 else (cash0 / burn_new)

        # --- Simple quality adj derived from assumptions (reuse logic, but feed our growth & burn)
        def adj_from_scenario(growth_m, burn_m):
            adj = 1.0
            if growth_m >= 0.07: adj *= 1.05
            elif growth_m < 0.02: adj *= 0.97
            if burn_m > 2.0: adj *= 0.92
            return adj

        # Approximate burn multiple from assumptions:
        # net new ARR per month ~ (mrr_t - mrr_{t-1}) * 12; use first step as proxy.
        nn_arr_first = (mrr0*(1+growth_mom) - mrr0) * 12.0
        burn_mult_est = np.nan if nn_arr_first <= 0 else burn_new / nn_arr_first
        qual_adj = adj_from_scenario(growth_mom, burn_mult_est if pd.notna(burn_mult_est) else 0.0)

        # --- Valuation on month N using sector/stage multiples + quality adj
        band_raw = get_multiple_band(sector, stage, USER_MULTIPLES)
        band_adj = [round(x * qual_adj, 2) for x in band_raw]
        arr_T = arr_fore[-1] if arr_fore else 0.0
        val_low_s, val_mid_s, val_high_s = [arr_T * m for m in band_adj]

        # --- KPIs snapshot
        k1, k2, k3, k4 = st.columns(4)
        with k1: st.metric("ARR in horizon", f"{currency}{arr_T:,.0f}")
        with k2: st.metric("Runway (months)", "∞" if burn_new==0 else f"{runway_new:.1f}")
        with k3: st.metric("Est. Burn Multiple", "—" if pd.isna(burn_mult_est) else f"{burn_mult_est:.2f}")
        with k4: st.metric("Quality adj ×", f"{qual_adj:.2f}")

        # --- Charts (Altair) for scenario
        import altair as alt
        scen_df = pd.DataFrame({
            'date': dates,
            'mrr': mrr_fore,
            'arr': arr_fore,
            'cash': cash_track
        })

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Projected MRR & ARR**")
            base = alt.Chart(scen_df).encode(x=alt.X('date:T', title='Date'))
            mrr_line = base.mark_line(color=COLOR_NAMES["mrr"], strokeWidth=2).encode(
                y=alt.Y('mrr:Q', title=f"MRR ({currency})"),
                tooltip=[alt.Tooltip('date:T'), alt.Tooltip('mrr:Q', title='MRR', format=',')]
            )
            arr_line = base.mark_line(color=COLOR_NAMES["arr"], strokeWidth=2, strokeDash=[4,3]).encode(
                y=alt.Y('arr:Q', title=f"ARR ({currency})", axis=alt.Axis(orient='right', titleColor=COLOR_NAMES["arr"])),
                tooltip=[alt.Tooltip('date:T'), alt.Tooltip('arr:Q', title='ARR', format=',')]
            )
            st.altair_chart(alt.layer(mrr_line, arr_line).resolve_scale(y='independent').properties(height=260),
                            use_container_width=True)
            st.caption(f"Color key: MRR = {COLOR_NAMES['mrr']}, ARR = {COLOR_NAMES['arr']}")

        with c2:
            st.markdown("**Projected Cash Balance**")
            cash_chart = alt.Chart(scen_df).mark_line(color="#8e8e8e", strokeWidth=2).encode(
                x=alt.X('date:T', title='Date'),
                y=alt.Y('cash:Q', title=f"Cash ({currency})"),
                tooltip=[alt.Tooltip('date:T'), alt.Tooltip('cash:Q', title='Cash', format=',')]
            ).properties(height=260)
            st.altair_chart(cash_chart, use_container_width=True)

        # --- Show implied valuation at horizon
        st.markdown("**Implied Valuation at Horizon**")
        cols = st.columns(3)
        with cols[0]: st.metric("Low", f"{currency}{val_low_s:,.0f}")
        with cols[1]: st.metric("Mid", f"{currency}{val_mid_s:,.0f}")
        with cols[2]: st.metric("High", f"{currency}{val_high_s:,.0f}")
        st.caption(f"Multiples used (adjusted): {band_adj} — base {band_raw}, quality adj ×{qual_adj:.2f}")

    # Save current scenario inputs so Export can use them
    st.session_state['scenario'] = {
        'months': months,
        'growth_mom': float(growth_mom),
        'burn_change': float(burn_change),
    }

st.markdown("---")
st.subheader("🎯 Target by VC — Measure Fit")

with st.expander("VC Fit Scorer", expanded=True):
    # 1) Preset picker + load
    preset = st.selectbox("VC preset (optional)", ["Custom"] + list(VC_PRESETS.keys()))
    if preset != "Custom":
        if st.button("Load preset", key="load_preset_btn"):
            load_preset_into_form(preset)  # <- must be defined earlier to update st.session_state['vc_form']
            st.success(f"Loaded preset: {preset}")
            st.rerun()  # refresh UI with new defaults

    # 2) Read the (possibly preset-loaded) form from session
    form = st.session_state['vc_form']

    # 3) Two columns for inputs
    c1, c2 = st.columns(2)
    with c1:
        vc_name = st.text_input("VC name", key="vc_name", value=form['name'])
        vc_sectors = st.multiselect(
            "Preferred sectors",
            options=SECTOR_OPTIONS,
            default=_subset(form['sectors'], SECTOR_OPTIONS),
            key="vc_sectors"
        )
        vc_stages = st.multiselect(
            "Preferred stages",
            options=STAGE_OPTIONS,
            default=_subset(form['stages'], STAGE_OPTIONS),
            key="vc_stages"
        )
    with c2:
        vc_geos_str = st.text_input(
            "Geographies (comma-separated)",
            value=", ".join(form['geos']),
            key="vc_geos_str"
        )
        min_check = st.number_input(
            "Min check (€)",
            min_value=0.0,
            value=float(form['min_check']),
            step=50_000.0,
            key="vc_min"
        )
        max_check = st.number_input(
            "Max check (€)",
            min_value=0.0,
            value=float(form['max_check']),
            step=100_000.0,
            key="vc_max"
        )

    # 4) Persist back to session_state so the scorer uses current values
    st.session_state['vc_form'] = {
        'name': st.session_state.get('vc_name', vc_name),
        'sectors': st.session_state.get('vc_sectors', vc_sectors),
        'stages': st.session_state.get('vc_stages', vc_stages),
        'geos': [g.strip() for g in st.session_state.get('vc_geos_str', vc_geos_str).split(',') if g.strip()],
        'min_check': st.session_state.get('vc_min', min_check),
        'max_check': st.session_state.get('vc_max', max_check),
    }

    if latest is None:
        st.warning("No KPI rows for this company; fit will be limited.")
    else:
        profile = VCProfile(
            name=st.session_state['vc_form']['name'],
            sectors=st.session_state['vc_form']['sectors'],
            stages=st.session_state['vc_form']['stages'],
            geos=st.session_state['vc_form']['geos'],
            min_check=float(st.session_state['vc_form']['min_check']),
            max_check=float(st.session_state['vc_form']['max_check']),
        )
        fit = compute_fit({"sector":sector,"stage":stage,"country":country}, latest, profile)
        st.markdown(f"### **Overall Fit: {fit['overall']}/100**")
        st.progress(fit['overall']/100)
        b1,b2,b3,b4,b5 = st.columns(5)
        b1.metric("Sector", fit['breakdown']['Sector'])
        b2.metric("Stage", fit['breakdown']['Stage'])
        b3.metric("Traction", fit['breakdown']['Traction'])
        b4.metric("Geography", fit['breakdown']['Geography'])
        b5.metric("Check size", fit['breakdown']['Check size'])
        st.markdown("**Why:**")
        for r in fit['reasons']:
            st.write("- ", r)

                # Radar next to metrics
        rcol1, rcol2 = st.columns([1,1])
        with rcol1:
            st.markdown("**Fit radar**")
            radar_b64 = fit_radar_b64(fit['breakdown'], title=f"{st.session_state['vc_form']['name']} fit")
            st.image(f"data:image/png;base64,{radar_b64}")

        with rcol2:
            st.markdown("**Why:**")
            for r in fit['reasons']:
                st.write("- ", r)

        if st.button("💾 Save Fit to report"):
            st.session_state['saved_fit'] = {
                'timestamp': pd.Timestamp.utcnow().isoformat(),
                'company': selected_company,
                'vc_profile': st.session_state['vc_form'],
                'fit': fit,
                'fit_radar_b64': radar_b64,   # <— add this
            }
            st.success(f"Saved VC fit for {st.session_state['vc_form']['name']} to include in the export.")

# ---------------- Export HTML ----------------
st.markdown("---")
st.subheader("⬇️ Exports")

if st.button("Generate Report Page (HTML)", key="btn_export_html") and latest is not None:
    # charts → base64 PNG helpers with readable axes & legends
    def _fig_to_b64():
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=160)
        plt.close()
        return base64.b64encode(buf.getvalue()).decode()

    def _date_axis(ax):
        loc = AutoDateLocator(minticks=4, maxticks=8)
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(ConciseDateFormatter(loc))
        for label in ax.get_xticklabels():
            label.set_rotation(0)
            label.set_ha("center")

    def _currency_formatter(sym):
        return FuncFormatter(lambda v, pos: f"{sym}{v:,.0f}")

    # ---- Chart 1: MRR & ARR (same axis, currency formatting, legend) ----
    def chart_growth_as_b64(df: pd.DataFrame, currency_sym: str, company: str):
        fig = plt.figure(figsize=(8.5, 4.0))
        ax = plt.gca()

        ax.plot(df["date"], df["mrr"], label="MRR", color=COLOR_NAMES["mrr"], linewidth=2)
        ax.plot(df["date"], df["arr"], label="ARR", color=COLOR_NAMES["arr"], linewidth=2, linestyle="--")

        ax.set_title(f"{company} — MRR & ARR")
        ax.set_ylabel(f"Amount ({currency_sym})")
        ax.yaxis.set_major_formatter(_currency_formatter(currency_sym))
        ax.grid(True, linewidth=0.4, alpha=0.4)
        ax.legend(loc="upper left", frameon=False)
        _date_axis(ax)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6, prune=None))

        return _fig_to_b64()

    # ---- Chart 2: NRR/GRR (%) + Burn multiple (twin axis), legend & percent formatting ----
    def chart_retention_burn_as_b64(df: pd.DataFrame, company: str):
        fig = plt.figure(figsize=(8.5, 4.6))
        axL = plt.gca()
        axR = axL.twinx()  # right axis for burn multiple

        # Left axis: percentages (NRR/GRR are ratios ~1.04, 0.95 etc.)
        axL.plot(df["date"], df["rev_nrr_m"], label="NRR (monthly)", color=COLOR_NAMES["rev_nrr_m"], linewidth=2)
        axL.plot(df["date"], df["rev_grr_m"], label="GRR (monthly)", color=COLOR_NAMES["rev_grr_m"], linewidth=2, linestyle="--")
        axL.set_ylabel("Retention (monthly)")
        axL.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
        axL.grid(True, linewidth=0.4, alpha=0.4)

        # Right axis: burn multiple (unitless)
        axR.plot(df["date"], df["burn_multiple"], label="Burn multiple", color=COLOR_NAMES["burn_multiple"], linewidth=2)
        axR.set_ylabel("Burn multiple")
        axR.yaxis.set_major_locator(MaxNLocator(nbins=6))

        # Title + x-axis format + legends
        axL.set_title(f"{company} — Retention & Burn")
        _date_axis(axL)

        # Combined legend
        h1, l1 = axL.get_legend_handles_labels()
        h2, l2 = axR.get_legend_handles_labels()
        axL.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False)

        return _fig_to_b64()

    # Build images with improved readability
    img1 = chart_growth_as_b64(metrics_df, currency, selected_company)
    img2 = chart_retention_burn_as_b64(metrics_df, selected_company)

    # ===== Scenario section for Export (uses saved sliders or sensible defaults) =====
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
    from matplotlib.ticker import FuncFormatter, PercentFormatter, MaxNLocator

    # Read scenario inputs (or default: 12 months, +5% MoM, no burn change)
    sc = st.session_state.get('scenario', {'months': 12, 'growth_mom': 0.05, 'burn_change': 0.0})
    months_exp = int(sc.get('months', 12))
    growth_mom_exp = float(sc.get('growth_mom', 0.05))
    burn_change_exp = float(sc.get('burn_change', 0.0))

    # Baselines
    mrr0 = float(latest.get('mrr') or 0.0)
    cash0 = float(latest.get('cash_balance') or 0.0)
    burn0 = float(latest.get('net_burn') or 0.0)
    burn_new = max(0.0, burn0 * (1.0 + burn_change_exp))

    # Projections
    dates_s = pd.date_range((latest['date'] + pd.offsets.MonthBegin(1)), periods=months_exp, freq='MS')
    mrr_fore_s = [mrr0]
    for _ in range(months_exp):
        mrr_fore_s.append(mrr_fore_s[-1] * (1.0 + growth_mom_exp))
    mrr_fore_s = mrr_fore_s[1:]
    arr_fore_s = [m * 12.0 for m in mrr_fore_s]

    cash_track_s = [cash0]
    for _ in range(months_exp):
        cash_track_s.append(max(0.0, cash_track_s[-1] - burn_new))
    cash_track_s = cash_track_s[1:]
    runway_new_s = (cash0 / burn_new) if burn_new > 0 else float('inf')

    # Quality adj (same spirit as the app logic)
    def _adj_from_scenario(growth_m, burn_mult):
        adj = 1.0
        if growth_m >= 0.07: adj *= 1.05
        elif growth_m < 0.02: adj *= 0.97
        if burn_mult > 2.0: adj *= 0.92
        return adj

    nn_arr_first_s = (mrr0*(1+growth_mom_exp) - mrr0) * 12.0
    burn_mult_est_s = np.nan if nn_arr_first_s <= 0 else burn_new / nn_arr_first_s
    qual_adj_s = _adj_from_scenario(growth_mom_exp, burn_mult_est_s if pd.notna(burn_mult_est_s) else 0.0)

    # Valuation at horizon (sector/stage multiples + adj)
    band_raw_s = get_multiple_band(sector, stage, USER_MULTIPLES)
    band_adj_s = [round(x * qual_adj_s, 2) for x in band_raw_s]
    arr_T_s = arr_fore_s[-1] if arr_fore_s else 0.0
    val_low_s, val_mid_s, val_high_s = [arr_T_s * m for m in band_adj_s]

    # ---- Matplotlib helpers reused for scenario charts ----
    def _fig_to_b64():
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=160)
        plt.close()
        return base64.b64encode(buf.getvalue()).decode()

    def _date_axis(ax):
        loc = AutoDateLocator(minticks=4, maxticks=8)
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(ConciseDateFormatter(loc))

    def _currency_formatter(sym):
        return FuncFormatter(lambda v, pos: f"{sym}{v:,.0f}")

    # Chart S1: Projected MRR & ARR
    def chart_scen_growth_as_b64(dates, mrrs, arrs, currency_sym, company):
        fig = plt.figure(figsize=(8.5, 4.0))
        ax = plt.gca()
        ax.plot(dates, mrrs, label="MRR", color=COLOR_NAMES["mrr"], linewidth=2)
        ax.plot(dates, arrs, label="ARR", color=COLOR_NAMES["arr"], linewidth=2, linestyle="--")
        ax.set_title(f"{company} — Scenario: MRR & ARR")
        ax.set_ylabel(f"Amount ({currency_sym})")
        ax.yaxis.set_major_formatter(_currency_formatter(currency_sym))
        ax.grid(True, linewidth=0.4, alpha=0.4)
        ax.legend(loc="upper left", frameon=False)
        _date_axis(ax)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        return _fig_to_b64()

    # Chart S2: Projected Cash
    def chart_scen_cash_as_b64(dates, cash, currency_sym, company):
        fig = plt.figure(figsize=(8.5, 4.0))
        ax = plt.gca()
        ax.plot(dates, cash, label="Cash", color="#8e8e8e", linewidth=2)
        ax.set_title(f"{company} — Scenario: Cash Balance")
        ax.set_ylabel(f"Cash ({currency_sym})")
        ax.yaxis.set_major_formatter(_currency_formatter(currency_sym))
        ax.grid(True, linewidth=0.4, alpha=0.4)
        ax.legend(loc="upper right", frameon=False)
        _date_axis(ax)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        return _fig_to_b64()

    imgS1 = chart_scen_growth_as_b64(dates_s, mrr_fore_s, arr_fore_s, currency, selected_company)
    imgS2 = chart_scen_cash_as_b64(dates_s, cash_track_s, currency, selected_company)

    # Human-friendly runway text
    runway_txt = "∞" if np.isinf(runway_new_s) else f"{runway_new_s:.1f} months"
    burn_mult_txt = "—" if pd.isna(burn_mult_est_s) else f"{burn_mult_est_s:.2f}"

    # Small legend for color names (not hex codes)
    charts_legend_s = f"""
      <div class='small' style='margin-top:8px'>
        <b>Color key:</b>
        MRR = {COLOR_NAMES['mrr']}, ARR = {COLOR_NAMES['arr']}, Cash = grey
      </div>
    """

    scenario_html = f"""
      <div class='card'>
        <h2>Scenario Modeling</h2>
        <p>
          Horizon: <b>{months_exp} months</b> •
          MoM growth: <b>{growth_mom_exp*100:.1f}%</b> •
          Burn change: <b>{'+' if burn_change_exp>=0 else ''}{burn_change_exp*100:.0f}%</b><br>
          New burn: <b>{currency}{burn_new:,.0f}/mo</b> •
          New runway: <b>{runway_txt}</b> •
          Est. burn multiple: <b>{burn_mult_txt}</b>
        </p>
        <div class='grid'>
          <img src='data:image/png;base64,{imgS1}' style='max-width:100%;border:1px solid #eee;border-radius:8px;'>
          <img src='data:image/png;base64,{imgS2}' style='max-width:100%;border:1px solid #eee;border-radius:8px;'>
        </div>
        <div style='margin-top:10px'>
          <b>Implied Valuation at Horizon</b><br>
          Multiples used (adjusted): {band_adj_s} — base {band_raw_s}, quality adj ×{qual_adj_s:.2f}<br>
          Low: <b>{currency}{val_low_s:,.0f}</b> •
          Mid: <b>{currency}{val_mid_s:,.0f}</b> •
          High: <b>{currency}{val_high_s:,.0f}</b>
        </div>
        {charts_legend_s}
      </div>
    """

    # Benchmarks
    bench_cards = []
    for key, spec in BENCHMARKS.items():
        label = spec["label"]; kind = spec["kind"]; good = spec["good"]; warn = spec["warn"]
        val = latest.get(key)
        status, _ok = bench_status(val, kind, good, warn)
        shown_val = fmt_value_for(key, val)

        rule_txt = f"≥ {good*100:.0f}%" if (kind=='gte' and key in ('rev_nrr_m','rev_grr_m','mrr_growth_mom')) else (f"≥ {good:.0f}" if kind=='gte' else f"≤ {good:.0f}")
        bench_cards.append(
            f"<div class='card'><div style='display:flex;justify-content:space-between;align-items:center;'>"
            f"<div><b>{label}</b><br><span style='color:#666'>Target: {rule_txt}</span></div>"
            f"<span class='badge {status}'>{shown_val}</span></div></div>"
        )
    bench_html = f"<div class='card'><h2>Benchmarks vs. SaaS norms</h2><div class='bench-grid'>{''.join(bench_cards)}</div><div class='small'>Green = meets target. Amber = borderline. Red = below target.</div></div>"

    # Fit radar image for export (use saved image if available, else recompute quickly)
    fit_radar_b64_export = None
    sf = st.session_state.get('saved_fit')
    if sf and sf.get('company') == selected_company and sf.get('fit_radar_b64'):
        fit_radar_b64_export = sf['fit_radar_b64']
    elif 'fit' in locals() and fit:
        fit_radar_b64_export = fit_radar_b64(fit['breakdown'], title=f"{st.session_state['vc_form']['name']} fit")

    radar_html = ""
    if fit_radar_b64_export:
        radar_html = f"""
        <div class='card'>
          <h2>VC Fit — Radar</h2>
          <img src='data:image/png;base64,{fit_radar_b64_export}' style='max-width:420px;border:1px solid #eee;border-radius:8px;'>
        </div>
        """

    # Competitors table → HTML
    comp_df = st.session_state.get('competitors_df', pd.DataFrame())
    comp_html = ""
    if comp_df is not None and not comp_df.empty:
        show_cols = ["name","country","stage","funding_usd","price","url","features","notes"]
        show_cols = [c for c in show_cols if c in comp_df.columns]
        comp_tbl = comp_df[show_cols].copy()
        comp_tbl.rename(columns={"funding_usd":"funding (USD)"}, inplace=True)
        comp_html = "<div class='card'><h2>Competitor Landscape</h2>" + comp_tbl.to_html(index=False, escape=True) + "</div>"
        diff_feats = st.session_state.get('diff_features', [])
        if diff_feats:
            comp_html += f"<div class='card'><b>Differentiation highlights:</b> {', '.join(diff_feats)}</div>"
    else:
        comp_html = "<div class='card'><h2>Competitor Landscape</h2><p>No competitors provided.</p></div>"

    html = f"""
    <!doctype html><html lang='en'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
      <title>{selected_company} — Snapshot</title>
      <style>
        body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px}}
        .card{{border:1px solid #eee;border-radius:12px;padding:16px;margin-bottom:16px}}
        .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
        .small{{color:#777}}
        .badge{{display:inline-block;padding:6px 10px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid transparent}}
        .badge.green{{background:#E8F5E9;color:#1B5E20;border-color:#A5D6A7}}
        .badge.amber{{background:#FFF8E1;color:#7C4A03;border-color:#FFE082}}
        .badge.red{{background:#FFEBEE;color:#B71C1C;border-color:#EF9A9A}}
        .bench-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}}
      </style>
    </head><body>
      <h1>📈 {selected_company} — Health, Valuation & Fit</h1>
      <p class='small'>Sector: {sector} • Stage: {stage} • Country: {country}</p>

      <div class='card'>
        <h2>Key KPIs (latest)</h2>
        <div class='grid'>
          <div><b>ARR</b><br>{fmt_money(float(latest['arr']) if pd.notna(latest['arr']) else 0)}</div>
          <div><b>MoM MRR Growth</b><br>{fmt_pct(float(latest['mrr_growth_mom']))}</div>
          <div><b>NRR (monthly)</b><br>{fmt_pct(float(latest['rev_nrr_m']))}</div>
          <div><b>Burn Multiple</b><br>{'—' if pd.isna(latest['burn_multiple']) else f"{float(latest['burn_multiple']):.2f}"}</div>
          <div><b>Runway (months)</b><br>{'—' if pd.isna(latest['runway_m']) else f"{float(latest['runway_m']):.1f}"}</div>
          <div><b>CAC Payback (months)</b><br>{'—' if pd.isna(latest['cac_payback_m']) else f"{float(latest['cac_payback_m']):.1f}"}</div>
        </div>
      </div>

      <div class='card'><h2>Valuation Benchmarks</h2>
        <p>Multiples (adjusted): {adj_band}</p>
        <div class='grid'>
          <div><b>Low</b><br>{fmt_money(val_low)}</div>
          <div><b>Mid</b><br>{fmt_money(val_mid)}</div>
          <div><b>High</b><br>{fmt_money(val_high)}</div>
        </div>
      </div>

      <div class='card'><h2>Charts</h2>
        <img src='data:image/png;base64,{img1}' style='max-width:100%;border:1px solid #eee;border-radius:8px;margin-bottom:12px;'>
        <img src='data:image/png;base64,{img2}' style='max-width:100%;border:1px solid #eee;border-radius:8px;'>
      </div>

      {scenario_html}
      {comp_html}
      {bench_html}
      {radar_html}

      <div class='card'>
        <h2>About the Author</h2>
        <div style='display:flex;gap:16px;align-items:center;'>
          <div style='width:56px;height:56px;border-radius:50%;overflow:hidden;background:#f4f4f4;display:flex;align-items:center;justify-content:center;'></div>
          <div>
            <div><b>{AUTHOR_NAME}</b></div>
            <div><a href='mailto:{AUTHOR_EMAIL}'>{AUTHOR_EMAIL}</a></div>
            <div><a href='{AUTHOR_LINKEDIN}' target='_blank' rel='noopener noreferrer'>LinkedIn</a></div>
          </div>
        </div>
      </div>

      <p class='small'>Generated by VC Snapshot. Data stays client-side.</p>
    </body></html>
    """
    st.download_button("Download report.html", html.encode('utf-8'),
                       file_name=f"{selected_company}_snapshot.html", mime='text/html', key="btn_download_report_html")
    st.session_state['last_report_html'] = html

st.caption("Use the preset picker in the VC Fit section to auto-fill the form, then edit as needed and save to include in the export.")

# Optional: PDF via WeasyPrint if available; otherwise suggest browser print
pdf_ready = False
try:
    import weasyprint  # type: ignore
    pdf_ready = True
except Exception:
    pdf_ready = False

if pdf_ready:
    st.caption("PDF export uses the last HTML you generated above.")
    if st.button("Generate report.pdf (experimental)", key="btn_export_pdf"):
        html_src = st.session_state.get('last_report_html')
        if not html_src:
            st.warning("Please click “Generate Report Page (HTML)” first so I can convert it to PDF.")
        else:
            from weasyprint import HTML
            pdf_bytes = HTML(string=html_src).write_pdf()
            st.download_button(
                "Download report.pdf",
                data=pdf_bytes,
                file_name=f"{selected_company}_snapshot.pdf",
                mime="application/pdf",
                key="btn_download_report_pdf"
            )
else:
    st.caption("Tip: open the downloaded HTML and use your browser’s **Print → Save as PDF** for a perfect PDF.")

# ---------------- Export: One-click Investment Memo ----------------
st.markdown("---")
st.subheader("📝 Generate Investment Memo")

analyst_notes = st.text_area(
    "Analyst notes / risks (optional)",
    height=120,
    placeholder="Key insights, risks, diligence items…",
    key="txt_analyst_notes"
)

if st.button("Generate Investment Memo (HTML)", key="btn_investment_memo"):
    # Reuse chart builders defined above in this block
    img1 = chart_growth_as_b64(metrics_df, currency, selected_company)
    img2 = chart_retention_burn_as_b64(metrics_df, selected_company)

    # Scenario already computed above; if not, use defaults
    sc = st.session_state.get('scenario', {'months': 12, 'growth_mom': 0.05, 'burn_change': 0.0})
    months_exp = int(sc.get('months', 12))
    growth_mom_exp = float(sc.get('growth_mom', 0.05))
    burn_change_exp = float(sc.get('burn_change', 0.0))

    # Benchmarks (reuse)
    bench_cards = []
    for key, spec in BENCHMARKS.items():
        label = spec["label"]; kind = spec["kind"]; good = spec["good"]; warn = spec["warn"]
        val = latest.get(key); status, _ok = bench_status(val, kind, good, warn)
        shown_val = fmt_value_for(key, val)
        rule_txt = f"≥ {good*100:.0f}%" if (kind=='gte' and key in ('rev_nrr_m','rev_grr_m','mrr_growth_mom')) else (f"≥ {good:.0f}" if kind=='gte' else f"≤ {good:.0f}")
        bench_cards.append(
            f"<div class='card'><div style='display:flex;justify-content:space-between;align-items:center;'>"
            f"<div><b>{label}</b><br><span style='color:#666'>Target: {rule_txt}</span></div>"
            f"<span class='badge {status}'>{shown_val}</span></div></div>"
        )
    bench_html = f"<div class='card'><h2>Benchmarks vs. SaaS norms</h2><div class='bench-grid'>{''.join(bench_cards)}</div><div class='small'>Green = meets target. Amber = borderline. Red = below target.</div></div>"

    # Fit (reuse saved)
    fit_html = ""
    radar_html = ""
    sf = st.session_state.get('saved_fit')
    if sf and sf.get('company') == selected_company:
        prof = sf['vc_profile']; f = sf['fit']
        bd = f.get('breakdown', {})
        bd_list = "".join([f"<li>{k}: {v}</li>" for k,v in bd.items()])
        reasons_list = "".join([f"<li>{re}</li>" for re in f.get('reasons', [])])
        fit_html = f"""
        <div class='card'>
          <h2>VC Fit — {prof.get('name','')}</h2>
          <p><b>Overall Fit:</b> {f.get('overall','—')}/100</p>
          <p><b>Profile:</b> Sectors: {', '.join(prof.get('sectors',[]))} • Stages: {', '.join(prof.get('stages',[]))} • Geos: {', '.join(prof.get('geos',[]))} • Check: {prof.get('min_check','—')}–{prof.get('max_check','—')} €</p>
          <div class='grid'>
            <div><b>Breakdown</b><ul>{bd_list}</ul></div>
            <div><b>Why</b><ul>{reasons_list}</ul></div>
          </div>
        </div>
        """
        radar_b64 = sf.get('fit_radar_b64')
        if radar_b64:
            radar_html = f"""
            <div class='card'>
              <h2>VC Fit — Radar</h2>
              <img src='data:image/png;base64,{radar_b64}' style='max-width:420px;border:1px solid #eee;border-radius:8px;'>
            </div>
            """

    # Competitors
    comp_df = st.session_state.get('competitors_df', pd.DataFrame())
    comp_html = "<div class='card'><h2>Competitor Landscape</h2><p>No competitors provided.</p></div>"
    if comp_df is not None and not comp_df.empty:
        show_cols = ["name","country","stage","funding_usd","price","url","features","notes"]
        show_cols = [c for c in show_cols if c in comp_df.columns]
        comp_tbl = comp_df[show_cols].copy()
        comp_tbl.rename(columns={"funding_usd":"funding (USD)"}, inplace=True)
        comp_html = "<div class='card'><h2>Competitor Landscape</h2>" + comp_tbl.to_html(index=False, escape=True) + "</div>"
        diff_feats = st.session_state.get('diff_features', [])
        if diff_feats:
            comp_html += f"<div class='card'><b>Differentiation highlights:</b> {', '.join(diff_feats)}</div>"

    memo_html = f"""
    <!doctype html><html lang='en'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
      <title>{selected_company} — Investment Memo</title>
      <style>
        body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px}}
        .card{{border:1px solid #eee;border-radius:12px;padding:16px;margin-bottom:16px}}
        .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
        .small{{color:#777}}
        .badge{{display:inline-block;padding:6px 10px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid transparent}}
        .badge.green{{background:#E8F5E9;color:#1B5E20;border-color:#A5D6A7}}
        .badge.amber{{background:#FFF8E1;color:#7C4A03;border-color:#FFE082}}
        .badge.red{{background:#FFEBEE;color:#B71C1C;border-color:#EF9A9A}}
        .bench-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}}
      </style>
    </head><body>
      <h1>📈 {selected_company} — Investment Memo</h1>
      <p class='small'>Sector: {sector} • Stage: {stage} • Country: {country}</p>

      <div class='card'>
        <h2>Key KPIs (latest)</h2>
        <div class='grid'>
          <div><b>ARR</b><br>{fmt_money(float(latest['arr']) if pd.notna(latest['arr']) else 0)}</div>
          <div><b>MoM MRR Growth</b><br>{fmt_pct(float(latest['mrr_growth_mom']))}</div>
          <div><b>NRR (monthly)</b><br>{fmt_pct(float(latest['rev_nrr_m']))}</div>
          <div><b>Burn Multiple</b><br>{'—' if pd.isna(latest['burn_multiple']) else f"{float(latest['burn_multiple']):.2f}"}</div>
          <div><b>Runway (months)</b><br>{'—' if pd.isna(latest['runway_m']) else f"{float(latest['runway_m']):.1f}"}</div>
          <div><b>CAC Payback (months)</b><br>{'—' if pd.isna(latest['cac_payback_m']) else f"{float(latest['cac_payback_m']):.1f}"}</div>
        </div>
      </div>

      <div class='card'><h2>Charts</h2>
        <img src='data:image/png;base64,{img1}' style='max-width:100%;border:1px solid #eee;border-radius:8px;margin-bottom:12px;'>
        <img src='data:image/png;base64,{img2}' style='max-width:100%;border:1px solid #eee;border-radius:8px;'>
      </div>

      {bench_html}
      {comp_html}
      {radar_html}
      {fit_html}

      {("<div class='card'><h2>Analyst Notes / Risks</h2><p>" + st.session_state.get('txt_analyst_notes',"").replace("\n","<br>") + "</p></div>") if st.session_state.get('txt_analyst_notes',"").strip() else ""}

      <div class='card'>
        <h2>About the Author</h2>
        <div style='display:flex;gap:16px;align-items:center;'>
          <div style='width:56px;height:56px;border-radius:50%;overflow:hidden;background:#f4f4f4;display:flex;align-items:center;justify-content:center;'></div>
          <div>
            <div><b>{AUTHOR_NAME}</b></div>
            <div><a href='mailto:{AUTHOR_EMAIL}'>{AUTHOR_EMAIL}</a></div>
            <div><a href='{AUTHOR_LINKEDIN}' target='_blank' rel='noopener noreferrer'>LinkedIn</a></div>
          </div>
        </div>
      </div>

      <p class='small'>Generated by VC Snapshot. Data stays client-side.</p>
    </body></html>
    """
    st.download_button(
        "Download Investment Memo (HTML)",
        memo_html.encode('utf-8'),
        file_name=f"{selected_company}_investment_memo.html",
        mime='text/html',
        key="btn_download_investment_memo_html"
    )


