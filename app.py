import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'accept-language': 'en-US,en;q=0.9',
    'referer': 'https://www.nseindia.com/'
}

def get_next_expiry():
    today = datetime.now()
    days_ahead = 3 - today.weekday()
    if days_ahead < 0: days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%d %b").upper()

def get_advanced_s_r_levels(df, spot_price, search_range=500):
    """±500 రేంజ్ లో సపోర్ట్ & రెసిస్టెన్స్ లెక్కించే అడ్వాన్స్‌డ్ ఫంక్షన్"""
    try:
        nearby = df[(df['STRIKE'] >= spot_price - search_range) & (df['STRIKE'] <= spot_price + search_range)]
        
        # Resistance
        calls = nearby[nearby['STRIKE'] > spot_price]
        res = int(calls.loc[calls['CALL_OI'].idxmax(), 'STRIKE']) if not calls.empty else int(spot_price + 100)
        
        # Support
        puts = nearby[nearby['STRIKE'] <= spot_price]
        sup = int(puts.loc[puts['PUT_OI'].idxmax(), 'STRIKE']) if not puts.empty else int(spot_price - 100)
        
        return sup, res
    except:
        return int(spot_price-100), int(spot_price+100)

def get_nse_data(spot_price):
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        response = session.get(url, headers=HEADERS, timeout=10)
        
        if response.status_code == 200:
            raw_data = response.json()
            records = raw_data['records']['data']
            df_list = [{'STRIKE': r['strikePrice'], 'CALL_OI': r.get('CE', {}).get('openInterest', 0), 'PUT_OI': r.get('PE', {}).get('openInterest', 0)} for r in records if 'CE' in r or 'PE' in r]
            df = pd.DataFrame(df_list)
            
            sup, res = get_advanced_s_r_levels(df, spot_price)
            pcr = round(raw_data['filtered']['PE']['totOI'] / raw_data['filtered']['CE']['totOI'], 2)
            return sup, res, pcr
        return None, None, 1.0
    except:
        return None, None, 1.0

@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        nifty = yf.Ticker("^NSEI")
        # 🔥 'progress' ఆర్గ్యుమెంట్ తొలగించబడింది ఎర్రర్ రాకుండా
        hist = nifty.history(interval="5m", period="1d")
        if hist.empty: return jsonify({"status": "error", "message": "No Market Data"})

        latest = hist.iloc[-1]
        ltp = round(float(latest['Close']), 2)
        support, resistance, pcr = get_nse_data(ltp)
        
        # AI Scoring
        bullish = 0; bearish = 0
        tp = (hist['High'] + hist['Low'] + hist['Close']) / 3
        vwap = (tp * hist['Volume']).cumsum() / hist['Volume'].cumsum()
        
        if ltp > vwap.iloc[-1]: bullish += 40
        else: bearish += 40
        
        if pcr >= 1.05: bullish += 30
        elif pcr <= 0.95: bearish += 30
        
        if latest['Close'] > latest['Open']: bullish += 30
        else: bearish += 30

        strike = round(ltp / 50) * 50
        op_type = "CE" if bullish > bearish else "PE"
        broker_symbol = f"NIFTY {get_next_expiry()} {strike} {op_type}"

        return jsonify({
            "status": "success", "ltp": f"{ltp:,.2f}",
            "change": round(ltp - hist['Close'].iloc[0], 2),
            "pct": round(((ltp - hist['Close'].iloc[0])/hist['Close'].iloc[0])*100, 2),
            "bullish": min(bullish, 100), "bearish": min(bearish, 100),
            "support": support, "resistance": resistance, "pcr": pcr,
            "broker_symbol": broker_symbol, "entry": 130, "t1": 150, "t2": 175, "sl": 115,
            "data_source": "AI Master Engine (NSE ±500)"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)