# Finalog
Presenting... Finalog, the financials logger - a streamlined pipeline for transcribing transactions directly to your spreadsheet!

The backbone of the app runs in Python using OpenCV + mss for screen capture and image segmentation, PyQt6 for GUI elements, and Gemini for transaction processing. It functions primarily based on macOS's _iPhone Mirroring_ capabilities, allowing users to initiate scans of their phone screens. Since it is screen capture, Gemini will attempt to parse out any transactions in the captured image.

## Quickstart

Install the app via pip!
```bash
pip install finalog
```

The initial setup will run on start. Please ensure you have the following:
- Gemini API key
- Google Sheets ID
- Google Sheets JSON key credentials file

See more info below if you do not have the Google Sheets API info (it is optional)

## Configure Google Sheets

The Google Sheets integration works with this set procedure. Follow all steps, it seems complex but is not trust 👍

1. Enable the API
   - Go to the Google Cloud Console and **create a new project**. 
   - Navigate to **APIs & Services** > **Library**, then search for "**Google Sheets API**". 
   - Select the API and **click Enable**. 
2. Configure a Service Account
   - Click the back arrows back to the **API & Services** homepage
   - Navigate to the **Credentials** tab, and click **Create Credentials** > **Service account** in the top-center of screen
   - Fill out the name, ID, and description for the account
   - Go to the **Keys Tab** - click the Keys Tab at the top of the page
   - Add a new key: click the **Add Key** dropdown and select **Create new key**
   - Choose JSON: **Select JSON** as the key type and click **Create**
   - Download and Save: The JSON file will automatically download to your computer. Store it securely, as **!! this is the only copy you will ever get !!**
3. Configure YOUR spreadsheet
   - Copy the Service Account email to your clipboard
   - Share your spreadsheet to this email as an **Editor**
   - Keep your saved JSON key credentials in a known location and ensure you know the path to the JSON credentials file 
   
>Run config to setup API keys and other settings!!
```bash
finalog config
```