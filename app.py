import os
import time
from datetime import timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(page_title="KRBL Mandi AI | Paddy Intelligence", page_icon="🌾", layout="wide")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
API_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

AGMARKNET_BASE = "https://api.agmarknet.gov.in/v1"
AGMARKNET_STATES = ["Punjab", "Haryana", "Uttar Pradesh", "Rajasthan", "Uttarakhand", "Delhi"]

def agmarknet_session():
    session = requests.Session()
    retry = Retry(
        total=2, connect=2, read=1, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"], raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://agmarknet.gov.in",
        "Referer": "https://agmarknet.gov.in/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    })
    return session

def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)

def _find_metadata_id(payload, terms, kind):
    """Resolve IDs from AGMARKNET's actual short-key filter arrays as well as alternate schemas."""
    if not isinstance(payload, (dict, list)):
        return None, None
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    short_key = {"state": "state_data", "commodity": "cmdt_data"}[kind]
    rows = data.get(short_key, []) if isinstance(data, dict) else []
    # Explicitly inspect the named array first: API fields are commonly abbreviated (state_name/cmdt_name).
    if isinstance(rows, list):
        id_candidates = {
            "state": ("state_id", "stateId", "stateid", "state_code", "stateCode", "id", "value"),
            "commodity": ("cmdt_id", "cmdtId", "cmdtid", "commodity_id", "commodityId", "commodityid", "comm_id", "commId", "commodity_code", "commodityCode", "id", "value"),
        }[kind]
        name_candidates = {
            "state": ("state_name", "stateName", "statename", "state_desc", "stateDesc", "name", "label", "state"),
            "commodity": ("cmdt_name", "cmdtName", "cmdtname", "cmdt_desc", "cmdtDesc", "commodity_name", "commodityName", "commodityname", "commodity_desc", "commodityDesc", "commodity_description", "comm_name", "commName", "commodity_name_en", "name", "label", "commodity"),
        }[kind]
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = next((str(row[k]) for k in name_candidates if row.get(k) is not None), "")
            ident = next((row[k] for k in id_candidates if row.get(k) is not None), None)
            norm = " ".join(name.lower().replace("(dhan)", "").replace("(common)", "").replace("-", " ").split())
            if ident is not None and name and any(term in norm for term in terms):
                return ident, name
    # Fallback for nested or alternate API payloads.
    id_keys = {
        "state": ("state_id", "stateId", "stateid", "state_code", "stateCode", "id", "value"),
        "commodity": ("cmdt_id", "cmdtId", "cmdtid", "commodity_id", "commodityId", "commodityid", "comm_id", "commId", "commodity_code", "commodityCode", "id", "value"),
    }[kind]
    name_keys = {
        "state": ("state_name", "stateName", "statename", "state_desc", "stateDesc", "name", "label", "state"),
        "commodity": ("cmdt_name", "cmdtName", "cmdtname", "cmdt_desc", "cmdtDesc", "commodity_name", "commodityName", "commodityname", "commodity_desc", "commodityDesc", "commodity_description", "comm_name", "commName", "commodity_name_en", "name", "label", "commodity"),
    }[kind]
    candidates = []
    for item in _walk_dicts(payload):
        name = next((str(item[k]) for k in name_keys if k in item and item[k] is not None), "")
        ident = next((item[k] for k in id_keys if k in item and item[k] is not None), None)
        norm = " ".join(name.lower().replace("(dhan)", "").replace("(common)", "").replace("-", " ").split())
        if name and ident is not None and any(term in norm for term in terms):
            candidates.append((name, ident))
    return (candidates[0][1], candidates[0][0]) if candidates else (None, None)

def _extract_record_list(payload):
    """Extract a list of row dictionaries from varying AGMARKNET JSON envelopes."""
    if isinstance(payload, list) and payload and all(isinstance(x, dict) for x in payload):
        return payload
    best = []
    for item in _walk_dicts(payload):
        for value in item.values():
            if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                if len(value) > len(best):
                    best = value
    return best

