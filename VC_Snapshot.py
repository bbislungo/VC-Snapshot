# VC Snapshot — Streamlit MVP v7 (Main + Targets‑by‑VC Fit + Save Fit to Report)
# Layout:
# - Sidebar: uploads (companies/kpis), extractor (URL/docs), multiples editor
# - Main: KPI cards, charts, valuation
# - Fit Scorer: button opens panel to define ANY VC profile → 0–100 fit + reasons
# - NEW: "Save Fit to report" stores the current VC profile + score and includes it in exported HTML

import io, base64, json, re
from typing import Dict, List, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd
import streamlit as st

# Optional deps
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

import matplotlib.pyplot as plt

# ---------------- Branding ----------------
AUTHOR_NAME = "Emanuele Borsellino"
AUTHOR_EMAIL = "emabors@gmail.com"
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/emanuele-borsellino-712288348/"
AUTHOR_LOGO_URL = ""  # optional image URL

# ---------------- Demo data (fallbacks) ----------------

def demo_companies_csv() -> str:
    return """company,sector,stage,country
Nimbus,Developer Tools,Seed,Italy
Aurora,Fintech,Series A,Spain
Hearth,Consumer Subscriptions,Pre-Seed,Italy
"""

def demo_kpis_csv() -> str:
    return """date,company,mrr,new_mrr,expansion_mrr,churned_mrr,cash_balance,net_burn,customers,new_customers,churned_customers,gross_margin_pct,cac
2025-04-01,Nimbus,40000,8000,2000,3000,900000,70000,320,30,12,0.78,3500
2025-05-01,Nimbus,47000,9000,3000,2000,830000,72000,345,35,10,0.78,3400
2025-06-01,Nimbus,52000,8000,5000,3000,765000,73000,360,28,13,0.78,3300
2025-07-01,Nimbus,58000,9000,6000,4000,700000,74000,378,30,12,0.78,3200
2025-04-01,Aurora,120000,15000,6000,8000,3500000,180000,820,40,25,0.72,6000
2025-05-01,Aurora,128000,14000,7000,6000,3320000,185000,845,35,22,0.72,6200
2025-06-01,Aurora,131000,12000,6000,9000,3145000,190000,850,28,23,0.72,6400
2025-07-01,Aurora,139000,15000,8000,5000,2960000,195000,870,32,12,0.72,6500
2025-04-01,Hearth,8000,3000,500,700,140000,28000,210,22,20,0.65,900
2025-05-01,Hearth,9200,2500,700,1000,120000,29000,218,18,15,0.65,950
2025-06-01,Hearth,9800,2200,500,1100,100000,30000,220,16,14,0.65,1000
2025-07-01,Hearth,10400,2400,800,600,90000,31000,224,18,12,0.65,1000
"""

# ---------------- Multiples ----------------
DEFAULT_MULTIPLES: Dict[str, Dict[str, List[float]]] = {
    "Developer Tools": {"Pre-Seed":[8,12,18],"Seed":[6,10,15],"Series A":[5,8,12],"Series B+":[4,6,9]},
    "Fintech":         {"Pre-Seed":[10,15,22],"Seed":[8,12,18],"Series A":[6,10,14],"Series B+":[5,8,12]},
    "Consumer Subscriptions": {"Pre-Seed":[5,8,12],"Seed":[4,6,9],"Series A":[3,5,8],"Series B+":[2.5,4,6]},
}

# ---------------- Utils ----------------
@st.cache_data
def load_csv(uploaded: Optional[io.BytesIO], fallback_csv: str) -> pd.DataFrame:
    if uploaded is not None:
        try:
            return pd.read_csv(uploaded)
        except Exception:
            uploaded.seek(0)
            return pd.read_csv(uploaded, sep=';')
    return pd.read_csv(io.StringIO(fallback_csv))

