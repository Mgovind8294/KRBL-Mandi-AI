import os
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
            except requests.exceptions.HTTPError as exc:
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", "unknown")
                body = (getattr(response, "text", "") or "").replace("\\n", " ").strip()[:140]
                errors.append(f"{state} {year}-{month:02d}: HTTP {status} {body}")
            except Exception as exc:
                errors.append(f"{state} {year}-{month:02d}: {type(exc).__name__}: {str(exc)[:100]}")
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
        "purchase qtl": "Purchase Quantity (Qtl)", "purchase quantity": "Purchase Quantity (Qtl)",
        "purchase quantity qtl": "Purchase Quantity (Qtl)", "arrival qtl": "Arrival Quantity",
        "arrival quantity qtl": "Arrival Quantity", "arrival quantity quintal": "Arrival Quantity",
        "avg price rs qtl": "Modal Price (₹/qtl)", "avg price rs/qtl": "Modal Price (₹/qtl)",
        "avg price rs qtl": "Modal Price (₹/qtl)", "avg price rs qtl.": "Modal Price (₹/qtl)",
        "avg price rs qtl": "Modal Price (₹/qtl)", "avg price rs/qtl.": "Modal Price (₹/qtl)",
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
    for col in ["Min Price (₹/qtl)", "Max Price (₹/qtl)", "Modal Price (₹/qtl)", "Arrival Quantity", "Purchase Quantity (Qtl)"]:
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
def fetch_data(api_key, limit=1000):
    """Fetch multiple pages from the official OGD resource, then retain the six configured states."""
    session = api_session()
    page_size = max(100, min(1000, int(limit)))
    params = {"api-key": api_key, "format": "json", "limit": page_size, "offset": 0}
    state_aliases = {
        "punjab": "Punjab", "haryana": "Haryana",
        "uttar pradesh": "Uttar Pradesh", "uttarakhand": "Uttarakhand",
        "rajasthan": "Rajasthan", "delhi": "Delhi",
        "nct of delhi": "Delhi", "national capital territory of delhi": "Delhi",
    }
    all_rows, total = [], None
    # OGD can report a large nationwide total. Cap requests for predictable app response time;
    # the sidebar will report whether the full reported result set was downloaded.
    max_pages = 30
    try:
        for page in range(max_pages):
            params["offset"] = page * page_size
            response = session.get(API_URL, params=params, timeout=(10, 35))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Unexpected OGD response format")
            if payload.get("error") or (payload.get("message") and not payload.get("records")):
                raise RuntimeError(str(payload.get("error") or payload.get("message"))[:200])
            rows = payload.get("records") or []
            if not rows:
                break
            all_rows.extend(rows)
            if total is None:
                try:
                    total = int(payload.get("total"))
                except (TypeError, ValueError):
                    total = None
            print(f"OGD page {page + 1}: {len(rows)} rows; downloaded {len(all_rows)}")
            if len(rows) < page_size or (total is not None and len(all_rows) >= total):
                break
        if not all_rows:
            raise RuntimeError("OGD returned zero records.")
        result = standardize_columns(pd.DataFrame(all_rows)).drop_duplicates().reset_index(drop=True)
        if "State" not in result.columns:
            raise RuntimeError("OGD response did not include a recognizable State column.")
        result["State"] = result["State"].astype(str).str.strip().replace(state_aliases)
        result = result[result["State"].astype(str).str.lower().isin(state_aliases)].copy()
        if result.empty:
            raise RuntimeError("OGD pages loaded, but none of the records matched the six configured states.")
        result.attrs["api_rows_fetched"] = len(result)
        result.attrs["api_total_records"] = total if total is not None else "not supplied"
        result.attrs["api_pages_fetched"] = page + 1
        result.attrs["api_rows_downloaded"] = len(all_rows)
        result.attrs["api_complete"] = (total is not None and len(all_rows) >= total)
        result.attrs["state_errors"] = []
        result.attrs["fetch_scope"] = "Official OGD pages filtered to Punjab, Haryana, Uttar Pradesh, Rajasthan, Uttarakhand and Delhi."
        return result
    except Exception as exc:
        detail = str(exc).replace(api_key, "[REDACTED_API_KEY]") if api_key else str(exc)
        raise RuntimeError(f"Official OGD request failed: {type(exc).__name__}: {detail[:300]}") from exc

def read_uploaded_file(upload):
    if upload.name.lower().endswith((".xlsx", ".xls")):
        # Prefer the workbook historical transaction sheet over the dashboard/control sheets.
        book = pd.ExcelFile(upload)
        sheet = "Raw_Data" if "Raw_Data" in book.sheet_names else book.sheet_names[0]
        return pd.read_excel(book, sheet_name=sheet)
    return pd.read_csv(upload)

