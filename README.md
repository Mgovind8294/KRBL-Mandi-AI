# KRBL Mandi AI — Six-State Paddy Price Dashboard

Streamlit dashboard combining uploaded historical Excel data with daily government-reported mandi prices from the data.gov.in / AGMARKNET resource.

## States covered
Punjab, Haryana, Uttar Pradesh, Rajasthan, Uttarakhand and Delhi.

## Streamlit Community Cloud deployment
1. Open https://share.streamlit.io/ and sign in with the GitHub account that owns this repository.
2. Select repository `Mgovind8294/KRBL-Mandi-AI`, branch `main`, entrypoint `app.py`.
3. In the app settings, open **Secrets** and add this valid TOML line using your own API key:
   ```toml
   DATA_GOV_IN_API_KEY = "YOUR_REAL_DATA_GOV_IN_API_KEY"
   ```
4. Save and reboot/redeploy the app. Never commit the key to GitHub or share it in screenshots.
5. Open the deployed dashboard and upload `Paddy_Complete_Paddy_Price_Prediction_FIXED(4).xlsx` using the Historical workbook uploader. The app combines the workbook rows with government observations in memory; it does not modify the workbook.
6. Confirm the status message reports government API records and check the latest observation date. If the API times out, workbook forecasts remain available after upload.

## Data source and limitations
- Official resource: https://www.data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi
- Resource ID: `9ef84268-d588-465a-a308-a864a43d0070`
- Endpoint: `https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070`
- The source contains daily reported minimum, maximum and modal prices. It is not a tick-by-tick real-time quote feed.
- The government price resource may not include arrival quantity. Arrival forecasting therefore requires dated arrival quantities in the uploaded workbook.
- Forecasts are estimates, not official government predictions or guaranteed prices.
- The dashboard does not replace failed API responses with fabricated data.

## Excel-first daily updates and forecasting

The dashboard supports a manual, real-data workflow when the government API is unavailable:
1. Upload the current master Excel/CSV containing all historical records.
2. Optionally upload a separate daily-update Excel/CSV. Its rows are appended to the master in memory.
3. The app removes only exact duplicate rows; it does not deduplicate by date/mandi/variety, because multiple real purchase lots can share those fields.
4. Download `KRBL_Mandi_AI_Master_History.xlsx` after every append and keep it as the master file for the next update. Streamlit Cloud's local session filesystem is not a reliable permanent store, so the downloaded master is the durable copy.
5. The model uses dated historical price records for price forecasts and dated arrival quantities for arrival forecasts. Forecasts are shown only when enough valid observations are available. It never fills gaps with invented/sample records.

Recommended column names (common alternatives are also recognized): `Date` or `Arrival Date`, `State`, `District`, `Mandi` or `Market`, `Variety`, `Arrival_Qtl` or `Arrival Quantity`, `Purchase_Qtl` or `Purchase Quantity`, and `Avg_Price_Rs_Qtl` or `Modal Price (₹/qtl)`. Use consistent units and date formats.

## Scheduled government-data snapshot (fallback for Streamlit Cloud timeouts)

A GitHub Actions workflow now runs daily and can also be started manually. It fetches the same official OGD resource outside Streamlit Cloud and commits the latest successful snapshot to `data/ogd_latest.csv`. When the dashboard's direct API request times out, the app can display this saved snapshot instead. The snapshot is not guaranteed to refresh until the workflow succeeds.

One-time setup:
1. Open the repository on GitHub → **Settings → Secrets and variables → Actions → New repository secret**.
2. Add a secret named `DATA_GOV_IN_API_KEY` and paste your newly generated data.gov.in API key as its value. This is separate from Streamlit Community Cloud Secrets.
3. Open **Actions → Refresh official mandi data → Run workflow** to test it manually.
4. Open the workflow run and confirm that the fetch step succeeds and the commit step updates `data/ogd_latest.csv`.
5. After a successful run, refresh the Streamlit dashboard. Check the latest observation date and source label; cached records are a snapshot, not live data.

If the workflow fails with connection timeout too, the hosting environment is not the only likely cause; check the official API's availability and response. Do not paste API keys into issues, chat, logs, or screenshots.
