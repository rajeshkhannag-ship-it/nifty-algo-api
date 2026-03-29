import yfinance as yf
import pandas as pd
import numpy as np
import requests
import xgboost as xgb
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ==========================================
# 🔐 Stocko (SASOnline) API Credentials
# ==========================================
SAS_CLIENT_ID = "SAS-CLIENT1"
SAS_SECRET = "Hhtg74iYYZY1nSJUvDBxKntGqfigem6yKyYw9rlb2qSXyhEEs8BZEtw27KsIE1UI"
SAS_REDIRECT_URI = "https://nifty-algo-api.onrender.com/api/sas_callback"
SAS_BASE_URL = "https://api.stocko.in"

SAS_ACCESS_TOKEN = None  

HEADERS = {'user-agent': 'Mozilla/5.0', 'referer': 'https://www.nseindia.com/'}

# ------------------------------------------
# 1. Stocko Auth & Token Generation
# ------------------------------------------
@app.route('/api/sas_login')
def sas_login():
    # 🔥 Updated to Stocko's new /oauth2/auth endpoint
    auth_url = f"{SAS_BASE_URL}/oauth2/auth?response_type=code&client_id={SAS_CLIENT_ID}&redirect_uri={SAS_REDIRECT_URI}&scope=orders holdings"
    return redirect(auth_url)

@app.route('/api/sas_callback')
def sas_callback():
    global SAS_ACCESS_TOKEN
    auth_code = request.args.get('code')
    if not auth_code:
        return jsonify({"error": "No Auth Code Found"})
    
    payload = {
        "grant_type": "authorization_code",
        "client_id": SAS_CLIENT_ID,
        "client_secret": SAS_SECRET,
        "redirect_uri": SAS_REDIRECT_URI,
        "code": auth_code
    }
    
    try:
        # 🔥 Updated to Stocko's new /oauth2/token endpoint
        res = requests.post(f"{SAS_BASE_URL}/oauth2/token", data=payload)
        data = res.json()
        SAS_ACCESS_TOKEN = data.get('access_token')
        return jsonify({"status": "success", "message": "Stocko API Token Activated!", "token": SAS_ACCESS_TOKEN})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ------------------------------------------
# 2. Market Data & S&R Logic
# ------------------------------------------
def get_next_expiry():
    today = datetime.now()
    days_ahead = 3 - today.weekday()
    if days_ahead < 0: days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%d %b").upper()

def get_nse_data(spot_price):
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        response = session.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            raw = response.json()
            records = raw['records']['data']
            df = pd.DataFrame([{'STRIKE': r['strikePrice'], 'CALL_OI': r.get('CE', {}).get('openInterest', 0), 'PUT_OI': r.get('PE', {}).get('openInterest', 0)} for r in records if 'CE' in r or 'PE' in r])
            
            nearby = df[(df['STRIKE'] >= spot_price - 500) & (df['STRIKE'] <= spot_price + 500)]
            calls = nearby[nearby['STRIKE'] > spot_price]
            res = int(calls.loc[calls['CALL_OI'].idxmax(), 'STRIKE']) if not calls.empty else int(spot_price + 100)
            puts = nearby[nearby['STRIKE'] <= spot_price]
            sup = int(puts.loc[puts['PUT_OI'].idxmax(), 'STRIKE']) if not puts.empty else int(spot_price - 100)
            
            pcr = round(raw['filtered']['PE']['totOI'] / raw['filtered']['CE']['totOI'], 2)
            return sup, res, pcr
        return None, None, 1.0
    except: return None, None, 1.0

def fetch_hybrid_nifty_data():
    global SAS_ACCESS_TOKEN
    hist = yf.Ticker("^NSEI").history(interval="5m", period="5d")
    
    if SAS_ACCESS_TOKEN:
        try:
            headers = {"Authorization": f"Bearer {SAS_ACCESS_TOKEN}"}
            res = requests.get(f"{SAS_BASE_URL}/api/v1/marketdata/quote/NSE/NIFTY 50", headers=headers)
            if res.status_code == 200:
                sas_ltp = res.json().get('data', {}).get('ltp')
                if sas_ltp:
                    hist.iloc[-1, hist.columns.get_loc('Close')] = float(sas_ltp)
        except Exception as e:
            pass 
            
    return hist

# ------------------------------------------
# 3. Main Call Endpoint (XGBoost Integration)
# ------------------------------------------
@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        hist = fetch_hybrid_nifty_data()
        if hist.empty: return jsonify({"status": "error", "message": "No Data"})
        
        ltp = round(float(hist.iloc[-1]['Close']), 2)
        support, resistance, pcr = get_nse_data(ltp)
        
        df = hist.copy()
        df['VWAP'] = (df['Volume'] * (df['High'] + df['Low'] + df['Close']) / 3).cumsum() / df['Volume'].cumsum()
        df['Target'] = np.where(df['Close'].shift(-1) > df['Close'], 1, 0)
        df.dropna(inplace=True)
        
        bullish, bearish = 50, 50 
        
        if len(df) > 20:
            X = df[['Open', 'High', 'Low', 'Close', 'Volume', 'VWAP']]
            y = df['Target']
            
            model = xgb.XGBClassifier(n_estimators=15, max_depth=3, use_label_encoder=False, eval_metric='logloss')
            model.fit(X, y)
            
            latest_features = pd.DataFrame([df.iloc[-1][['Open', 'High', 'Low', 'Close', 'Volume', 'VWAP']]])
            prob = model.predict_proba(latest_features)[0]
            
            bearish = int(prob[0] * 100)
            bullish = int(prob[1] * 100)
            
            if pcr > 1.0: bullish = min(bullish + 10, 100); bearish = max(bearish - 10, 0)
            elif pcr < 0.9: bearish = min(bearish + 10, 100); bullish = max(bullish - 10, 0)

        strike = round(ltp / 50) * 50
        op_type = "CE" if bullish > bearish else "PE"
        
        data_src = "Stocko API + XGBoost AI" if SAS_ACCESS_TOKEN else "YF Fallback + XGBoost AI"

        return jsonify({
            "status": "success", "ltp": f"{ltp:,.2f}",
            "change": round(ltp - hist['Close'].iloc[-2], 2),
            "pct": round(((ltp - hist['Close'].iloc[-2])/hist['Close'].iloc[-2])*100, 2),
            "bullish": bullish, "bearish": bearish,
            "support": support if support else "N/A", "resistance": resistance if resistance else "N/A",
            "pcr": pcr, "broker_symbol": f"NIFTY {get_next_expiry()} {strike} {op_type}",
            "entry": 130, "t1": 150, "t2": 175, "sl": 115, "data_source": data_src
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
