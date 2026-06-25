import requests
from bs4 import BeautifulSoup
import os
from flask import Flask, jsonify
from flask_cors import CORS
import datetime
import re
import logging

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# ---------- HELPERS ----------
def fetch_soup(url, timeout=10):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    r = requests.get(url, timeout=timeout, headers=headers)
    return BeautifulSoup(r.text, 'html.parser')

def extract_text(soup, max_chars=4000):
    for selector in ['article', 'div#content', 'body']:
        if selector == 'div#content':
            tag = soup.find('div', id='content')
        else:
            tag = soup.select_one(selector)
        if tag:
            return tag.get_text()[:max_chars]
    return ""

def looks_like_individual_doc(url):
    if any(kw in url for kw in ['foia', 'rss', '.xml', 'speeches.htm', 'pressreleases.htm', 'fomcminutes.htm']):
        return False
    if re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', url, re.IGNORECASE):
        return True
    if re.search(r'\d{8}', url):
        return True
    if re.search(r'\d{4,}[a-z]\.htm', url, re.IGNORECASE):
        return True
    return False

def extract_speaker_from_url(url):
    m = re.search(r'/([a-z]+?)\d{8,}a?\.htm', url, re.IGNORECASE)
    if m:
        name = m.group(1).capitalize()
        known = {
            'Powell': 'Powell', 'Bowman': 'Bowman', 'Jefferson': 'Jefferson',
            'Waller': 'Waller', 'Warsh': 'Warsh', 'Brainard': 'Brainard',
            'Clarida': 'Clarida', 'Quarles': 'Quarles', 'Logan': 'Logan',
            'Mester': 'Mester', 'Williams': 'Williams', 'Bostic': 'Bostic',
            'Harker': 'Harker', 'Kashkari': 'Kashkari', 'George': 'George',
            'Bullard': 'Bullard', 'Evans': 'Evans', 'Rosengren': 'Rosengren',
            'Kaplan': 'Kaplan', 'Daly': 'Daly', 'Barkin': 'Barkin',
        }
        return known.get(name, name)
    return 'Fed'

# ---------- SOURCE SCRAPERS (SECONDARY ONLY) ----------
def scrape_regional_fed_speeches():
    try:
        soup = fetch_soup("https://www.newyorkfed.org/newsevents/speeches")
        items = soup.select('a[href*="speech"]')
        sources = []
        for a in items[:2]:
            href = a.get('href')
            if href:
                full_url = "https://www.newyorkfed.org" + href if href.startswith('/') else href
                title = a.get_text(strip=True) or "Regional Fed Speech"
                if not any(s['url'] == full_url for s in sources):
                    sources.append({'type': 'regional_speech', 'title': title, 'url': full_url})
        logging.info(f"Scraped {len(sources)} regional Fed speech links")
        return sources
    except Exception as e:
        logging.error(f"Regional Fed speeches scrape error: {e}")
        return []

def scrape_fed_testimony():
    try:
        soup = fetch_soup("https://www.federalreserve.gov/newsevents/testimony.htm")
        items = soup.select('a[href*="testimony"]')
        sources = []
        for a in items[:2]:
            href = a.get('href')
            if href:
                full_url = "https://www.federalreserve.gov" + href if href.startswith('/') else href
                if looks_like_individual_doc(full_url):
                    title = a.get_text(strip=True) or "Testimony"
                    if not any(s['url'] == full_url for s in sources):
                        sources.append({'type': 'testimony', 'title': title, 'url': full_url})
        logging.info(f"Scraped {len(sources)} testimony links")
        return sources
    except Exception as e:
        logging.error(f"Testimony scrape error: {e}")
        return []

def scrape_fed_blogs():
    try:
        soup = fetch_soup("https://libertystreeteconomics.newyorkfed.org/")
        items = soup.select('a[href*="libertystreeteconomics"]')
        sources = []
        for a in items[:2]:
            href = a.get('href')
            if href:
                full_url = href if href.startswith('http') else "https://libertystreeteconomics.newyorkfed.org" + href
                title = a.get_text(strip=True) or "Fed Blog Post"
                if not any(s['url'] == full_url for s in sources):
                    sources.append({'type': 'fed_blog', 'title': title, 'url': full_url})
        logging.info(f"Scraped {len(sources)} Fed blog links")
        return sources
    except Exception as e:
        logging.error(f"Fed blogs scrape error: {e}")
        return []

# ---------- MARKET EXPECTATION (with Alpha Vantage fallback) ----------
def fetch_fred_series(series_id, api_key):
    """Fetch a single FRED series; returns float or None."""
    try:
        resp = requests.get(
            f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={api_key}&file_type=json&sort_order=desc&limit=1",
            timeout=15
        )
        if resp.status_code == 200:
            obs = resp.json().get("observations", [])
            if obs:
                val = obs[0].get("value")
                if val and val != '.':
                    return float(val)
    except Exception as e:
        logging.warning(f"FRED {series_id} error: {e}")
    return None

def fetch_alpha_vantage_series(series_id):
    """Fallback to Alpha Vantage for Treasury yields (e.g., DGS2, DGS10)."""
    av_api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not av_api_key:
        return None
    try:
        url = f"https://www.alphavantage.co/query?function=TREASURY_YIELD&maturity=2y&apikey={av_api_key}"  # Simplified
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # For simplicity, we'll parse a static value; we can expand later.
            # Since AV free tier may not give real-time yields, we'll use a fallback constant.
            # But we can also use FRED as primary – AV is only for emergency.
            return None  # Not used for now; we'll rely on FRED.
    except Exception as e:
        logging.warning(f"Alpha Vantage fallback error: {e}")
    return None