@st.cache_data(ttl=900, show_spinner=False)
def fetch_agmarknet_data():
    """Fetch official AGMARKNET price/arrival observations for the six requested states."""
    session = agmarknet_session()
    base = AGMARKNET_BASE
    filters_response = session.get(f"{base}/daily-price-arrival/filters", timeout=(12, 45))
    filters_response.raise_for_status()
    metadata = filters_response.json()
    if not isinstance(metadata, (dict, list)):
        raise ValueError("AGMARKNET filter endpoint returned an unexpected response.")
    now = pd.Timestamp.now().normalize()
    previous_month = now - pd.DateOffset(months=1)
    months = [(now.year, now.month), (previous_month.year, previous_month.month)]
    frames, errors = [], []
    for state in AGMARKNET_STATES:
        state_id, state_label = _find_metadata_id(metadata, [state.lower()], "state")
        if state_id is None:
            errors.append(f"{state}: state ID not found in AGMARKNET filters")
            continue
        # Try common paddy labels; the exact label and ID are taken from official filter metadata.
        commodity_id, commodity_label = _find_metadata_id(
            metadata,
            ["paddy dhan basmati", "basmati", "paddy dhan common", "paddy", "dhan"],
            "commodity",
        )
        if commodity_id is None:
            # The AGMARKNET filter payload uses short keys such as cmdt_data.
            # Include a small sample of that exact list in diagnostics to expose the real schema.
            data_block = metadata.get("data") if isinstance(metadata, dict) else None
            cmdt_data = data_block.get("cmdt_data") if isinstance(data_block, dict) else None
            cmdt_sample = repr(cmdt_data[:5] if isinstance(cmdt_data, list) else cmdt_data)[:1400]
            top_keys = list(metadata.keys())[:20] if isinstance(metadata, dict) else ["<top-level list>"]
            data_keys = list(data_block.keys())[:30] if isinstance(data_block, dict) else []
            raise RuntimeError(
                "Paddy commodity ID not found in AGMARKNET filters. "
                f"Top-level keys: {top_keys}; data keys: {data_keys}; "
                f"cmdt_data sample: {cmdt_sample}"
            )
        for year, month in months:
            try:
                resp = session.get(
                    f"{base}/prices-and-arrivals/date-wise/specific-commodity",
                    params={"year": year, "month": month, "stateId": state_id, "commodityId": commodity_id,
                            "includeExcel": "false"},
                    timeout=(12, 45),
                )
                resp.raise_for_status()
                rows = _extract_record_list(resp.json())
                if not rows:
                    continue
                frame = standardize_columns(pd.DataFrame(rows))
                if "State" not in frame.columns:
                    frame["State"] = state
                frames.append(frame)
            except Exception as exc:
                errors.append(f"{state} {year}-{month:02d}: {type(exc).__name__}")
    if not frames:
        raise RuntimeError("AGMARKNET API responded, but no usable price/arrival rows were returned. " + "; ".join(errors[:8]))
    result = pd.concat(frames, ignore_index=True, sort=False)
    result.attrs["state_errors"] = errors
    result.attrs["api_rows_fetched"] = len(result)
    result.attrs["commodity_label"] = commodity_label
    return result

st.markdown("""
<style>
.block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
[data-testid="stMetric"] {background: #f7f9fc; border: 1px solid #e5eaf2; padding: 14px; border-radius: 12px;}
</style>
""", unsafe_allow_html=True)

def get_api_key():
    try:
        return str(st.secrets.get("DATA_GOV_IN_API_KEY", "") or os.getenv("DATA_GOV_IN_API_KEY", "")).strip()
    except Exception:
        return os.getenv("DATA_GOV_IN_API_KEY", "").strip()

