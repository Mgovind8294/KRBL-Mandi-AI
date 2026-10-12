import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="KRBL Mandi AI | Paddy Intelligence", page_icon="🌾", layout="wide")
ENAM_DASHBOARD = "https://enam.gov.in/dashboard"
DELHI_MARKET_BOARD = "https://delagrimarket.nic.in/"

st.markdown("""
<style>
.block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
[data-testid="stMetric"] {background: #f7f9fc; border: 1px solid #e5eaf2; padding: 14px; border-radius: 12px;}
</style>
""", unsafe_allow_html=True)

def standardize_columns(frame):
    df = frame.copy()
    aliases = {
        "state": "State", "state name": "State", "state/ut": "State", "district": "District", "district name": "District",
        "market": "Market", "mandi": "Market", "market name": "Market", "apmc": "Market", "apmc name": "Market", "market name/apmc": "Market", "commodity": "Commodity", "commodity name": "Commodity",
        "crop": "Commodity", "variety": "Variety", "grade": "Grade", "arrival_date": "Arrival Date",
        "arrival date": "Arrival Date", "date": "Arrival Date", "price date": "Arrival Date", "reported date": "Arrival Date",
        "min_price": "Min Price (₹/qtl)", "min price": "Min Price (₹/qtl)", "min price (rs/qtl)": "Min Price (₹/qtl)", "min price (per quintal)": "Min Price (₹/qtl)",
        "max_price": "Max Price (₹/qtl)", "max price": "Max Price (₹/qtl)", "max price (rs/qtl)": "Max Price (₹/qtl)", "max price (per quintal)": "Max Price (₹/qtl)",
        "modal_price": "Modal Price (₹/qtl)", "modal price": "Modal Price (₹/qtl)", "last traded price": "Modal Price (₹/qtl)", "last traded price (per quintal)": "Modal Price (₹/qtl)", "price (rs/qtl)": "Modal Price (₹/qtl)",
        "modal price (₹/qtl)": "Modal Price (₹/qtl)", "modal price (rs/qtl)": "Modal Price (₹/qtl)", "modal price (rs/qtl.)": "Modal Price (₹/qtl)", "price": "Modal Price (₹/qtl)",
        "arrival": "Arrival Quantity", "arrivals": "Arrival Quantity", "commodity arrivals": "Arrival Quantity", "commodity arrivals (quintal)": "Arrival Quantity", "arrivals (quintal)": "Arrival Quantity", "arrivals (tonnes)": "Arrival Quantity", "arrivals (qtl)": "Arrival Quantity", "arrival (qtl)": "Arrival Quantity", "arrivals (in tonnes)": "Arrival Quantity",
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

def read_uploaded_file(upload):
    if upload.name.lower().endswith((".xlsx", ".xls")):
        # Prefer the workbook historical transaction sheet over the dashboard/control sheets.
        book = pd.ExcelFile(upload)
        sheet = "Raw_Data" if "Raw_Data" in book.sheet_names else book.sheet_names[0]
        return pd.read_excel(book, sheet_name=sheet)
    return pd.read_csv(upload)


def fetch_enam_public_table():
    """Attempt to read a public, server-rendered e-NAM trade table without credentials.
    Returns (dataframe, message). It never fabricates data or bypasses login/access controls.
    """
    from io import StringIO
    import requests

    # Try both official e-NAM hostnames. Keep TLS certificate verification enabled:
    # disabling verification would expose the app to man-in-the-middle attacks.
    urls = [
        "https://www.enam.gov.in/web/dashboard/trade-data",
        "https://enam.gov.in/web/dashboard/trade-data",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KRBLMandiAI/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        response = None
        failures = []
        for url in urls:
            try:
                candidate = requests.get(url, headers=headers, timeout=(8, 20))
                candidate.raise_for_status()
                response = candidate
                break
            except requests.exceptions.SSLError:
                failures.append(f"{url}: SSL certificate/handshake error")
            except requests.RequestException as exc:
                failures.append(f"{url}: {type(exc).__name__}")
        if response is None:
            if failures and all("SSL" in failure for failure in failures):
                return None, "Both official e-NAM hostnames failed TLS/SSL verification from the app server. Secure automatic fetching is unavailable in this runtime; certificate verification was not disabled. Use the official export until the hosting/network certificate chain is fixed."
            return None, "Could not connect to the official e-NAM trade page. " + "; ".join(failures)
        # Try multiple installed HTML parsers. Streamlit Cloud may still be restarting
        # after a requirements change, so the error message should identify the cause.
        parser_errors = []
        tables = None
        for parser in ("lxml", "html5lib"):
            try:
                tables = pd.read_html(StringIO(response.text), flavor=parser)
                break
            except ImportError:
                parser_errors.append(parser)
        if tables is None:
            return None, "HTML table parsers are not installed in the running app (tried lxml and html5lib). Wait for Streamlit Cloud to finish installing requirements and reboot the app."
        if not tables:
            return None, "The e-NAM page responded, but it did not expose a readable HTML table. It may load data through browser-side scripts."
        frames = []
        for table in tables:
            if not table.empty:
                frames.append(table)
        if not frames:
            return None, "The e-NAM page did not return any trade rows."
        raw = pd.concat(frames, ignore_index=True, sort=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [
                " ".join(str(part).strip() for part in col if str(part).strip() and not str(part).lower().startswith("unnamed"))
                for col in raw.columns
            ]
        else:
            raw.columns = [str(col).strip() for col in raw.columns]
        # e-NAM's visible table can group Min/Modal/Max Price beneath a parent heading.
        normalized = {}
        for col in raw.columns:
            key = " ".join(str(col).lower().replace("_", " ").replace("-", " ").split())
            if "modal price" in key:
                normalized[col] = "Modal Price (₹/qtl)"
            elif "min price" in key:
                normalized[col] = "Min Price (₹/qtl)"
            elif "max price" in key:
                normalized[col] = "Max Price (₹/qtl)"
            elif "commodity arrivals" in key or "arrival quantity" in key:
                normalized[col] = "Arrival Quantity"
            elif key == "date" or key.endswith(" date"):
                normalized[col] = "Arrival Date"
            elif key in ("state", "state name"):
                normalized[col] = "State"
            elif "apmc" in key:
                normalized[col] = "Market"
            elif "commodity" in key:
                normalized[col] = "Commodity"
        raw.rename(columns=normalized, inplace=True)
        data = standardize_columns(raw)
        date_ok = "Arrival Date" in data.columns and data["Arrival Date"].notna().any()
        price_ok = "Modal Price (₹/qtl)" in data.columns and data["Modal Price (₹/qtl)"].notna().any()
        if not date_ok or not price_ok:
            return None, "The public page returned HTML, but no dated price records could be mapped safely. No prices were loaded."
        data = data.drop_duplicates().reset_index(drop=True)
        data["Data Source"] = "e-NAM public page (automatically fetched)"
        return data, f"Read {len(data):,} rows from the public e-NAM page."
    except requests.RequestException as exc:
        if isinstance(exc, requests.exceptions.SSLError):
            return None, "e-NAM TLS/SSL verification failed. The app cannot establish a secure connection; certificate verification remains enabled."
        return None, f"e-NAM public page request failed ({type(exc).__name__}). The page may be temporarily unavailable or block automated requests."
    except ImportError as exc:
        return None, f"HTML table parser dependency is missing ({type(exc).__name__}). Check that Streamlit Cloud installed lxml/html5lib from requirements.txt and completed its reboot."
    except ValueError:
        return None, "e-NAM did not expose a parseable public HTML table. A documented data endpoint or official export is needed for reliable automation."
    except Exception as exc:
        return None, f"Could not safely parse the public e-NAM page ({type(exc).__name__})."


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
st.caption("Mandi price intelligence, arrival trends, and ML forecasts. Only verified uploaded records are displayed; no sample prices are generated.")

with st.sidebar:
    st.header("Data source")
    st.markdown("[e-NAM — Trade Details](https://www.enam.gov.in/web/dashboard/trade-data)")
    st.markdown("[e-NAM — Historical Trade Data](https://www.enam.gov.in/web/dashboard/Historical)")
    st.markdown("[e-NAM — Live Price Information](https://www.enam.gov.in/web/dashboard/live_price)")
    st.markdown("[Delhi Agricultural Marketing Board](https://delagrimarket.nic.in/)")
    st.caption("The app currently imports official reports uploaded by the user; an automated e-NAM API connection has not been verified. No synthetic mandi prices are shown.")

st.subheader("Official e-NAM data")
st.write("First try the public e-NAM trade page automatically. If the portal blocks automated access or only renders data in the browser, the app will explain that and keep the verified-report upload option. No prices are invented.")
if st.button("Fetch e-NAM public data now", type="primary", use_container_width=False):
    with st.spinner("Connecting to the official e-NAM trade page…"):
        live_enam, live_message = fetch_enam_public_table()
    st.session_state["enam_public_data"] = live_enam
    st.session_state["enam_public_message"] = live_message
    st.session_state["enam_public_checked"] = True
if st.session_state.get("enam_public_checked"):
    if st.session_state.get("enam_public_data") is not None:
        st.success(st.session_state.get("enam_public_message", "e-NAM data fetched."))
    else:
        st.warning(st.session_state.get("enam_public_message", "No verified public data was returned."))
enam_upload = st.file_uploader(
    "Upload official e-NAM report (CSV / Excel)",
    type=["csv", "xlsx", "xls"],
    key="enam_report_upload",
)
enam_df = st.session_state.get("enam_public_data")
if enam_upload is not None:
    try:
        enam_df = standardize_columns(read_uploaded_file(enam_upload))
        if "Arrival Date" not in enam_df.columns:
            st.error("e-NAM report needs a recognizable date column. Rename it to Date or Arrival Date and upload again.")
            enam_df = None
        else:
            enam_df = enam_df.drop_duplicates().reset_index(drop=True)
            enam_df["Data Source"] = "e-NAM official report (uploaded)"
            st.success(f"e-NAM report imported: {len(enam_df):,} rows. This is uploaded report data, not an automatic live API connection.")
            st.caption("Source: e-NAM official dashboard. Confirm the report date and units before using prices for procurement.")
    except Exception as exc:
        st.error(f"Could not read the e-NAM report ({type(exc).__name__}): {str(exc)[:180]}")
        enam_df = None

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
history_df = None
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

df = None
data_source = None
if enam_df is not None:
    df = enam_df.copy()
    data_source = str(df["Data Source"].iloc[0]) if "Data Source" in df.columns and len(df) else "e-NAM public data"
elif history_df is not None:
    df = history_df.copy()
    data_source = "Historical Excel (user-uploaded; not government-live)"

if df is None:
    st.markdown("### Government data connection")
    st.error("No e-NAM report or historical workbook is loaded. No prices are being displayed.")
    st.write("Download a report from the official e-NAM dashboard and upload it above, or upload a verified historical workbook. The app does not generate sample mandi prices.")
    st.markdown("- [Official e-NAM dashboard](https://enam.gov.in/dashboard)")
    st.markdown("- [Delhi Agricultural Marketing Board](https://delagrimarket.nic.in/)")
    st.stop()

st.caption(f"Current data source: **{data_source}**")
if data_source == "Historical Excel (user-uploaded; not government-live)":
    st.warning("Historical Excel is user-provided data, not a verified live government feed. Check its source and observation date before relying on it.")
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
    elif data_source == "e-NAM official report (uploaded)":
        st.info("No commodity selected, so all fetched commodities are shown.")
for col in ["State", "District", "Market", "Variety", "Data Source"]:
    if col in df.columns:
        opts = sorted(df[col].dropna().astype(str).unique())
        selected = st.multiselect(col, opts, key=f"filter_{col}")
        if selected:
            df = df[df[col].astype(str).isin(selected)]

# Drop invalid/placeholder prices; preserve rows with missing prices for arrival analysis.
price_col = "Modal Price (₹/qtl)" if "Modal Price (₹/qtl)" in df.columns else None
if price_col:
    df[price_col] = pd.to_numeric(df[price_col], errors="coerce")
    invalid_price_count = int((df[price_col].notna() & (df[price_col] <= 0)).sum())
    df.loc[df[price_col] <= 0, price_col] = pd.NA
    if invalid_price_count:
        st.warning(f"{invalid_price_count:,} zero/negative price value(s) were excluded from price calculations.")
    df = df.drop_duplicates().reset_index(drop=True)

if df["Arrival Date"].notna().any():
    latest_observation = df["Arrival Date"].max().normalize()
    st.caption("Latest observation date in selected records: " + latest_observation.strftime("%d %b %Y"))
    latest_day_df = df[df["Arrival Date"].dt.normalize() == latest_observation].copy()
else:
    latest_observation = None
    latest_day_df = df.iloc[0:0].copy()

if price_col and latest_day_df[price_col].notna().any():
    latest_prices = latest_day_df[price_col].dropna()
    a, b, c, d = st.columns(4)
    a.metric("Latest-day average modal price", f"₹{latest_prices.mean():,.0f}/qtl")
    b.metric("Latest-day lowest modal price", f"₹{latest_prices.min():,.0f}/qtl")
    c.metric("Latest-day highest modal price", f"₹{latest_prices.max():,.0f}/qtl")
    d.metric("Latest-day price records", f"{len(latest_prices):,}")
    st.caption("Price KPIs use only records from the latest date after filters. They are not a claim of today's price unless the report itself is dated today.")
else:
    st.warning("No valid positive modal-price values are available for the latest observation date in the selected data.")

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
st.caption("Sources: uploaded official e-NAM report or user-provided historical workbook. Uploaded report date and units must be verified. Forecasts are estimates, not guaranteed purchase prices.")

