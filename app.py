import yfinance as yf
import pandas as pd
import numpy as np
import requests
import xgboost as xgb
import urllib3
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

# SSL Warnings Fix
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36',
    'accept-language': 'en-US,en;q=0.9',
    'accept-encoding': 'gzip, deflate, br'
}

def get_current_expiry():
    now = datetime.now()
    days_ahead = 3 - now.weekday() # Thursday Expiry
    if days_ahead < 0 or (days_ahead == 0 and (now.hour > 15 or (now.hour == 15 and now.minute >= 30))):
        days_ahead += 7 
    return (now + timedelta(days=days_ahead)).strftime("%d %b").upper()

@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        # Step 1: Base AI History Data (Yahoo Finance)
        hist = yf.Ticker("^NSEI").history(interval="5m", period="5d")
        if hist.empty: return jsonify({"status": "error", "message": "No Market Data Available"})
        
        yf_ltp = round(float(hist['Close'].iloc[-1]), 2)
        
        recent_data = hist.tail(150)
        pa_resistance = int(recent_data['High'].max())
        pa_support = int(recent_data['Low'].min())
        
        final_ltp = None
        support = pa_support
        resistance = pa_resistance
        pcr = "N/A"
        data_source = ""

        # ========================================================
        # 🥇 OPTION 1: NSE OFFICIAL WEBSITE (PRIMARY)
        # ========================================================
        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
            nse_res = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", headers=HEADERS, timeout=5)
            
            if nse_res.status_code == 200:
                raw = nse_res.json()
                final_ltp = float(raw['records']['underlyingValue'])
                data_source = "1. NSE Official Data"
                
                # Calculate S&R from Open Interest
                records = raw['records']['data']
                df_nse = pd.DataFrame([{'STRIKE': r['strikePrice'], 'CALL_OI': r.get('CE', {}).get('openInterest', 0), 'PUT_OI': r.get('PE', {}).get('openInterest', 0)} for r in records if 'CE' in r or 'PE' in r])
                nearby = df_nse[(df_nse['STRIKE'] >= final_ltp - 500) & (df_nse['STRIKE'] <= final_ltp + 500)]
                
                calls = nearby[nearby['STRIKE'] > final_ltp]
                if not calls.empty: resistance = int(calls.loc[calls['CALL_OI'].idxmax(), 'STRIKE'])
                
                puts = nearby[nearby['STRIKE'] <= final_ltp]
                if not puts.empty: support = int(puts.loc[puts['PUT_OI'].idxmax(), 'STRIKE'])
                
                pcr = round(raw['filtered']['PE']['totOI'] / raw['filtered']['CE']['totOI'], 2)
        except:
            pass

        # ========================================================
        # 🥈 OPTION 2: YAHOO FINANCE (FALLBACK)
        # ========================================================
        if not final_ltp:
            final_ltp = yf_ltp
            data_source = "2. Yahoo Finance Data"
            
            if resistance <= final_ltp: resistance = int(final_ltp + 150)
            if support >= final_ltp: support = int(final_ltp - 150)

        hist.iloc[-1, hist.columns.get_loc('Close')] = final_ltp

        # ========================================================
        # 🤖 XGBOOST MACHINE LEARNING LOGIC
        # ========================================================
        df = hist.copy()
        df['VWAP'] = (df['Volume'] * (df['High'] + df['Low'] + df['Close']) / 3).cumsum() / df['Volume'].cumsum()
        df['Target'] = np.where(df['Close'].shift(-1) > df['Close'], 1, 0)
        df.dropna(inplace=True)
        
        bullish, bearish = 50, 50 
        
        if len(df) > 20:
            X = df[['Open', 'High', 'Low', 'Close', 'Volume', 'VWAP']]
            y = df['Target']
            
            model = xgb.XGBClassifier(n_estimators=20, max_depth=3, use_label_encoder=False, eval_metric='logloss')
            model.fit(X, y)
            
            latest_features = pd.DataFrame([df.iloc[-1][['Open', 'High', 'Low', 'Close', 'Volume', 'VWAP']]])
            prob = model.predict_proba(latest_features)[0]
            
            bearish = int(prob[0] * 100)
            bullish = int(prob[1] * 100)
            
            # Smart PCR Adjustment
            if pcr != "N/A":
                if float(pcr) > 1.2: bullish = min(bullish + 10, 100); bearish = max(bearish - 10, 0)
                elif float(pcr) < 0.8: bearish = min(bearish + 10, 100); bullish = max(bullish - 10, 0)

        strike = round(final_ltp / 50) * 50
        op_type = "CE" if bullish > bearish else "PE"
        
        prev_close = hist['Close'].iloc[-2]

        return jsonify({
            "status": "success", 
            "ltp": f"{final_ltp:,.2f}",
            "change": round(final_ltp - prev_close, 2),
            "pct": round(((final_ltp - prev_close)/prev_close)*100, 2),
            "bullish": bullish, 
            "bearish": bearish,
            "support": support, 
            "resistance": resistance,
            "broker_symbol": f"NIFTY {get_current_expiry()} {strike} {op_type}",
            "entry": 130, "t1": 150, "t2": 175, "sl": 115, 
            "data_source": f"{data_source} + XGBoost ML"
        })
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