def standardize_columns(frame):
    df = frame.copy()
    aliases = {
        "state": "State", "state name": "State", "district": "District", "district name": "District",
        "market": "Market", "mandi": "Market", "market name": "Market", "commodity": "Commodity",
        "crop": "Commodity", "variety": "Variety", "grade": "Grade", "arrival_date": "Arrival Date",
        "arrival date": "Arrival Date", "date": "Arrival Date", "price date": "Arrival Date", "reported date": "Arrival Date",
        "min_price": "Min Price (₹/qtl)", "min price": "Min Price (₹/qtl)",
        "max_price": "Max Price (₹/qtl)", "max price": "Max Price (₹/qtl)",
        "modal_price": "Modal Price (₹/qtl)", "modal price": "Modal Price (₹/qtl)",
        "modal price (₹/qtl)": "Modal Price (₹/qtl)", "modal price (rs/qtl)": "Modal Price (₹/qtl)", "modal price (rs/qtl.)": "Modal Price (₹/qtl)", "price": "Modal Price (₹/qtl)",
        "arrival": "Arrival Quantity", "arrivals": "Arrival Quantity", "arrivals (tonnes)": "Arrival Quantity", "arrivals (qtl)": "Arrival Quantity", "arrival (qtl)": "Arrival Quantity", "arrivals (in tonnes)": "Arrival Quantity",
        "arrival qtl": "Arrival Quantity", "arrival quantity": "Arrival Quantity",
        "arrival quantity (tonnes)": "Arrival Quantity", "arrival (tonnes)": "Arrival Quantity",
        "quantity": "Arrival Quantity", "avg price rs qtl": "Modal Price (₹/qtl)",
        "avg price rs/qtl": "Modal Price (₹/qtl)", "avg price": "Modal Price (₹/qtl)",
        "purchase qtl": "Purchase Quantity (Qtl)",
    }
    for col in list(df.columns):
        clean = str(col).strip().lower().replace("-", " ").replace("_", " ")
        clean = " ".join(clean.split())
        target = aliases.get(clean)
        if target and target not in df.columns:
            df.rename(columns={col: target}, inplace=True)
    if "Commodity" not in df.columns:
        df["Commodity"] = "Unknown"
    if "Market" not in df.columns:
        df["Market"] = "Unknown"
    if "Arrival Date" in df.columns:
        df["Arrival Date"] = pd.to_datetime(df["Arrival Date"], errors="coerce", dayfirst=True)
    for col in ["Min Price (₹/qtl)", "Max Price (₹/qtl)", "Modal Price (₹/qtl)", "Arrival Quantity"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    return df

def api_session():
    session = requests.Session()
    retry = Retry(total=2, connect=2, read=1, backoff_factor=0.8,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], raise_on_status=False)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session

@st.cache_data(ttl=900, show_spinner=False)
def fetch_data(api_key, limit=500):
    """Fetch a bounded sample separately for each requested state; fail clearly if all calls fail."""
    session = api_session()
    states = ["Punjab", "Haryana", "Uttar Pradesh", "Rajasthan", "Uttarakhand", "Delhi"]
    per_state = max(50, min(1000, int(limit)))
    all_rows, errors = [], []
    totals = {}
    for state in states:
        params = {
            "api-key": api_key, "format": "json", "limit": per_state, "offset": 0,
            "filters[state.keyword]": state, "filters[commodity]": "Paddy(Dhan)(Common)"
        }
        try:
            response = session.get(API_URL, params=params, timeout=(8, 25))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Unexpected response format")
            if payload.get("error") or (payload.get("message") and not payload.get("records")):
                raise ValueError(str(payload.get("error") or payload.get("message"))[:160])
            rows = payload.get("records") or []
            totals[state] = payload.get("total", len(rows))
            # Some API records use "Paddy(Dhan)(Common)" or basmati labels.
            all_rows.extend(rows)
            if not rows:
                errors.append(f"{state}: no matching records")
        except Exception as exc:
            errors.append(f"{state}: {type(exc).__name__}")
    if not all_rows:
        raise RuntimeError("Government API returned no records for the six selected states. " + "; ".join(errors))
    result = standardize_columns(pd.DataFrame(all_rows))
    result.attrs["state_totals"] = totals
    result.attrs["state_errors"] = errors
    result.attrs["api_rows_fetched"] = len(all_rows)
    return result

def read_uploaded_file(upload):
    if upload.name.lower().endswith((".xlsx", ".xls")):
        # Prefer the workbook historical transaction sheet over the dashboard/control sheets.
        book = pd.ExcelFile(upload)
        sheet = "Raw_Data" if "Raw_Data" in book.sheet_names else book.sheet_names[0]
        return pd.read_excel(book, sheet_name=sheet)
    return pd.read_csv(upload)

