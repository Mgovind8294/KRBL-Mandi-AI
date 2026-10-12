import os
from datetime import date, timedelta
import requests
import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="KRBL Mandi AI | Paddy Intelligence", page_icon="🌾", layout="wide")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
API_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

def get_api_key():
    try:
        return str(st.secrets.get("DATA_GOV_IN_API_KEY", "") or os.getenv("DATA_GOV_IN_API_KEY", "")).strip()
    except Exception:
        return os.getenv("DATA_GOV_IN_API_KEY", "").strip()

@st.cache_data(ttl=900, show_spinner=False)
def fetch_data(api_key, limit=1000):
    params = {"api-key": api_key, "format": "json", "limit": limit}
    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("records", []) if isinstance(payload, dict) else []
    if not rows:
        message = payload.get("message", "API returned no records") if isinstance(payload, dict) else "Unexpected API response"
        raise ValueError(message)
    df = pd.DataFrame(rows)
    aliases = {
        "state": "State", "district": "District", "market": "Market",
        "commodity": "Commodity", "variety": "Variety", "grade": "Grade",
        "arrival_date": "Arrival Date", "min_price": "Min Price (₹/qtl)",
        "max_price": "Max Price (₹/qtl)", "modal_price": "Modal Price (₹/qtl)",
        "commodity_variety": "Variety"
    }
    for source, target in aliases.items():
        if source in df.columns and target not in df.columns:
            df[target] = df[source]
    if "Arrival Date" in df.columns:
        df["Arrival Date"] = pd.to_datetime(df["Arrival Date"], errors="coerce", dayfirst=True)
    for col in ["Min Price (₹/qtl)", "Max Price (₹/qtl)", "Modal Price (₹/qtl)"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

st.markdown("## 🌾 KRBL Mandi AI — India Paddy Price Intelligence")
st.caption("Government-reported mandi prices from data.gov.in / AGMARKNET. Forecasts are estimates, not official government predictions.")
with st.sidebar:
    st.header("Configuration")
    api_key = st.text_input("data.gov.in API key", value=get_api_key(), type="password",
                            help="For deployment, add DATA_GOV_IN_API_KEY in Streamlit App settings → Secrets.")
    limit = st.select_slider("Records to request", options=[100, 500, 1000], value=1000)
    if st.button("Refresh government data", use_container_width=True):
        fetch_data.clear()
        st.rerun()
    st.divider()
    st.caption("Source: Government of India OGD mandi-price resource")
    st.markdown("[Open official dataset](https://www.data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi)")

if not api_key:
    st.warning("API key is not configured. Add DATA_GOV_IN_API_KEY in Streamlit Secrets, then reload this page.")
    st.markdown("**How to activate:** 1. Get an API key at [data.gov.in](https://data.gov.in/). 2. In Streamlit app settings → Secrets, add:")
    st.code('DATA_GOV_IN_API_KEY = "YOUR_REAL_API_KEY"', language="toml")
    st.stop()

try:
    with st.spinner("Fetching latest available government records…"):
        df = fetch_data(api_key, limit)
    st.success(f"Government API connected — {len(df):,} records fetched. Source updates are controlled by the government publisher.")
except Exception as exc:
    st.error(f"Government API could not be verified: {exc}")
    st.info("Check the API key, resource access, internet availability and data.gov.in service status. No sample prices are substituted.")
    st.stop()

if "Commodity" in df.columns:
    commodities = sorted(df["Commodity"].dropna().astype(str).unique())
    default_commodity = next((x for x in commodities if "paddy" in x.lower() or "rice" in x.lower()), commodities[0] if commodities else None)
    selected_commodities = st.multiselect("Commodity", commodities, default=[default_commodity] if default_commodity else [])
    if selected_commodities:
        df = df[df["Commodity"].astype(str).isin(selected_commodities)]
for col in ["State", "District", "Market", "Variety"]:
    if col in df.columns:
        options = sorted(df[col].dropna().astype(str).unique())
        selected = st.multiselect(col, options, key=f"filter_{col}")
        if selected:
            df = df[df[col].astype(str).isin(selected)]

price_col = "Modal Price (₹/qtl)" if "Modal Price (₹/qtl)" in df.columns else None
if price_col and df[price_col].notna().any():
    a, b, c = st.columns(3)
    a.metric("Average modal price", f"₹{df[price_col].mean():,.0f}/qtl")
    b.metric("Lowest modal price", f"₹{df[price_col].min():,.0f}/qtl")
    c.metric("Highest modal price", f"₹{df[price_col].max():,.0f}/qtl")
else:
    st.warning("No modal-price values are present in the returned records.")

if "Arrival Date" in df.columns:
    st.caption("Latest arrival date in returned data: " + (df["Arrival Date"].max().strftime("%d %b %Y") if df["Arrival Date"].notna().any() else "not supplied"))
st.subheader("Government-reported mandi records")
st.dataframe(df, use_container_width=True, hide_index=True)
st.download_button("Download filtered government records (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                   "government_mandi_prices.csv", "text/csv")

st.subheader("Indicative 1–7 day price trend")
st.caption("A simple statistical baseline from the fetched government records. Not a validated trading model; arrival prediction is not shown unless the source supplies reliable arrival quantities.")
if price_col and "Arrival Date" in df.columns:
    series = df.dropna(subset=["Arrival Date", price_col]).groupby("Arrival Date", as_index=False)[price_col].mean().sort_values("Arrival Date")
    if len(series) >= 3:
        horizon = st.slider("Forecast horizon (days)", 1, 7, 3)
        recent = series[price_col].tail(7)
        baseline = float(recent.mean())
        change = float(recent.diff().dropna().mean()) if len(recent) > 1 else 0.0
        last_date = series["Arrival Date"].max().date()
        forecasts = pd.DataFrame([{"Forecast date": last_date + timedelta(days=i), "Indicative modal price (₹/qtl)": round(max(0, baseline + change*i), 0)} for i in range(1, horizon+1)])
        st.dataframe(forecasts, use_container_width=True, hide_index=True)
        st.plotly_chart(px.line(forecasts, x="Forecast date", y="Indicative modal price (₹/qtl)", markers=True), use_container_width=True)
        st.download_button("Download forecast CSV", forecasts.to_csv(index=False).encode("utf-8-sig"), "indicative_price_forecast.csv", "text/csv")
    else:
        st.info("Not enough dated price observations in the filtered records to produce a trend estimate.")
else:
    st.info("Price trend requires date and modal-price fields in the API response.")

st.divider()
st.caption("Data source: Government of India Open Government Data (AGMARKNET). Government records may be delayed, incomplete or unavailable. Forecasts are indicative and should not be used as guaranteed prices.")
