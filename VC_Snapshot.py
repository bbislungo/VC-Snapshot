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

import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

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

# ---------------- Branding / Header ----------------
AUTHOR_NAME = "Emanuele Borsellino"
AUTHOR_EMAIL = "emabors@gmail.com"
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/emanuele-borsellino-712288348/"
AUTHOR_LOGO_URL = ""  # optional image URL

st.set_page_config(page_title="VC Snapshot — Analyze Startups & Fit", layout="wide")
st.title("📊 VC Snapshot")
st.caption("Analyze startup KPIs, benchmark valuation, and measure VC fit. Built by Emanuele Borsellino.")
st.markdown(f"**About:** Hi, I’m {AUTHOR_NAME}, aspiring VC analyst. This tool evaluates startup health and how well they fit a VC thesis.")

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

# ---------------- Multiples (editable JSON) ----------------
DEFAULT_MULTIPLES: Dict[str, Dict[str, List[float]]] = {
    "Developer Tools": {"Pre-Seed":[8,12,18],"Seed":[6,10,15],"Series A":[5,8,12],"Series B+":[4,6,9]},
    "Fintech":         {"Pre-Seed":[10,15,22],"Seed":[8,12,18],"Series A":[6,10,14],"Series B+":[5,8,12]},
    "Consumer Subscriptions": {"Pre-Seed":[5,8,12],"Seed":[4,6,9],"Series A":[3,5,8],"Series B+":[2.5,4,6]},
}

st.sidebar.markdown("### 💲 Multiples (editable)")
mult_text = st.sidebar.text_area("JSON (sector → stage → [low, mid, high])", value=pd.Series(DEFAULT_MULTIPLES).to_json(), height=150)
try:
    USER_MULTIPLES = pd.read_json(io.StringIO(mult_text)).to_dict()
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
meta = companies[companies['company'] == selected_company].iloc[0].to_dict() if not companies.empty else {}
sector = meta.get('sector', 'Generic')
stage = meta.get('stage', 'Seed')
country = meta.get('country', '')
latest = metrics_df.iloc[-1] if len(metrics_df) else None

fmt_pct = lambda x: "—" if pd.isna(x) else f"{x*100:.1f}%"
fmt_money = lambda x: "—" if pd.isna(x) else f"{currency}{x:,.0f}"

st.markdown("---")
st.subheader("📊 Health KPIs (latest)")
cols = st.columns(6)
if latest is not None:
    with cols[0]: st.metric("ARR", fmt_money(latest['arr']))
    with cols[1]: st.metric("MoM MRR Growth", fmt_pct(latest['mrr_growth_mom']))
    with cols[2]: st.metric("NRR (monthly)", fmt_pct(latest['rev_nrr_m']))
    with cols[3]: st.metric("Burn Multiple", "—" if pd.isna(latest['burn_multiple']) else f"{latest['burn_multiple']:.2f}")
    with cols[4]: st.metric("Runway (months)", "—" if pd.isna(latest['runway_m']) else f"{latest['runway_m']:.1f}")
    with cols[5]: st.metric("CAC Payback (months)", "—" if pd.isna(latest['cac_payback_m']) else f"{latest['cac_payback_m']:.1f}")
else:
    st.info("No KPI rows for this company yet.")

st.markdown("---")
left, right = st.columns(2)
with left:
    st.markdown("#### MRR & ARR over time")
    if not metrics_df.empty:
        st.line_chart(metrics_df[['date','mrr','arr']].set_index('date'))
    else:
        st.write("No data")
with right:
    st.markdown("#### Retention & Burn metrics")
    if not metrics_df.empty:
        st.line_chart(metrics_df[['date','rev_nrr_m','rev_grr_m','burn_multiple']].set_index('date'))
    else:
        st.write("No data")

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