def build_forecast(history, target_col, horizon):
    """Simple transparent lag-feature ML model; only returns a model when enough daily history exists."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error
    from sklearn.model_selection import train_test_split

    daily = history.dropna(subset=["Arrival Date", target_col]).groupby("Arrival Date", as_index=False)[target_col].mean()
    daily = daily.sort_values("Arrival Date").set_index("Arrival Date").asfreq("D")
    daily[target_col] = daily[target_col].interpolate(limit=3).ffill().bfill()
    values = daily[target_col].dropna()
    if len(values) < 18:
        return None, None, "At least 18 dated daily observations are required for the ML forecast."
    data = pd.DataFrame({"y": values})
    for lag in [1, 2, 3, 7, 14]:
        data[f"lag_{lag}"] = data["y"].shift(lag)
    data["rolling_7"] = data["y"].shift(1).rolling(7).mean()
    data["day_of_year"] = data.index.dayofyear
    data["day_of_week"] = data.index.dayofweek
    data = data.dropna()
    if len(data) < 12:
        return None, None, "There are not enough complete lag features for a meaningful forecast."
    features = [c for c in data.columns if c != "y"]
    split = max(8, int(len(data) * 0.8))
    if len(data) - split < 2:
        split = len(data) - 2
    train, test = data.iloc[:split], data.iloc[split:]
    model = RandomForestRegressor(n_estimators=250, min_samples_leaf=2, random_state=42, n_jobs=-1)
    model.fit(train[features], train["y"])
    mae = mean_absolute_error(test["y"], model.predict(test[features]))
    full_model = RandomForestRegressor(n_estimators=250, min_samples_leaf=2, random_state=42, n_jobs=-1)
    full_model.fit(data[features], data["y"])
    work = values.copy()
    forecast_rows = []
    last_date = values.index.max()
    for step in range(1, horizon + 1):
        next_date = last_date + pd.Timedelta(days=step)
        def lag_val(n):
            ix = next_date - pd.Timedelta(days=n)
            return float(work.loc[ix]) if ix in work.index else float(work.iloc[-min(n, len(work))])
        row = {
            "lag_1": lag_val(1), "lag_2": lag_val(2), "lag_3": lag_val(3),
            "lag_7": lag_val(7), "lag_14": lag_val(14),
            "rolling_7": float(work.loc[work.index < next_date].tail(7).mean()),
            "day_of_year": next_date.dayofyear, "day_of_week": next_date.dayofweek,
        }
        pred = max(0.0, float(full_model.predict(pd.DataFrame([row])[features])[0]))
        work.loc[next_date] = pred
        forecast_rows.append({"Forecast Date": next_date, f"Predicted {target_col}": round(pred, 2)})
    return pd.DataFrame(forecast_rows), float(mae), None

st.title("🌾 KRBL Mandi AI — India Paddy Price Intelligence")
st.caption("Government mandi observations + transparent historical forecasting. Government price data is updated by the publisher, not tick-by-tick real time; forecasts are estimates.")

with st.sidebar:
    st.header("Data configuration")
    api_key = st.text_input("data.gov.in API key", value=get_api_key(), type="password",
                            help="Keep the key private. For deployment use Streamlit App settings → Secrets.")
    limit = st.select_slider("Recent government records to fetch", options=[100, 250, 500, 1000], value=500,
                             help="Fetched in pages of 100, newest arrival dates requested first where the API supports sorting.")
    if st.button("Load / refresh OGD data", use_container_width=True):
        fetch_data.clear()
        st.session_state["run_ogd_fetch"] = True
        st.rerun()
    st.divider()
    st.caption("Government source: OGD / AGMARKNET")
    st.markdown("[Open official mandi dataset](https://www.data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi)")

df = None
data_source = None
live_df = None
history_df = None
api_errors = []

agmarknet_df = None
agmarknet_errors = []
with st.expander("AGMARKNET live connection (official price & arrival source)", expanded=not bool(get_api_key())):
    st.write("This connector uses the public AGMARKNET 2.0 API behind the Daily Price and Arrival Report. No data.gov.in API key is needed for this connection.")
    if st.button("Test / load AGMARKNET live data", key="load_agmarknet", use_container_width=True):
        fetch_agmarknet_data.clear()
        try:
            with st.spinner("Connecting to official AGMARKNET and loading recent monthly reports…"):
                agmarknet_df = fetch_agmarknet_data()
            st.session_state["agmarknet_live_rows"] = agmarknet_df
            st.session_state["agmarknet_live_errors"] = agmarknet_df.attrs.get("state_errors", [])
            st.success(f"AGMARKNET returned {len(agmarknet_df):,} rows.")
        except Exception as exc:
            st.session_state["agmarknet_live_error"] = f"{type(exc).__name__}: {str(exc)[:350]}"
            st.error("AGMARKNET data could not be loaded. Details: " + st.session_state["agmarknet_live_error"])
    if st.session_state.get("agmarknet_live_error"):
        st.caption("Last connection result: " + st.session_state["agmarknet_live_error"])
    if st.session_state.get("agmarknet_live_errors"):
        st.warning("Some state/month queries were empty or failed: " + "; ".join(st.session_state["agmarknet_live_errors"][:8]))
if st.session_state.get("agmarknet_live_rows") is not None:
    agmarknet_df = st.session_state["agmarknet_live_rows"].copy()
if api_key and st.session_state.get("run_ogd_fetch", False):
    # Consume the one-shot request before networking so reruns never repeat slow API calls.
    st.session_state["run_ogd_fetch"] = False
    try:
        with st.spinner("Connecting to Government of India mandi data for six states…"):
            live_df = fetch_data(api_key, limit)
        api_errors = live_df.attrs.get("state_errors", [])
        latest_api_date = live_df["Arrival Date"].max() if "Arrival Date" in live_df.columns else pd.NaT
        date_note = latest_api_date.strftime("%d %b %Y") if pd.notna(latest_api_date) else "date unavailable"
        st.success(f"Government API responded — {len(live_df):,} records across the six-state query. Latest observation in fetched rows: {date_note}.")
        if api_errors:
            st.warning("Some state requests returned no rows or failed: " + "; ".join(api_errors))
        st.caption("Source is daily AGMARKNET/OGD data, not tick-by-tick real-time quotes. The latest returned date may differ by mandi.")
    except requests.exceptions.Timeout:
        st.warning("Government API timed out. Historical Excel will remain available; no sample/live-looking data is substituted.")
    except requests.exceptions.ConnectionError:
        st.warning("Could not connect to api.data.gov.in from this hosting environment. Historical Excel remains available.")
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        st.warning(f"Government API returned HTTP {status or 'error'}. Check the API key, resource access and portal status.")
    except Exception as exc:
        st.warning(f"Government data could not be loaded ({type(exc).__name__}). Historical workbook mode remains available.")
elif not api_key:
    st.info("OGD API key not configured. AGMARKNET can be tested separately in the section above; no key is needed for that connector.")
else:
    st.info("OGD requests are paused to keep the dashboard responsive. Use 'Load / refresh OGD data' in the sidebar only if you want to test the data.gov.in connector.")

st.subheader("Historical workbook / offline fallback")
st.write("Upload your existing Paddy Price Prediction Excel. Its historical records are kept separate from official government observations, and the original workbook is not modified.")
uploaded = st.file_uploader("Upload historical mandi data (.xlsx, .xls, .csv)", type=["xlsx", "xls", "csv"])
if uploaded is not None:
    try:
        history_df = standardize_columns(read_uploaded_file(uploaded))
        history_df["Data Source"] = "Historical Excel"
        st.success(f"Historical workbook loaded: {len(history_df):,} rows. Original workbook remains unchanged.")
    except Exception as exc:
        st.error(f"Could not read the uploaded file ({type(exc).__name__}). Check that it is a valid Excel/CSV file.")

if live_df is not None:
    live_df["Data Source"] = "Government OGD/AGMARKNET"
if live_df is not None and history_df is not None:
    # Union schemas without overwriting the historical workbook's original observations.
    df = pd.concat([history_df, live_df], ignore_index=True, sort=False)
    data_source = "Combined: Historical Excel + Government OGD/AGMARKNET"
elif history_df is not None:
    df = history_df.copy()
    data_source = "Historical Excel"
elif live_df is not None:
    df = live_df.copy()
    data_source = "Government OGD/AGMARKNET"

if df is None:
    st.markdown("### Next step")
    st.write("No data is currently loaded. Live records may be unreachable because of API access, hosting-network, or portal availability issues. The app will not substitute fabricated or sample records.")
    st.markdown("- Check the [official data.gov.in portal](https://data.gov.in/).")
    st.markdown("- Keep the API key in Streamlit Secrets; never publish it in GitHub or screenshots.")
    st.markdown("- Upload your historical Excel above to activate forecasting while the API is unavailable.")
    st.stop()

st.caption(f"Current data source: **{data_source}**")
if "Arrival Date" not in df.columns:
    st.error("Date column was not recognized. Rename the date column to 'Arrival Date' or 'Date' and upload again.")
    st.dataframe(df.head(10), use_container_width=True)
    st.stop()

if "Commodity" in df.columns:
    commodities = sorted(df["Commodity"].dropna().astype(str).unique())
    defaults = [x for x in commodities if "paddy" in x.lower() or "dhan" in x.lower()]
    selected = st.multiselect("Commodity", commodities, default=defaults or commodities[:1])
    if selected:
        df = df[df["Commodity"].astype(str).isin(selected)]
    elif data_source == "Live government API":
        st.info("No commodity selected, so all fetched commodities are shown.")
for col in ["State", "District", "Market", "Variety", "Data Source"]:
    if col in df.columns:
        opts = sorted(df[col].dropna().astype(str).unique())
        selected = st.multiselect(col, opts, key=f"filter_{col}")
        if selected:
            df = df[df[col].astype(str).isin(selected)]

price_col = "Modal Price (₹/qtl)" if "Modal Price (₹/qtl)" in df.columns else None
if price_col and df[price_col].notna().any():
    a, b, c, d = st.columns(4)
    a.metric("Average modal price", f"₹{df[price_col].mean():,.0f}/qtl")
    b.metric("Lowest modal price", f"₹{df[price_col].min():,.0f}/qtl")
    c.metric("Highest modal price", f"₹{df[price_col].max():,.0f}/qtl")
    d.metric("Records", f"{len(df):,}")
else:
    st.warning("Modal-price column was not found or has no numeric values. Expected column: Modal Price (₹/qtl).")

if df["Arrival Date"].notna().any():
    st.caption("Latest observation date in the selected data: " + df["Arrival Date"].max().strftime("%d %b %Y"))
st.subheader("Mandi records")
st.dataframe(df, use_container_width=True, hide_index=True)
st.download_button("Download filtered records (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                   "krbl_mandi_filtered_records.csv", "text/csv")

st.divider()
st.subheader("Forecast centre")
st.caption("Forecasts are calculated from the currently filtered rows and averaged by date. If multiple mandis or varieties are selected, this is an aggregate forecast—not a separate forecast for each mandi/variety.")
horizon = st.slider("Forecast horizon (days)", 1, 7, 3)
if price_col and df[price_col].notna().sum() >= 18:
    with st.spinner("Training price model and evaluating a time-ordered holdout…"):
        forecasts, mae, err = build_forecast(df, price_col, horizon)
    if err:
        st.info(err)
    else:
        st.success("Historical ML model trained. Test evaluation uses a chronological holdout; it is not a guarantee of future accuracy.")
        p1, p2 = st.columns(2)
        p1.metric("Holdout MAE", f"₹{mae:,.0f}/qtl")
        p2.metric("Forecast horizon", f"{horizon} day(s)")
        st.dataframe(forecasts, use_container_width=True, hide_index=True)
        st.plotly_chart(px.line(forecasts, x="Forecast Date", y=f"Predicted {price_col}", markers=True,
                                title="Indicative price forecast"), use_container_width=True)
        st.download_button("Download price forecast (CSV)", forecasts.to_csv(index=False).encode("utf-8-sig"),
                           "krbl_price_forecast.csv", "text/csv")
else:
    st.info("Price ML forecast requires at least 18 dated observations with numeric modal prices. Upload the historical workbook if the live API is unavailable.")

if "Arrival Quantity" in df.columns and df["Arrival Quantity"].notna().sum() >= 18:
    with st.spinner("Training arrival model…"):
        arr_forecast, arr_mae, arr_err = build_forecast(df, "Arrival Quantity", horizon)
    st.subheader("Arrival forecast")
    if arr_err:
        st.info(arr_err)
    else:
        st.metric("Arrival holdout MAE", f"{arr_mae:,.2f} (source units)")
        st.caption("Arrival units follow the uploaded source file; verify whether they are tonnes, quintals, or another unit.")
        st.dataframe(arr_forecast, use_container_width=True, hide_index=True)
        st.plotly_chart(px.line(arr_forecast, x="Forecast Date", y="Predicted Arrival Quantity", markers=True,
                                title="Indicative arrival forecast"), use_container_width=True)
        st.download_button("Download arrival forecast (CSV)", arr_forecast.to_csv(index=False).encode("utf-8-sig"),
                           "krbl_arrival_forecast.csv", "text/csv")
else:
    st.info("Arrival forecast needs at least 18 dated arrival-quantity observations. The current government price resource may not supply arrival quantity; use a workbook that includes a date and arrival quantity column.")

st.divider()
st.caption("Sources: Government of India OGD / AGMARKNET and uploaded historical workbook. Price observations are daily and may be delayed. Official source: https://www.data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi. Forecasts are estimates; do not interpret as guaranteed purchase prices.")

