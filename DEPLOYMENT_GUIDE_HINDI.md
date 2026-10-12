# हिंदी में Live API और Hosting चालू करने के निर्देश

## A. API key
1. https://data.gov.in/ पर अपने खाते से sign in करें और API key लें।
2. API key को किसी chat या GitHub file में न डालें।
3. Dashboard deploy होने के बाद Streamlit app → Manage app / Settings → Secrets में यह जोड़ें:
   ```toml
   DATA_GOV_IN_API_KEY = "अपनी_असली_API_KEY_यहाँ"
   ```
4. Save करें और app reboot/reload करें।
5. स्क्रीन पर “Government API connected” दिखे और records आएँ, तभी live API verification सफल माना जाएगा।

## B. Public URL
1. https://share.streamlit.io/ खोलें और उसी GitHub account से sign in करें।
2. “Create app” / “Deploy an app” चुनें।
3. Repository: `Mgovind8294/KRBL-Mandi-AI`
4. Branch: `main`
5. Main file path: `app.py`
6. Deploy दबाएँ। Build सफल होने पर Streamlit सार्वजनिक `*.streamlit.app` URL दिखाएगा।
7. URL खोलकर API connection और data filters जाँचें।

## C. सुरक्षा और सीमाएँ
- इस repository को public रखते हुए API key, `.streamlit/secrets.toml`, या निजी Excel procurement workbook कभी commit न करें।
- निजी Excel को सार्वजनिक repository में न रखें। Dashboard में आवश्यक हो तो UI के माध्यम से अलग से upload करें।
- सरकारी feed की update timing data.gov.in/AGMARKNET पर निर्भर है। 15-minute app cache का अर्थ यह नहीं कि सरकार का data हर 15 मिनट update होता है।
- Forecast indicative baseline estimate है, validated procurement recommendation या official government prediction नहीं।
- इस environment से आपकी ओर से API key जारी करना या Streamlit पर login/consent देकर app publish करना संभव नहीं है। अंतिम public URL केवल successful deployment के बाद मिलेगा।
