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
        "arrival date": "Arrival Date", "date": "Arrival Date", "price date": "Arrival Date",
        "min_price": "Min Price (₹/qtl)", "min price": "Min Price (₹/qtl)",
        "max_price": "Max Price (₹/qtl)", "max price": "Max Price (₹/qtl)",
        "modal_price": "Modal Price (₹/qtl)", "modal price": "Modal Price (₹/qtl)",
        "modal price (₹/qtl)": "Modal Price (₹/qtl)", "price": "Modal Price (₹/qtl)",
        "arrival": "Arrival Quantity", "arrivals": "Arrival Quantity",
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
    """Fetch paginated OGD records, retrying without sorting if the API rejects sort syntax."""
    page_size = 100
    session = api_session()
    all_rows = []
    total_available = None
    sort_supported = True
    offset = 0
    max_records = max(1, int(limit))

    while offset < max_records:
        this_limit = min(page_size, max_records - offset)
        base_params = {
            "api-key": api_key,
            "format": "json",
            "limit": this_limit,
            "offset": offset,
        }
        attempts = []
        if sort_supported:
            attempts.append({**base_params, "sort[arrival_date]": "desc"})
        attempts.append(base_params)

        response = None
        last_error = None
        for params in attempts:
            try:
                response = session.get(API_URL, params=params, timeout=(8, 20))
                if response.status_code >= 400 and "sort[arrival_date]" in params:
                    # Some OGD resources reject the sort parameter. Retry the same page without it.
                    sort_supported = False
                    continue
                response.raise_for_status()
                break
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if params == attempts[-1]:
                    raise
        if response is None:
            if last_error:
                raise last_error
            raise ValueError("No response received from the government API.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("Government API returned a non-JSON response.") from exc

        if not isinstance(payload, dict):
            raise ValueError("Unexpected government API response format.")
        if payload.get("error") or payload.get("message") and not payload.get("records"):
            message = str(payload.get("error") or payload.get("message"))
            raise ValueError(f"Government API response: {message[:240]}")

        if total_available is None:
            try:
                total_available = int(payload.get("total", 0))
            except (TypeError, ValueError):
                total_available = None

        rows = payload.get("records") or []
        if not isinstance(rows, list):
            raise ValueError("Government API 'records' field was not a list.")
        if not rows:
            break

        all_rows.extend(rows)
        offset += len(rows)
        if len(rows) < this_limit:
            break

    if not all_rows:
        raise ValueError("Government API returned zero records. Check API access or retry later.")

    result = standardize_columns(pd.DataFrame(all_rows))
    result.attrs["api_total_available"] = total_available
    result.attrs["api_rows_fetched"] = len(all_rows)
    result.attrs["api_sort_supported"] = sort_supported
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
    if st.button("Refresh government data", use_container_width=True):
        fetch_data.clear()
        st.rerun()
    st.divider()
    st.caption("Government source: OGD / AGMARKNET")
    st.markdown("[Open official mandi dataset](https://www.data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi)")

df = None
data_source = None
if api_key:
    try:
        with st.spinner("Connecting to Government of India mandi data…"):
            df = fetch_data(api_key, limit)
        data_source = "Live government API"
        latest_api_date = df["Arrival Date"].max() if "Arrival Date" in df.columns else pd.NaT
        date_note = latest_api_date.strftime("%d %b %Y") if pd.notna(latest_api_date) else "date unavailable"
        st.success(f"Government API connected — {len(df):,} records fetched. Latest date in fetched records: {date_note}.")
        if not df.attrs.get("api_sort_supported", True):
            st.warning("The API did not accept the date-sort parameter. Records were fetched by API pagination order, so the sample may not contain the newest records.")
        total_available = df.attrs.get("api_total_available")
        if total_available:
            st.caption(f"API reports approximately {total_available:,} total records for this resource; this dashboard fetched {len(df):,}.")
        st.caption("This is the latest date returned in the fetched sample, not a guarantee that every mandi has reported today.")
    except requests.exceptions.Timeout:
        st.warning("Government API timed out. This can happen during portal maintenance or network restrictions. Use the historical workbook option below; no sample data will be substituted.")
    except requests.exceptions.ConnectionError:
        st.warning("The hosting service could not connect to api.data.gov.in. This is a network/service issue unless a separate API response confirms a key problem. Use historical data below.")
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        st.warning(f"Government API returned HTTP {status or 'error'}. Check that the API key is valid, the resource is accessible, and the portal is available. The API key is not shown here.")
    except Exception as exc:
        st.warning(f"Government data could not be loaded ({type(exc).__name__}). Sensitive request details are hidden.")
else:
    st.info("Government API key is not available. You can still use your historical Excel/CSV to run forecasts.")

st.subheader("Historical workbook / offline fallback")
st.write("Upload your existing Paddy Price Prediction Excel or a CSV. The original file is read in memory and is not changed or added to the public repository. Live government data is attempted first whenever an API key is available.")
uploaded = st.file_uploader("Upload historical mandi data (.xlsx, .xls, .csv)", type=["xlsx", "xls", "csv"])
if uploaded is not None:
    try:
        uploaded_df = standardize_columns(read_uploaded_file(uploaded))
        if df is None or st.checkbox("Use uploaded workbook instead of government API data", value=True):
            df = uploaded_df
            data_source = "Uploaded historical workbook"
            st.success(f"Workbook loaded: {len(df):,} rows. Please check column mapping below.")
    except Exception as exc:
        st.error(f"Could not read the uploaded file ({type(exc).__name__}). Check that it is a valid Excel/CSV file.")

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
for col in ["State", "District", "Market", "Variety"]:
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
st.caption("Source: Government of India OGD / AGMARKNET when connected; otherwise the uploaded workbook. Forecasts are estimates. Check source dates, units, and holdout error before business use.")

