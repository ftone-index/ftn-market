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
    r = requests.get(url, timeout=timeout)
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

# ---------- MARKET EXPECTATION ----------
def compute_market_ftn():
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
    scores = []
    for src in all_sources:
        try:
            soup = fetch_soup(src['url'])
            text = extract_text(soup)
            if text:
                # We'll let FTN-Index do the AI scoring, just return the sources
                pass
        except Exception as e:
            logging.error(f"Error processing extra source {src['url']}: {e}")
    return jsonify({
        "sources": all_sources,
        "scores": []   # FTN-Index will score these itself
    })

@app.route('/health')
def health():
    return "OK"

@app.route('/')
def home():
    return "FTN Market Service is live. Use /api/market_ftn or /api/extra_scores"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
