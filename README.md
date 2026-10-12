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