@st.cache_data
def normalize_dates(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    if col in df.columns:
        try:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.to_period('M').dt.to_timestamp()
        except Exception:
            pass
    return df

def safe_div(n, d):
    if isinstance(n, (pd.Series, np.ndarray)) or isinstance(d, (pd.Series, np.ndarray)):
        n_series = pd.Series(n)
        d_series = pd.Series(d).replace(0, np.nan)
        return n_series.astype(float).div(d_series)
    if d is None or (isinstance(d, float) and pd.isna(d)) or d == 0:
        return np.nan
    return n / d

# ---------------- Metric engine ----------------

def compute_metrics(kpis: pd.DataFrame) -> pd.DataFrame:
    df = kpis.sort_values('date').copy()
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
    adj = 1.0
    if pd.notna(row.get('rev_nrr_m')) and row['rev_nrr_m'] >= 1.05:
        adj *= 1.05
    if pd.notna(row.get('mrr_growth_mom')) and row['mrr_growth_mom'] >= 0.07:
        adj *= 1.05
    if pd.notna(row.get('burn_multiple')) and row['burn_multiple'] > 2.0:
        adj *= 0.9
    if pd.notna(row.get('rule_of_40')) and row['rule_of_40'] < 0:
        adj *= 0.95
    return adj

# ---------------- Fit scoring ----------------
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

def _norm(s: str) -> str:
    return (s or '').strip().lower()

def sector_score(company_sector: str, vc_sectors: List[str]) -> (int, str):
    cs = _norm(company_sector)
    v = [_norm(x) for x in vc_sectors]
    if cs in v:
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
    if any((_norm("europe") in v and c)):
        return 80, "Within Europe"
    return 50, "Geo unclear or outside focus"

def traction_score(latest: pd.Series) -> (int, List[str]):
    notes = []
    score_parts = []
    nrr = latest.get('rev_nrr_m')
    if pd.notna(nrr):
        if nrr >= 1.10: score_parts.append(95); notes.append("NRR ≥110% (excellent)")
        elif nrr >= 1.00: score_parts.append(80); notes.append("NRR ~100% (good)")
        else: score_parts.append(55); notes.append("NRR <100% (watch churn)")
    mom = latest.get('mrr_growth_mom')
    if pd.notna(mom):
        if mom >= 0.08: score_parts.append(90); notes.append("MoM ≥8% (fast)")
        elif mom >= 0.03: score_parts.append(75); notes.append("MoM 3–8% (steady)")
        else: score_parts.append(55); notes.append("MoM <3% (slow)")
    bm = latest.get('burn_multiple')
    if pd.notna(bm):
        if bm < 1: score_parts.append(95); notes.append("Burn multiple <1 (elite)")
        elif bm <= 2: score_parts.append(80); notes.append("Burn multiple 1–2 (good)")
        else: score_parts.append(55); notes.append("Burn multiple >2 (inefficient)")
    rw = latest.get('runway_m')
    if pd.notna(rw):
        if rw >= 18: score_parts.append(85); notes.append("Runway ≥18m")
        elif rw >= 12: score_parts.append(75); notes.append("Runway 12–18m")
        else: score_parts.append(60); notes.append("Runway <12m")
    if not score_parts:
        return 60, ["Insufficient KPI data; using neutral baseline"]
    return int(np.mean(score_parts)), notes

def check_size_score(arr: float, vc_min: Optional[float], vc_max: Optional[float]) -> (int, str):
    if vc_min is None and vc_max is None:
        return 80, "No check size provided"
    est_round = max(0.5, min(10.0, arr/100000.0))
    if vc_min and est_round < vc_min/1_000_000 * 0.5:
        return 55, "Company likely too early for VC check size"
    if vc_max and est_round > vc_max/1_000_000 * 2.0:
        return 60, "Company may be beyond preferred check size"
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
    return {
        "overall": overall,
        "breakdown": {
            "Sector": sec_s,
            "Stage": stg_s,
            "Traction": tr_s,
            "Geography": geo_s,
            "Check size": cs_s,
        },
        "reasons": reasons,
    }

# ---------------- App UI ----------------
st.set_page_config(page_title="VC Snapshot — Health + Valuation + Fit", layout="wide")
st.title("📈 VC Snapshot — Health, Valuation & VC Fit")

# Saved VC fit to include in export
if 'saved_fit' not in st.session_state:
    st.session_state['saved_fit'] = None

# --- Sidebar ---
with st.sidebar:
    st.header("🔧 Data Uploads")
    up_companies = st.file_uploader("companies.csv", type=["csv"], accept_multiple_files=False)
    up_kpis = st.file_uploader("kpis.csv", type=["csv"], accept_multiple_files=False)

    st.header("💻 Data Extractor (beta)")
    url_input = st.text_input("Startup website URL (optional)")
    doc_files = st.file_uploader("Upload PDFs/DOCs/TXT (optional)", type=["pdf","docx","txt"], accept_multiple_files=True)
    do_extract = st.button("Extract KPIs from URL/Docs")

    st.header("💲 Valuation Multiples (editable)")
    mult_text = st.text_area("Override multiples as JSON (sector → stage → [low, mid, high])", value=pd.Series(DEFAULT_MULTIPLES).to_json(), height=150)
    try:
        user_multiples = pd.read_json(io.StringIO(mult_text)).to_dict()
    except Exception:
        user_multiples = DEFAULT_MULTIPLES

# Data
companies = load_csv(up_companies, demo_companies_csv())
companies['stage'] = companies['stage'].astype('category')
companies['sector'] = companies['sector'].astype('category')

kpis = load_csv(up_kpis, demo_kpis_csv())
kpis = normalize_dates(kpis, 'date')

# Extractor hints
if do_extract:
    text_all = ""
    if url_input and requests and BeautifulSoup:
        try:
            r = requests.get(url_input, timeout=10)
            text_all += BeautifulSoup(r.text, 'lxml').get_text(" ", strip=True) + "\n"
        except Exception as e:
            st.sidebar.warning(f"Website parse failed: {e}")
    for f in (doc_files or []):
        name = getattr(f, 'name', 'file')
        try:
            if name.lower().endswith('.pdf') and PyPDF2:
                reader = PyPDF2.PdfReader(f)
                text_all += " ".join(page.extract_text() or "" for page in reader.pages)
            elif name.lower().endswith('.docx') and docx:
                d = docx.Document(f)
                text_all += " ".join(p.text for p in d.paragraphs)
            else:
                text_all += f.read().decode(errors='ignore')
        except Exception as e:
            st.sidebar.warning(f"Failed to parse {name}: {e}")
    def find_number(lbls):
        if not text_all: return np.nan
        m = re.search(r"("+"|".join([re.escape(x) for x in lbls])+r").{0,40}?([€$£]?[\s]*[\d,.]+)", text_all, flags=re.I)
        if m:
            raw = m.group(2).replace(',', '')
            try: return float(re.sub(r"[^0-9.]+","", raw))
            except Exception: return np.nan
        return np.nan
    hints = {
        'ARR≈': find_number(['ARR','annual recurring revenue']),
        'MRR≈': find_number(['MRR','monthly recurring revenue']),
        'Customers≈': find_number(['customers','active users']),
        'Burn≈': find_number(['burn','net burn']),
        'Cash≈': find_number(['cash balance','cash']),
    }
    st.sidebar.info("Extraction hints → " + ", ".join([f"{k}{v}" for k,v in hints.items() if not pd.isna(v)]))

# --- Main analysis ---
st.subheader("Company Overview")
col1, col2, col3 = st.columns([2,1,1])
with col1:
    selected_company = st.selectbox("Choose a company", companies['company'].unique().tolist())
with col2:
    show_demo = st.toggle("Demo mode (pre-fills)", value=True)
with col3:
    currency = st.selectbox("Currency", ["€","$","£"], index=0)

ck = kpis[kpis['company'] == selected_company].copy().sort_values('date')
metrics_df = compute_metrics(ck)

meta = companies[companies['company'] == selected_company].iloc[0].to_dict()
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

# --- Valuation ---
st.markdown("---")
st.subheader("💰 Valuation Benchmarker")
arr = float(latest['arr']) if (latest is not None and pd.notna(latest['arr'])) else 0.0
band = get_multiple_band(sector, stage, user_multiples)
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

# --- Button: Target by VC (Fit Scorer) ---
st.markdown("---")
show_fit = st.button("🎯 Target by VC — Measure Fit")
if show_fit:
    with st.expander("VC Fit Scorer", expanded=True):
        st.markdown("Define the VC profile (anything goes). The tool scores fit vs the current company.")
        c1, c2 = st.columns(2)
        with c1:
            vc_name = st.text_input("VC name", value="Example VC")
            vc_sectors = st.multiselect("Preferred sectors", options=list(DEFAULT_MULTIPLES.keys()) + ["DevTools","Consumer","AI","Infra"], default=[sector])
            vc_stages = st.multiselect("Preferred stages", options=["Pre-Seed","Seed","Series A","Series B+"], default=[stage])
        with c2:
            vc_geos = st.text_input("Geographies (comma-separated)", value="Europe, Italy").split(',')
            min_check = st.number_input("Min check (€)", min_value=0.0, value=250000.0, step=50000.0)
            max_check = st.number_input("Max check (€)", min_value=0.0, value=2000000.0, step=100000.0)
        if latest is None:
            st.warning("No KPI rows for this company; fit will be limited.")
        else:
            profile = VCProfile(name=vc_name, sectors=vc_sectors, stages=vc_stages, geos=vc_geos, min_check=min_check, max_check=max_check)
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
            # NEW: Save Fit to report
            if st.button("💾 Save Fit to report"):
                st.session_state['saved_fit'] = {
                    'timestamp': pd.Timestamp.utcnow().isoformat(),
                    'company': selected_company,
                    'vc_profile': {
                        'name': vc_name,
                        'sectors': vc_sectors,
                        'stages': vc_stages,
                        'geos': [g.strip() for g in vc_geos if g.strip()],
                        'min_check': float(min_check) if min_check else None,
                        'max_check': float(max_check) if max_check else None,
                    },
                    'fit': fit,
                }
                st.success(f"Saved VC fit for {vc_name} to include in the export.")

# --- Export: metrics CSV, portfolio CSV, HTML report ---
st.markdown("---")
st.subheader("⬇️ Exports")

# Portfolio snapshot
rows = []
for c in companies['company'].unique():
    kk = kpis[kpis['company'] == c].copy().sort_values('date')
    if kk.empty: continue
    md = compute_metrics(kk)
    last = md.iloc[-1]
    cmeta = companies[companies['company'] == c].iloc[0].to_dict()
    band_c = get_multiple_band(cmeta.get('sector','Generic'), cmeta.get('stage','Seed'), user_multiples)
    adj_c = quality_adjustment(last)
    rows.append({
        'company': c,
        'sector': cmeta.get('sector'),
        'stage': cmeta.get('stage'),
        'ARR': last['arr'],
        'MRR MoM %': last['mrr_growth_mom'],
        'NRR (m)': last['rev_nrr_m'],
        'Burn Multiple': last['burn_multiple'],
        'Runway (m)': last['runway_m'],
        'Implied Value (mid)': (last['arr'] or 0) * band_c[1] * adj_c,
    })

colx, coly, colz = st.columns(3)
with colx:
    if not metrics_df.empty:
        csv = metrics_df.to_csv(index=False).encode('utf-8')
        st.download_button("Download company metrics CSV", csv, file_name=f"{selected_company}_metrics.csv", mime='text/csv')
with coly:
    if rows:
        port_csv = pd.DataFrame(rows).to_csv(index=False).encode('utf-8')
        st.download_button("Download portfolio snapshot CSV", port_csv, file_name="portfolio_snapshot.csv", mime='text/csv')
with colz:
    gen_html = st.button("Generate Report Page (HTML)")

if gen_html and not metrics_df.empty:
    # charts → base64 images
    buf1 = io.BytesIO(); plt.figure(); plt.plot(metrics_df['date'], metrics_df['mrr']); plt.plot(metrics_df['date'], metrics_df['arr']); plt.title(f"{selected_company} — MRR & ARR"); plt.xlabel('Date'); plt.ylabel('Amount'); plt.tight_layout(); plt.savefig(buf1, format='png'); plt.close()
    buf2 = io.BytesIO(); plt.figure(); plt.plot(metrics_df['date'], metrics_df['rev_nrr_m']); plt.plot(metrics_df['date'], metrics_df['rev_grr_m']); plt.plot(metrics_df['date'], metrics_df['burn_multiple']); plt.title(f"{selected_company} — Retention & Burn"); plt.xlabel('Date'); plt.ylabel('Value'); plt.tight_layout(); plt.savefig(buf2, format='png'); plt.close()
    img1 = base64.b64encode(buf1.getvalue()).decode(); img2 = base64.b64encode(buf2.getvalue()).decode()

    author_logo_img = f"<img src='{AUTHOR_LOGO_URL}' alt='' style='max-width:100%;max-height:100%;'>" if AUTHOR_LOGO_URL else ""

    # Optional VC Fit section if saved for this company
    fit_html = ""
    if st.session_state.get('saved_fit'):
        sf = st.session_state['saved_fit']
        if sf and sf.get('company') == selected_company:
            prof = sf['vc_profile']
            f = sf['fit']
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
          <div><b>MoM MRR Growth</b><br>{fmt_pct(latest['mrr_growth_mom']) if latest is not None else '—'}</div>
          <div><b>NRR (monthly)</b><br>{fmt_pct(latest['rev_nrr_m']) if latest is not None else '—'}</div>
          <div><b>Burn Multiple</b><br>{'—' if latest is None or pd.isna(latest['burn_multiple']) else f"{latest['burn_multiple']:.2f}"}</div>
          <div><b>Runway (months)</b><br>{'—' if latest is None or pd.isna(latest['runway_m']) else f"{latest['runway_m']:.1f}"}</div>
          <div><b>CAC Payback (months)</b><br>{'—' if latest is None or pd.isna(latest['cac_payback_m']) else f"{latest['cac_payback_m']:.1f}"}</div>
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
          <div style='width:56px;height:56px;border-radius:50%;overflow:hidden;background:#f4f4f4;display:flex;align-items:center;justify-content:center;'>{author_logo_img}</div>
          <div>
            <div><b>{AUTHOR_NAME}</b></div>
            <div><a href='mailto:{AUTHOR_EMAIL}'>{AUTHOR_EMAIL}</a></div>
            <div><a href='{AUTHOR_LINKEDIN}' target='_blank' rel='noopener noreferrer'>LinkedIn</a></div>
          </div>
        </div>
      </div>

      <p class='small'>Generated by VC Snapshot.</p>
    </body></html>
    """
    st.download_button("Download report.html", html.encode('utf-8'), file_name=f"{selected_company}_snapshot.html", mime='text/html')

st.caption("Sidebar → upload real KPIs or use extractor hints. Main → analyze & value. Button → score & optionally save VC fit into the export.")
