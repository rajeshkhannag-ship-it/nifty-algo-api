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

def get_nse_data(spot_price):
    """NSE నుండి ±500 రేంజ్ లో సపోర్ట్ & రెసిస్టెన్స్ లెవెల్స్ లెక్కించడం"""
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        response = session.get(url, headers=HEADERS, timeout=10)
        
        if response.status_code == 200:
            raw_data = response.json()
            records = raw_data['records']['data']
            
            df_list = []
            for r in records:
                if 'CE' in r and 'PE' in r:
                    df_list.append({
                        'STRIKE': r['strikePrice'],
                        'CALL_OI': r['CE']['openInterest'],
                        'PUT_OI': r['PE']['openInterest']
                    })
            df = pd.DataFrame(df_list)

            # --- ±500 S&R Logic ---
            search_range = 500
            nearby = df[(df['STRIKE'] >= spot_price - search_range) & (df['STRIKE'] <= spot_price + search_range)]
            
            # Resistance (Max Call OI above Spot within 500 pts)
            calls = nearby[nearby['STRIKE'] > spot_price]
            res = int(calls.loc[calls['CALL_OI'].idxmax(), 'STRIKE']) if not calls.empty else int(spot_price + 100)
            
            # Support (Max Put OI below Spot within 500 pts)
            puts = nearby[nearby['STRIKE'] <= spot_price]
            sup = int(puts.loc[puts['PUT_OI'].idxmax(), 'STRIKE']) if not puts.empty else int(spot_price - 100)
            
            pcr = round(raw_data['filtered']['PE']['totOI'] / raw_data['filtered']['CE']['totOI'], 2)
            return sup, res, pcr
        return None, None, 1.0
    except:
        return None, None, 1.0

@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        nifty = yf.Ticker("^NSEI")
        hist = nifty.history(interval="5m", period="1d", progress=False)
        if hist.empty: return jsonify({"status": "error", "message": "No Market Data"})

        latest = hist.iloc[-1]
        ltp = round(float(latest['Close']), 2)
        
        # NSE ±500 S&R Levels
        support, resistance, pcr = get_nse_data(ltp)
        
        # AI Scoring Logic (Total 100 Points)
        bullish = 0; bearish = 0
        
        # 1. VWAP Cross-check (40 pts)
        tp = (hist['High'] + hist['Low'] + hist['Close']) / 3
        vwap = (tp * hist['Volume']).cumsum() / hist['Volume'].cumsum()
        if ltp > vwap.iloc[-1]: bullish += 40
        else: bearish += 40
        
        # 2. RSI Check (20 pts)
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        curr_rsi = round(100 - (100 / (1 + rs.iloc[-1])), 2)
        if curr_rsi > 55: bullish += 20
        elif curr_rsi < 45: bearish += 20
        
        # 3. PCR Sentiment (20 pts)
        if pcr >= 1.05: bullish += 20
        elif pcr <= 0.95: bearish += 20
        
        # 4. Volume Spike (20 pts)
        avg_vol = hist['Volume'].rolling(20).mean().iloc[-1]
        if latest['Volume'] > 1.5 * avg_vol:
            if latest['Close'] > latest['Open']: bullish += 20
            else: bearish += 20

        # Broker Symbol Format
        strike = round(ltp / 50) * 50
        op_type = "CE" if bullish > bearish else "PE"
        broker_symbol = f"NIFTY {get_next_expiry()} {strike} {op_type}"

        return jsonify({
            "status": "success",
            "ltp": f"{ltp:,.2f}",
            "change": round(ltp - hist['Close'].iloc[0], 2),
            "pct": round(((ltp - hist['Close'].iloc[0])/hist['Close'].iloc[0])*100, 2),
            "bullish": min(bullish, 100), "bearish": min(bearish, 100),
            "support": support if support else "N/A",
            "resistance": resistance if resistance else "N/A",
            "pcr": pcr, "broker_symbol": broker_symbol,
            "entry": 130, "t1": 150, "t2": 175, "sl": 115,
            "data_source": "AI Master Engine (NSE ±500)"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)