st.markdown("---")
st.subheader("🎯 Target by VC — Measure Fit")
with st.expander("VC Fit Scorer", expanded=True):
    preset = st.selectbox("VC preset (optional)", ["Custom"] + list(VC_PRESETS.keys()))
    if preset != "Custom":
        if st.button("Load preset", key="load_preset_btn"):
            load_preset_into_form(preset)
            st.success(f"Loaded preset: {preset}")
    form = st.session_state['vc_form']
    c1, c2 = st.columns(2)
    with c1:
        vc_name = st.text_input("VC name", key="vc_name", value=form['name'])
        vc_sectors = st.multiselect("Preferred sectors", options=["Fintech","Developer Tools","Consumer Subscriptions","AI","Infra","SaaS","Marketplaces","Industrial Tech","Consumer"], default=form['sectors'], key="vc_sectors")
        vc_stages = st.multiselect("Preferred stages", options=["Pre-Seed","Seed","Series A","Series B+"], default=form['stages'], key="vc_stages")
    with c2:
        vc_geos_str = st.text_input("Geographies (comma-separated)", value=", ".join(form['geos']), key="vc_geos_str")
        min_check = st.number_input("Min check (€)", min_value=0.0, value=float(form['min_check']), step=50_000.0, key="vc_min")
        max_check = st.number_input("Max check (€)", min_value=0.0, value=float(form['max_check']), step=100_000.0, key="vc_max")

    # Persist any changes back into session_state
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

        if st.button("💾 Save Fit to report"):
            st.session_state['saved_fit'] = {
                'timestamp': pd.Timestamp.utcnow().isoformat(),
                'company': selected_company,
                'vc_profile': st.session_state['vc_form'],
                'fit': fit,
            }
            st.success(f"Saved VC fit for {st.session_state['vc_form']['name']} to include in the export.")

# ---------------- Export HTML ----------------
st.markdown("---")
st.subheader("⬇️ Exports")

if st.button("Generate Report Page (HTML)") and latest is not None:
    # charts → base64 images
    def chart_as_b64(x_series, y_series_list, title, y_label):
        buf = io.BytesIO()
        plt.figure()
        for y in y_series_list:
            plt.plot(x_series, y)
        plt.title(title); plt.xlabel('Date'); plt.ylabel(y_label)
        plt.tight_layout(); plt.savefig(buf, format='png'); plt.close()
        return base64.b64encode(buf.getvalue()).decode()

    img1 = chart_as_b64(metrics_df['date'], [metrics_df['mrr'], metrics_df['arr']], f"{selected_company} — MRR & ARR", 'Amount')
    img2 = chart_as_b64(metrics_df['date'], [metrics_df['rev_nrr_m'], metrics_df['rev_grr_m'], metrics_df['burn_multiple']], f"{selected_company} — Retention & Burn", 'Value')

    # Optional VC Fit section if saved for this company
    fit_html = ""
    if st.session_state.get('saved_fit'):
        sf = st.session_state['saved_fit']
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
                <div>
                  <b>Breakdown</b>
                  <ul>{bd_list}</ul>
                </div>
                <div>
                  <b>Why</b>
                  <ul>{reasons_list}</ul>
                </div>
              </div>
            </div>
            """

    html = f"""
    <!doctype html><html lang='en'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
      <title>{selected_company} — Snapshot</title>
      <style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px}}.card{{border:1px solid #eee;border-radius:12px;padding:16px;margin-bottom:16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}h1{{margin-top:0}}.small{{color:#777}}</style>
    </head><body>
      <h1>📈 {selected_company} — Health, Valuation & Fit</h1>
      <p class='small'>Sector: {sector} • Stage: {stage} • Country: {country}</p>

      <div class='card'>
        <h2>Key KPIs (latest)</h2>
        <div class='grid'>
          <div><b>ARR</b><br>{fmt_money(arr)}</div>
          <div><b>MoM MRR Growth</b><br>{fmt_pct(latest['mrr_growth_mom'])}</div>
          <div><b>NRR (monthly)</b><br>{fmt_pct(latest['rev_nrr_m'])}</div>
          <div><b>Burn Multiple</b><br>{'—' if pd.isna(latest['burn_multiple']) else f"{latest['burn_multiple']:.2f}"}</div>
          <div><b>Runway (months)</b><br>{'—' if pd.isna(latest['runway_m']) else f"{latest['runway_m']:.1f}"}</div>
          <div><b>CAC Payback (months)</b><br>{'—' if pd.isna(latest['cac_payback_m']) else f"{latest['cac_payback_m']:.1f}"}</div>
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

      {fit_html}

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

      <p class='small'>Generated by VC Snapshot. Data stays client‑side.</p>
    </body></html>
    """
    st.download_button("Download report.html", html.encode('utf-8'), file_name=f"{selected_company}_snapshot.html", mime='text/html')

st.caption("Use the preset picker in the VC Fit section to auto‑fill the form, then edit as needed and save to include in the export.")
