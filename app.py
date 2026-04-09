import pandas as pd
import requests
import urllib3
import time
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
from nselib import capital_market

# SSL Warnings Fix
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'accept-language': 'en-US,en;q=0.9',
}

cache = {
    "indices": {},
    "fii_dii": None,
    "vol_gainers": [],
    "last_update": 0
}

# --- YOUR ADVANCED STRIKE & EXPIRY LOGIC ---
def nearest_50(spot):
    return int(round(spot / 50) * 50)

def generate_strikes(spot: float):
    atm = nearest_50(spot)
    # Expiry aggressive mode
    if datetime.now().weekday() >= 2:  # Wed/Thu
        step = 4  # ±200
    else:
        step = 6  # ±300
    strikes = [atm + i * 50 for i in range(-step, step + 1)]
    return atm, strikes

def get_current_expiry(data):
    expiries = sorted(list(set([x["expiryDate"] for x in data["records"]["data"]])))
    return expiries[0]   # nearest expiry

def format_strike(symbol, expiry, strike, opt_type):
    # convert date format (30-Mar-2026 → 30 MAR 2026)
    exp = expiry.replace("-", " ").upper()
    return f"{symbol} {exp} {strike} {opt_type}"
# -------------------------------------------

def update_intelligence_cache():
    current_time = time.time()
    if current_time - cache['last_update'] > 300: # 5 mins
        try:
            idx_df = capital_market.market_watch_all_indices()
            nifty = idx_df[idx_df['index'] == 'NIFTY 50'].iloc[0]
            bank = idx_df[idx_df['index'] == 'NIFTY BANK'].iloc[0]
            it = idx_df[idx_df['index'] == 'NIFTY IT'].iloc[0]
            cache['indices'] = {
                'nifty': f"₹{nifty['last']} ({nifty['percent_change']}%)",
                'bank': f"₹{bank['last']} ({bank['percent_change']}%)",
                'it': f"₹{it['last']} ({it['percent_change']}%)"
            }
        except: pass

        try:
            fii_df = capital_market.fii_dii_trading_activity()
            last_row = fii_df.iloc[-1]
            net_val = float(str(last_row.get('net_value', '0')).replace(',', ''))
            cache['fii_dii'] = {
                'date': last_row.get('date', ''),
                'net': net_val,
                'status': 'Bullish 🟢' if net_val > 0 else 'Bearish 🔴'
            }
        except: pass

        try:
            vol_df = capital_market.volume_gainers()
            cache['vol_gainers'] = vol_df[['symbol', 'last_price']].head(4).to_dict(orient='records')
        except: pass
        
        cache['last_update'] = current_time

def get_google_finance_live():
    try:
        url = "https://www.google.com/finance/quote/NIFTY_50:INDEXNSE"
        html = requests.get(url, headers=HEADERS, timeout=3).text
        marker = 'class="YMlKec fxKbKc">'
        if marker in html:
            price_str = html.split(marker)[1].split('<')[0].replace(',', '').replace('₹', '')
            return float(price_str)
        return None
    except: return None

@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        update_intelligence_cache()
        final_ltp = get_google_finance_live()
        if not final_ltp: final_ltp = 0.0

        support, resistance = "N/A", "N/A"
        signal = "NONE"
        alert_msg = ""
        trade_symbol = ""

        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=HEADERS, timeout=2)
            nse_res = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", headers=HEADERS, timeout=2)
            
            if nse_res.status_code == 200:
                raw = nse_res.json()
                nse_ltp = float(raw['records']['underlyingValue'])
                if final_ltp == 0.0: final_ltp = nse_ltp
                
                # Using your logic here
                expiry_date = get_current_expiry(raw)
                atm, valid_strikes = generate_strikes(final_ltp)
                
                records = raw['records']['data']
                exp_records = [r for r in records if r.get('expiryDate') == expiry_date]
                
                df_nse = pd.DataFrame([{'STRIKE': r['strikePrice'], 'CALL_OI': r.get('CE', {}).get('openInterest', 0), 'PUT_OI': r.get('PE', {}).get('openInterest', 0)} for r in exp_records if 'CE' in r or 'PE' in r])
                
                nearby = df_nse[df_nse['STRIKE'].isin(valid_strikes)]
                
                calls = nearby[nearby['STRIKE'] > final_ltp]
                if not calls.empty: resistance = int(calls.loc[calls['CALL_OI'].idxmax(), 'STRIKE'])
                
                puts = nearby[nearby['STRIKE'] <= final_ltp]
                if not puts.empty: support = int(puts.loc[puts['PUT_OI'].idxmax(), 'STRIKE'])

                # ALERT LOGIC (15 point buffer)
                if type(support) == int and abs(final_ltp - support) <= 15:
                    signal = "BUY_CE"
                    trade_symbol = format_strike("NIFTY", expiry_date, support, "CE")
                    alert_msg = f"PRICE AT SUPPORT! Watch {trade_symbol}"
                
                elif type(resistance) == int and abs(final_ltp - resistance) <= 15:
                    signal = "BUY_PE"
                    trade_symbol = format_strike("NIFTY", expiry_date, resistance, "PE")
                    alert_msg = f"PRICE AT RESISTANCE! Watch {trade_symbol}"

        except Exception as e: print("NSE Error:", e)

        return jsonify({
            "status": "success", 
            "ltp": f"{final_ltp:,.2f}",
            "support": support, 
            "resistance": resistance,
            "signal": signal,
            "alert_msg": alert_msg,
            "intelligence": cache
        })
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
