import requests
import os
from flask import Flask, jsonify
from flask_cors import CORS
import datetime
import logging

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# ---------- MARKET EXPECTATION ENDPOINT ----------
def compute_market_ftn():
    """
    Returns a 0‑100 score representing what the market is pricing in
    about the Fed's next moves, derived from:
      - 2‑year Treasury yield (rate hike/cut expectations)
      - 2y/10y Treasury spread (yield curve)
      - US Dollar Index (DXY) intraday change
    """
    y2 = None
    y10 = None
    spread = 0
    dxy_change = 0

    fred_api_key = os.environ.get("FRED_API_KEY")
    if fred_api_key:
        try:
            resp_2y = requests.get(
                f"https://api.stlouisfed.org/fred/series/observations?series_id=DGS2&api_key={fred_api_key}&file_type=json&sort_order=desc&limit=1",
                timeout=15
            )
            if resp_2y.status_code == 200:
                obs = resp_2y.json().get("observations", [])
                if obs:
                    y2 = float(obs[0].get("value", 0) or 0)
                del resp_2y
        except Exception as e:
            logging.warning(f"FRED 2y error: {e}")

        try:
            resp_10y = requests.get(
                f"https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key={fred_api_key}&file_type=json&sort_order=desc&limit=1",
                timeout=15
            )
            if resp_10y.status_code == 200:
                obs = resp_10y.json().get("observations", [])
                if obs:
                    y10 = float(obs[0].get("value", 0) or 0)
                del resp_10y
        except Exception as e:
            logging.warning(f"FRED 10y error: {e}")

        if y2 is not None and y10 is not None:
            spread = y10 - y2

    if y2 is not None and y2 > 0:
        hike_score = min(100, max(0, (y2 - 3.0) * (100 / 3.0)))
    else:
        hike_score = 50

    spread_score = min(100, max(0, 50 + spread * 50))

    try:
        dxy_url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=1d"
        dxy_resp = requests.get(dxy_url, timeout=15)
        if dxy_resp.status_code == 200:
            dxy_json = dxy_resp.json()
            result = dxy_json.get("chart", {}).get("result", [])
            if result:
                meta = result[0].get("meta", {})
                indicators = result[0].get("indicators", {}).get("quote", [{}])
                if indicators and indicators[0]:
                    opens = indicators[0].get("open", [])
                    closes = indicators[0].get("close", [])
                    if opens and closes and opens[0] and closes[0]:
                        open_val = float(opens[0])
                        close_val = float(closes[0])
                        if open_val and close_val:
                            dxy_change = ((close_val / open_val) - 1) * 100
                if dxy_change == 0:
                    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
                    current = meta.get("regularMarketPrice")
                    if prev_close and current:
                        dxy_change = ((current / prev_close) - 1) * 100
            del dxy_json
        del dxy_resp
    except Exception as e:
        logging.warning(f"DXY error: {e}")

    dxy_score = min(100, max(0, 50 + dxy_change * 100))
    market_ftn = round((hike_score + spread_score + dxy_score) / 3, 1)

    if market_ftn <= 20:
        label = "Extremely Dovish"
    elif market_ftn <= 40:
        label = "Dovish"
    elif market_ftn <= 60:
        label = "Neutral"
    elif market_ftn <= 80:
        label = "Hawkish"
    else:
        label = "Extremely Hawkish"

    return market_ftn, label, {
        "y2": y2,
        "spread": spread,
        "dxy_change": dxy_change
    }

@app.route('/api/market_ftn')
def market_ftn():
    score, label, components = compute_market_ftn()
    if score is None:
        return jsonify({"error": "Market data unavailable"}), 500
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    return jsonify({
        "index": "Market Expectation (FTN‑M)",
        "score": score,
        "label": label,
        "components": components,
        "timestamp": ts
    })

@app.route('/health')
def health():
    return "OK"

@app.route('/')
def home():
    return "FTN Market Service is live. Use /api/market_ftn"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
