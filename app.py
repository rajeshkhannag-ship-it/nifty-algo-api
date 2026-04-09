import requests
import urllib3
import time
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS

# SSL Warnings Fix
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'accept-language': 'en-US,en;q=0.9',
}

def nearest_50(spot):
    return int(round(spot / 50) * 50)

def generate_strikes(spot):
    atm = nearest_50(spot)
    # Expiry aggressive mode
    if datetime.now().weekday() >= 2:  # Wed/Thu (2, 3)
        step = 4  # ±200
    else:
        step = 6  # ±300
    strikes = [atm + i * 50 for i in range(-step, step + 1)]
    return atm, strikes

def get_current_expiry(data):
    expiries = sorted(list(set([x["expiryDate"] for x in data["records"]["data"]])))
    return expiries[0]

def format_strike(symbol, expiry, strike, opt_type):
    exp = expiry.replace("-", " ").upper()
    return f"{symbol} {exp} {strike} {opt_type}"

def get_google_finance_live():
    try:
        url = "https://www.google.com/finance/quote/NIFTY_50:INDEXNSE"
        html = requests.get(url, headers=HEADERS, timeout=3).text
        marker = 'class="YMlKec fxKbKc">'
        if marker in html:
            return float(html.split(marker)[1].split('<')[0].replace(',', '').replace('₹', ''))
    except: return None

@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        spot = get_google_finance_live()
        support, resistance = "N/A", "N/A"
        signal_data = None

        session = requests.Session()
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=2)
        nse_res = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", headers=HEADERS, timeout=3)
        
        if nse_res.status_code == 200:
            data = nse_res.json()
            if not spot: spot = float(data['records']['underlyingValue'])
            
            expiry = get_current_expiry(data)
            atm, active_strikes = generate_strikes(spot)
            
            records = data['records']['data']
            tot_ce_oi = data['filtered']['CE']['totOI']
            tot_pe_oi = data['filtered']['PE']['totOI']
            pcr = round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi > 0 else 1.0

            # Approximation of VWAP using Option Chain bounds
            vwap = spot # Fallback
            
            ce_list, pe_list = [], []
            oi_change = {}
            row_data = {}

            for r in records:
                if r['expiryDate'] == expiry:
                    s = r['strikePrice']
                    ce_oi = r.get('CE', {}).get('openInterest', 0)
                    pe_oi = r.get('PE', {}).get('openInterest', 0)
                    ce_chg = r.get('CE', {}).get('changeinOpenInterest', 0)
                    pe_chg = r.get('PE', {}).get('changeinOpenInterest', 0)
                    ce_ltp = r.get('CE', {}).get('lastPrice', 0)
                    pe_ltp = r.get('PE', {}).get('lastPrice', 0)
                    
                    if s in active_strikes:
                        if s > spot: ce_list.append((s, ce_oi))
                        if s <= spot: pe_list.append((s, pe_oi))
                        
                        oi_change[s] = {"CE": ce_chg, "PE": pe_chg}
                        row_data[s] = {"CE_LTP": ce_ltp, "PE_LTP": pe_ltp}

            if ce_list: resistance = sorted(ce_list, key=lambda x: x[1], reverse=True)[0][0]
            if pe_list: support = sorted(pe_list, key=lambda x: x[1], reverse=True)[0][0]

            # 🚀 SIGNAL GENERATION LOGIC
            strike = atm
            if strike in oi_change and strike in row_data:
                ce_change = oi_change[strike]["CE"]
                pe_change = oi_change[strike]["PE"]

                # 🟢 CALL (Short Covering / Long Buildup)
                if pcr > 1.05 and spot > vwap and ce_change < 0:
                    entry = row_data[strike]["CE_LTP"]
                    name = format_strike("NIFTY", expiry, strike, "CE")
                    signal_data = {
                        "type": "BUY CALL 🟢",
                        "name": name,
                        "entry": entry,
                        "t1": round(entry * 1.2, 2),
                        "t2": round(entry * 1.4, 2),
                        "sl": round(entry * 0.85, 2)
                    }

                # 🔴 PUT (Long Unwinding / Short Buildup)
                elif pcr < 0.95 and spot < vwap and pe_change < 0:
                    entry = row_data[strike]["PE_LTP"]
                    name = format_strike("NIFTY", expiry, strike, "PE")
                    signal_data = {
                        "type": "BUY PUT 🔴",
                        "name": name,
                        "entry": entry,
                        "t1": round(entry * 1.2, 2),
                        "t2": round(entry * 1.4, 2),
                        "sl": round(entry * 0.85, 2)
                    }

        if not spot: spot = 0.0

        return jsonify({
            "status": "success", 
            "ltp": f"{spot:,.2f}",
            "support": support, 
            "resistance": resistance,
            "pcr": pcr,
            "signal": signal_data
        })
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