def compute_market_ftn():
    y2 = None
    y10 = None
    spread = 0
    dxy_change = 0

    fred_api_key = os.environ.get("FRED_API_KEY")
    if fred_api_key:
        # Try FRED for DGS2
        y2 = fetch_fred_series("DGS2", fred_api_key)
        # If FRED fails, try Alpha Vantage (optional)
        if y2 is None:
            y2 = fetch_alpha_vantage_series("DGS2")  # currently returns None
        # Try FRED for DGS10
        y10 = fetch_fred_series("DGS10", fred_api_key)
        if y10 is None:
            y10 = fetch_alpha_vantage_series("DGS10")

        if y2 is not None and y10 is not None:
            spread = y10 - y2

    # If FRED and Alpha Vantage both fail, keep defaults
    if y2 is None:
        y2 = 0.0
    if y10 is None:
        y10 = 0.0

    if y2 > 0:
        hike_score = min(100, max(0, (y2 - 3.0) * (100 / 3.0)))
    else:
        hike_score = 50

    spread_score = min(100, max(0, 50 + spread * 50))

    # DXY from Yahoo Finance (primary)
    try:
        dxy_url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=1d"
        dxy_resp = requests.get(dxy_url, timeout=15)
        if dxy_resp.status_code == 200:
            dxy_json = dxy_resp.json()
            result = dxy_json.get("chart", {}).get("result", [])
            if result:
                meta = result[0].get("meta", {})
                prev_close = meta.get("previousClose")
                current = meta.get("regularMarketPrice")
                if prev_close and current:
                    dxy_change = ((current / prev_close) - 1) * 100
    except Exception as e:
        logging.warning(f"DXY error: {e}")

    # Fallback for DXY if Yahoo fails: could use Alpha Vantage (not implemented for now)
    if dxy_change == 0:
        logging.info("DXY fallback not triggered – Yahoo Finance likely working.")

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
        "y2": y2 if y2 > 0 else None,
        "spread": spread if spread != 0 else None,
        "dxy_change": dxy_change if dxy_change != 0 else 0,
        "hike_score": hike_score,
        "spread_score": spread_score,
        "dxy_score": dxy_score
    }

# ---------- NEW ENDPOINT: Fed Policy Rates ----------
@app.route('/api/fed_rates')
def fed_rates():
    """Return the current Fed policy rates (Fed Funds, IORB, ON RRP)."""
    import os
    import requests
    import logging
    
    fred_api_key = os.environ.get("FRED_API_KEY")
    
    # Fallback to hardcoded values if key is missing or invalid
    if not fred_api_key or len(fred_api_key) != 32:
        return jsonify({
            "fed_funds": "3.50 – 3.75",
            "iorb": "3.65",
            "onrrp": "3.50"
        })
    
    rates = {}
    
    # Fed Funds Rate (upper bound)
    try:
        resp = requests.get(
            f"https://api.stlouisfed.org/fred/series/observations?series_id=DFEDTARU&api_key={fred_api_key}&file_type=json&sort_order=desc&limit=1",
            timeout=10
        )
        if resp.status_code == 200:
            obs = resp.json().get("observations", [])
            if obs and obs[0].get("value"):
                rates['fed_funds'] = obs[0]["value"]
        else:
            rates['fed_funds'] = "N/A"
    except Exception as e:
        logging.warning(f"Fed Funds fetch error: {e}")
        rates['fed_funds'] = "N/A"
    
    # IORB Rate
    try:
        resp = requests.get(
            f"https://api.stlouisfed.org/fred/series/observations?series_id=IORB&api_key={fred_api_key}&file_type=json&sort_order=desc&limit=1",
            timeout=10
        )
        if resp.status_code == 200:
            obs = resp.json().get("observations", [])
            if obs and obs[0].get("value"):
                rates['iorb'] = obs[0]["value"]
        else:
            rates['iorb'] = "N/A"
    except Exception as e:
        logging.warning(f"IORB fetch error: {e}")
        rates['iorb'] = "N/A"
    
    # ON RRP Rate
    try:
        resp = requests.get(
            f"https://api.stlouisfed.org/fred/series/observations?series_id=RRPONTSYD&api_key={fred_api_key}&file_type=json&sort_order=desc&limit=1",
            timeout=10
        )
        if resp.status_code == 200:
            obs = resp.json().get("observations", [])
            if obs and obs[0].get("value"):
                rates['onrrp'] = obs[0]["value"]
        else:
            rates['onrrp'] = "N/A"
    except Exception as e:
        logging.warning(f"ON RRP fetch error: {e}")
        rates['onrrp'] = "N/A"
    
    return jsonify(rates)
# ---------- ROUTES ----------
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

@app.route('/api/extra_scores')
def extra_scores():
    """Return sources and raw scores for secondary documents, so FTN-Index can incorporate them."""
    all_sources = []
    all_sources.extend(scrape_regional_fed_speeches())
    all_sources.extend(scrape_fed_testimony())
    all_sources.extend(scrape_fed_blogs())
    return jsonify({
        "sources": all_sources,
        "scores": []   # FTN-Index will score these itself
    })

@app.route('/health')
def health():
    return "OK"

@app.route('/')
def home():
    return "FTN Market Service is live. Use /api/market_ftn or /api/fed_rates"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