def build_forecast(history, target_col, horizon):
    """Train a lag-feature model on actual dated observations; interpolate only short gaps."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error

    if "Arrival Date" not in history.columns or target_col not in history.columns:
        return None, None, f"Required columns are missing: Arrival Date and {target_col}."
    clean = history.dropna(subset=["Arrival Date", target_col]).copy()
    if clean.empty:
        return None, None, f"No valid dated numeric observations were found for {target_col}."
    clean["Arrival Date"] = pd.to_datetime(clean["Arrival Date"], errors="coerce").dt.normalize()
    clean[target_col] = pd.to_numeric(clean[target_col], errors="coerce")
    clean = clean.dropna(subset=["Arrival Date", target_col])
    daily = clean.groupby("Arrival Date")[target_col].mean().sort_index()
    if len(daily) < 12:
        return None, None, f"Only {len(daily)} distinct dates have valid {target_col} values. At least 12 distinct dates are required."
    # Fill only short gaps; do not create predictions from long missing periods.
    full_index = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    values = daily.reindex(full_index)
    values.index.name = "Arrival Date"
    values = values.interpolate(method="time", limit=3, limit_area="inside")
    values = values.dropna()
    if len(values) < 12:
        return None, None, f"After excluding long date gaps, only {len(values)} usable daily values remain for {target_col}."
    data = pd.DataFrame({"y": values})
    # Shorter lags make the model trainable with a practical amount of real history.
    for lag in [1, 2, 3]:
        data[f"lag_{lag}"] = data["y"].shift(lag)
    data["rolling_3"] = data["y"].shift(1).rolling(3).mean()
    data["day_of_year"] = data.index.dayofyear
    data["day_of_week"] = data.index.dayofweek
    data = data.dropna()
    if len(data) < 8:
        return None, None, f"Only {len(data)} training rows remain for {target_col}; add more dated history."
    features = [col for col in data.columns if col != "y"]
    split = max(6, int(len(data) * 0.8))
    if len(data) - split < 2:
        split = len(data) - 2
    if split < 4 or len(data) - split < 2:
        return None, None, f"Not enough rows for a reliable chronological evaluation of {target_col}."
    train, test = data.iloc[:split], data.iloc[split:]
    model = RandomForestRegressor(n_estimators=200, min_samples_leaf=2, random_state=42, n_jobs=-1)
    model.fit(train[features], train["y"])
    mae = mean_absolute_error(test["y"], model.predict(test[features]))
    full_model = RandomForestRegressor(n_estimators=200, min_samples_leaf=2, random_state=42, n_jobs=-1)
    full_model.fit(data[features], data["y"])
    work = values.copy()
    forecast_rows = []
    last_date = values.index.max()
    for step in range(1, horizon + 1):
        next_date = last_date + pd.Timedelta(days=1)
        def lag_val(n):
            ix = next_date - pd.Timedelta(days=n)
            if ix in work.index and pd.notna(work.loc[ix]):
                return float(work.loc[ix])
            prior = work.loc[work.index < next_date].dropna()
            return float(prior.iloc[-min(n, len(prior))])
        row = {
            "lag_1": lag_val(1), "lag_2": lag_val(2), "lag_3": lag_val(3),
            "rolling_3": float(work.loc[work.index < next_date].dropna().tail(3).mean()),
            "day_of_year": next_date.dayofyear, "day_of_week": next_date.dayofweek,
        }
        pred = max(0.0, float(full_model.predict(pd.DataFrame([row])[features])[0]))
        work.loc[next_date] = pred
        forecast_rows.append({"Forecast Date": next_date, f"Predicted {target_col}": round(pred, 2)})
        last_date = next_date
    return pd.DataFrame(forecast_rows), float(mae), None

st.title("🌾 KRBL Mandi AI — India Paddy Price Intelligence")
st.caption("Government OGD price feed + scheduled snapshot fallback + authorized KRBL purchase data. Only verified records are shown; forecasts are estimates.")

with st.sidebar:
    st.header("Data configuration")
    api_key = st.text_input("data.gov.in API key", value=get_api_key(), type="password",
                            help="Keep the key private. For deployment use Streamlit App settings → Secrets.")
    limit = st.select_slider("Government API page size", options=[250, 500, 1000], value=1000,
                             help="Downloads multiple official OGD pages (up to 30) and then filters to Punjab, Haryana, Uttar Pradesh, Rajasthan, Uttarakhand and Delhi. Full coverage depends on API availability and the reported total.")
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
    st.write("The direct AGMARKNET report endpoint is CAPTCHA-protected and cannot be used for unattended loading. Use the official data.gov.in OGD connector instead.")
    if st.button("Test AGMARKNET connection (CAPTCHA protected)", key="load_agmarknet", use_container_width=True):
        fetch_agmarknet_data.clear()
        try:
            with st.spinner("Connecting to official AGMARKNET and loading recent monthly reports…"):
                agmarknet_df = fetch_agmarknet_data()
            st.session_state["agmarknet_live_rows"] = agmarknet_df
            st.session_state["agmarknet_live_errors"] = agmarknet_df.attrs.get("state_errors", [])
            st.success(f"AGMARKNET returned {len(agmarknet_df):,} rows.")
        except Exception as exc:
            st.session_state["agmarknet_live_error"] = f"{type(exc).__name__}: {str(exc)[:350]}"
            st.error("Direct AGMARKNET report access is blocked by CAPTCHA. Use the official OGD connector instead. Details: " + st.session_state["agmarknet_live_error"])
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
        with st.spinner("Connecting to the official Government of India mandi dataset…"):
            live_df = fetch_data(api_key, limit)
        api_errors = live_df.attrs.get("state_errors", [])
        latest_api_date = live_df["Arrival Date"].max() if "Arrival Date" in live_df.columns else pd.NaT
        date_note = latest_api_date.strftime("%d %b %Y") if pd.notna(latest_api_date) else "date unavailable"
        st.success(f"Government API responded — {len(live_df):,} official records returned. Latest observation in fetched rows: {date_note}.")
        if api_errors:
            st.warning("Some state requests returned no rows or failed: " + "; ".join(api_errors))
        st.caption(f"Official OGD pagination: {live_df.attrs.get('api_pages_fetched', 'unknown')} page(s), {live_df.attrs.get('api_rows_downloaded', len(live_df)):,} rows downloaded; reported nationwide total: {live_df.attrs.get('api_total_records', 'not supplied')}. Full nationwide coverage fetched: {'Yes' if live_df.attrs.get('api_complete') else 'No / not confirmed'}. Results are filtered to the six configured states; prices are daily published observations, not tick-by-tick live data.")
    except requests.exceptions.Timeout:
        st.warning("Government API timed out. Historical Excel will remain available; no sample/live-looking data is substituted.")
    except requests.exceptions.ConnectionError:
        st.warning("Could not connect to api.data.gov.in from this hosting environment. Historical Excel remains available.")
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        st.warning(f"Government API returned HTTP {status or 'error'}. Check the API key, resource access and portal status.")
    except Exception as exc:
        diagnostic = str(exc).replace(api_key, "[REDACTED_API_KEY]") if api_key else str(exc)
        if isinstance(exc, RuntimeError) and "ConnectTimeout" in diagnostic or "HTTPSConnectionPool" in diagnostic:
            st.warning("Government API connection timed out from the hosting environment. This is a network/API reachability issue, not proof that the API key is invalid. Historical workbook mode remains available.")
            with st.expander("Connection diagnostics"):
                st.code(f"{type(exc).__name__}: {diagnostic[:700]}")
        else:
            st.warning(f"Government data could not be loaded. Exact diagnostic: {type(exc).__name__}: {diagnostic[:700]}. Historical workbook mode remains available.")
elif not api_key:
    st.info("OGD API key not configured. AGMARKNET can be tested separately in the section above; no key is needed for that connector.")
else:
    st.info("OGD is the recommended official data source. Click 'Load / refresh OGD data' in the sidebar to fetch current published records.")

st.subheader("Master Excel — daily data update")
st.write(
    "Upload the current master workbook containing all historical rows. Optionally upload a separate daily-update file to append new records. "
    "The app preserves the master history, removes only exact duplicate rows, and lets you download the updated master. "
    "For safety, download the updated file after every append and use it as the master on the next visit."
)
master_upload = st.file_uploader(
    "1) Upload master history Excel/CSV (all previous records)",
    type=["xlsx", "xls", "csv"],
    key="master_history_upload",
)
daily_upload = st.file_uploader(
    "2) Optional: upload today's new purchase/arrival Excel/CSV",
    type=["xlsx", "xls", "csv"],
    key="daily_history_upload",
)
master_df = None
if master_upload is not None:
    try:
        master_df = standardize_columns(read_uploaded_file(master_upload))
        st.success(f"Master history loaded: {len(master_df):,} rows.")
    except Exception as exc:
        st.error(f"Could not read master file ({type(exc).__name__}). Please check the Excel/CSV format.")
if daily_upload is not None:
    try:
        daily_df = standardize_columns(read_uploaded_file(daily_upload))
        if master_df is not None:
            combined_master = pd.concat([master_df, daily_df], ignore_index=True, sort=False)
        else:
            combined_master = daily_df.copy()
        before_dedup = len(combined_master)
        # Remove only exact duplicate rows. Do not deduplicate by date/mandi/variety because
        # multiple genuine purchase lots can share those values.
        combined_master = combined_master.drop_duplicates().reset_index(drop=True)
        removed = before_dedup - len(combined_master)
        history_df = combined_master.copy()
        history_df["Data Source"] = "Master Excel"
        st.success(
            f"Daily update appended: {len(daily_df):,} rows. "
            f"Master now contains {len(combined_master):,} unique rows"
            + (f" ({removed:,} exact duplicate rows skipped)." if removed else ".")
        )
        export_master = combined_master.copy()
        export_bytes = None
        try:
            from io import BytesIO
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                export_master.to_excel(writer, sheet_name="Raw_Data", index=False)
            export_bytes = buffer.getvalue()
            st.download_button(
                "Download updated master Excel",
                data=export_bytes,
                file_name="KRBL_Mandi_AI_Master_History.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except Exception as export_exc:
            st.warning(f"Excel export unavailable ({type(export_exc).__name__}); downloading master as CSV instead.")
            st.download_button(
                "Download updated master CSV",
                data=export_master.to_csv(index=False).encode("utf-8-sig"),
                file_name="KRBL_Mandi_AI_Master_History.csv",
                mime="text/csv",
                use_container_width=True,
            )
    except Exception as exc:
        st.error(f"Could not append daily update ({type(exc).__name__}). Please check the file format.")
elif master_df is not None:
    history_df = master_df.copy()
    history_df["Data Source"] = "Master Excel"
    st.info("Master history is loaded. Upload today's file above to append new rows, or continue forecasting from the master as-is.")

# If the live request from Streamlit Cloud fails, use the latest verified snapshot
# written by the scheduled GitHub Actions workflow. Never fabricate records.
if live_df is None and os.path.exists("data/ogd_latest.csv"):
    try:
        cached_df = standardize_columns(pd.read_csv("data/ogd_latest.csv"))
        if not cached_df.empty and "Arrival Date" in cached_df.columns:
            cached_df.attrs["cached"] = True
            live_df = cached_df
            st.info(
                "Live government API is unreachable from this session. Showing the latest "
                "GitHub Actions data snapshot instead; check the observation date before use."
            )
    except Exception as cache_exc:
        st.warning(f"Could not read the saved government-data snapshot: {type(cache_exc).__name__}: {str(cache_exc)[:180]}")

if live_df is not None:
    live_df["Data Source"] = "Government OGD/AGMARKNET (scheduled snapshot)" if live_df.attrs.get("cached") else "Government OGD/AGMARKNET"
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
    # Explicitly synthetic seed rows so the dashboard layout can be tested/submitted
    # while the government API is unreachable. These are NOT real market observations.
    demo_rows = []
    demo_markets = [
        ("Punjab", "Sangrur", "Dhuri Mandi", 4520, 540),
        ("Punjab", "Patiala", "Nabha Mandi", 4480, 470),
        ("Haryana", "Sirsa", "Sirsa Mandi", 4550, 560),
        ("Haryana", "Karnal", "Karnal Mandi", 4590, 510),
    ]
    demo_dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=21, freq="D")
    for day_idx, day in enumerate(demo_dates):
        for market_idx, (state, district, market, base_price, base_arrival) in enumerate(demo_markets):
            price = base_price + ((day_idx * 17 + market_idx * 13) % 95) - 35
            arrival = base_arrival + ((day_idx * 29 + market_idx * 41) % 180) - 70
            demo_rows.append({
                "State": state, "District": district, "Market": market,
                "Commodity": "Paddy (Basmati) — DEMO",
                "Variety": ["Pusa 1121", "Pusa 1509", "Pusa 1718", "Pusa 1885"][market_idx],
                "Arrival Date": day,
                "Min Price (₹/qtl)": price - 80,
                "Max Price (₹/qtl)": price + 90,
                "Modal Price (₹/qtl)": price,
                "Arrival Quantity": arrival,
                "Data Source": "DEMO / TESTING ONLY — NOT OFFICIAL DATA",
            })
    df = pd.DataFrame(demo_rows)
    data_source = "DEMO / TESTING ONLY — synthetic placeholder data"
    st.warning(
        "🧪 TESTING MODE: Government API/snapshot and uploaded history are unavailable. "
        "The records below are synthetic placeholder data used only to test the dashboard structure. "
        "They are NOT actual mandi prices, arrivals, or KRBL purchase records. Do not use for procurement or reporting."
    )

st.caption(f"Current data source: **{data_source}**")
if "State" in df.columns:
    st.caption("State coverage in loaded records: " + ", ".join(sorted(df["State"].dropna().astype(str).unique())))
if "Market" in df.columns and "District" in df.columns:
    st.caption(f"Loaded geography: {df['State'].nunique() if 'State' in df.columns else 0} state(s), {df['District'].nunique()} district(s), {df['Market'].nunique()} market/mandi name(s), {df['Variety'].nunique() if 'Variety' in df.columns else 0} variety label(s). Counts reflect returned records, not an authoritative master directory.")
if "Arrival Date" not in df.columns:
    st.error("Date column was not recognized. Rename the date column to 'Arrival Date' or 'Date' and upload again.")
    st.dataframe(df.head(10), use_container_width=True)
    st.stop()

if "Commodity" in df.columns:
    commodities = sorted(df["Commodity"].dropna().astype(str).unique())
    defaults = [x for x in commodities if "paddy" in x.lower() or "dhan" in x.lower()]
    selected = st.multiselect("Commodity / Variety coverage", commodities, default=defaults or commodities)
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
st.subheader("Trend overview")
chart_left, chart_right = st.columns(2)
with chart_left:
    if price_col and df[price_col].notna().any():
        price_trend = df.dropna(subset=["Arrival Date", price_col]).groupby("Arrival Date", as_index=False)[price_col].mean()
        st.plotly_chart(px.line(price_trend, x="Arrival Date", y=price_col, markers=True, title="Daily modal price trend"), use_container_width=True)
    else:
        st.info("Price trend will appear when modal price values are available.")
with chart_right:
    if "Arrival Quantity" in df.columns and df["Arrival Quantity"].notna().any():
        arrival_trend = df.dropna(subset=["Arrival Date", "Arrival Quantity"]).groupby("Arrival Date", as_index=False)["Arrival Quantity"].sum()
        st.plotly_chart(px.bar(arrival_trend, x="Arrival Date", y="Arrival Quantity", title="Daily arrival quantity (source units)"), use_container_width=True)
    else:
        st.info("Arrival trend will appear when arrival quantity values are available.")

st.subheader("Mandi records")
st.dataframe(df, use_container_width=True, hide_index=True)
st.download_button("Download filtered records (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                   "krbl_mandi_filtered_records.csv", "text/csv")

st.divider()
st.subheader("Forecast centre")
with st.expander("Forecast data check", expanded=False):
    st.write("Forecast input columns detected in the currently filtered data:")
    st.write({
        "Rows after filters": int(len(df)),
        "Distinct valid dates": int(df.loc[df["Arrival Date"].notna(), "Arrival Date"].dt.normalize().nunique()) if "Arrival Date" in df.columns else 0,
        "Price column": price_col if price_col else "Not detected",
        "Valid numeric price rows": int(df[price_col].notna().sum()) if price_col else 0,
        "Arrival quantity column": "Arrival Quantity" if "Arrival Quantity" in df.columns else "Not detected",
        "Valid numeric arrival rows": int(df["Arrival Quantity"].notna().sum()) if "Arrival Quantity" in df.columns else 0,
        "Columns in uploaded data": list(df.columns),
    })
st.caption("Forecasts are calculated from the currently filtered rows and averaged by date. If multiple mandis or varieties are selected, this is an aggregate forecast—not a separate forecast for each mandi/variety.")
horizon = st.slider("Forecast horizon (days)", 1, 7, 3)
if price_col and df[price_col].notna().sum() >= 12:
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
    st.info("Price forecast needs at least 12 distinct dates with numeric modal prices. If this message appears, check that your Excel has a Date/Arrival Date column and Avg_Price_Rs_Qtl/Modal Price values.")

if "Arrival Quantity" in df.columns and df["Arrival Quantity"].notna().sum() >= 12:
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
    st.info("Arrival forecast needs at least 12 distinct dates with numeric arrival quantities. Check that your Excel has Date/Arrival Date and Arrival_Qtl/Arrival Quantity columns.")

st.divider()
st.caption("Sources: Government of India OGD / AGMARKNET and uploaded historical workbook. Price observations are daily and may be delayed. Official source: https://www.data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi. Forecasts are estimates; do not interpret as guaranteed purchase prices.")

