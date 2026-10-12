# KRBL Mandi AI — Government Paddy Price Dashboard

Streamlit dashboard for government-reported mandi prices from the data.gov.in AGMARKNET resource.

## Deploy on Streamlit Community Cloud
1. Open https://share.streamlit.io/ and sign in using the GitHub account that owns this repository.
2. Choose repository `Mgovind8294/KRBL-Mandi-AI`, branch `main`, and main file path `app.py`.
3. Deploy the app.
4. In the app's settings, open **Secrets** and add:
   ```toml
   DATA_GOV_IN_API_KEY = "YOUR_REAL_DATA_GOV_IN_API_KEY"
   ```
5. Save secrets and reboot/reload the app. The app should show “Government API connected” when the key and resource work.

## API
- Resource ID: `9ef84268-d588-465a-a308-a864a43d0070`
- Endpoint: `https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070`
- Dataset: https://www.data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi

Never commit an API key or private procurement workbook to this public repository. A public URL is only issued after the owner signs in and successfully deploys the app. Government data refresh cadence depends on the publisher; the app caches responses for 15 minutes. Forecasts are baseline estimates, not guaranteed prices or official government forecasts.
