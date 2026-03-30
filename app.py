import yfinance as yf
import pandas as pd
import numpy as np
import requests
import xgboost as xgb
import pyotp
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ==========================================
# 🔐 STOCKO (SAS ONLINE) API CREDENTIALS
# ==========================================
SAS_CLIENT_ID = "SAS-CLIENT1"
SAS_SECRET = "Hhtg74iYYZY1nSJUvDBxKntGqfigem6yKyYw9rlb2qSXyhEEs8BZEtw27KsIE1UI"
SAS_BASE_URL = "https://api.stocko.in"
SAS_REDIRECT_URI = "https://nifty-algo-api.onrender.com/api/sas_callback"

# 🔥 TOTP ఆటోమేషన్ సీక్రెట్ కీ 
TOTP_SECRET = "మీ_16_అక్షరాల_సీక్రెట్_కీ_ఇక్కడ_పెట్టండి" 

SAS_ACCESS_TOKEN = None  
HEADERS = {'user-agent': 'Mozilla/5.0', 'referer': 'https://www.nseindia.com/'}

# ------------------------------------------
# 1. TOTP & Auto-Login Logic
# ------------------------------------------
def get_live_totp():
    if TOTP_SECRET and TOTP_SECRET != "మీ_16_అక్షరాల_సీక్రెట్_కీ_ఇక్కడ_పెట్టండి":
        return pyotp.TOTP(TOTP_SECRET).now()
    return None

@app.route('/api/sas_login')
def sas_login():
    auth_url = f"{SAS_BASE_URL}/oauth2/auth?response_type=code&client_id={SAS_CLIENT_ID}&redirect_uri={SAS_REDIRECT_URI}&scope=orders holdings"
    return redirect(auth_url)

@app.route('/api/sas_callback')
def sas_callback():
    global SAS_ACCESS_TOKEN
    auth_code = request.args.get('code')
    if not auth_code: return jsonify({"error": "No Auth Code Found"})
    
    payload = {"grant_type": "authorization_code", "client_id": SAS_CLIENT_ID, "client_secret": SAS_SECRET, "redirect_uri": SAS_REDIRECT_URI, "code": auth_code}
    try:
        res = requests.post(f"{SAS_BASE_URL}/oauth2/token", data=payload)
        SAS_ACCESS_TOKEN = res.json().get('access_token')
        return jsonify({"status": "success", "message": "Stocko Token Activated!"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)})

# ------------------------------------------
# 2. Helper Functions
# ------------------------------------------
def get_next_expiry():
    today = datetime.now()
    days_ahead = 3 - today.weekday()
    if days_ahead < 0: days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%d %b").upper()

# ------------------------------------------
# 3. Main Call Endpoint (3-TIER WATERFALL LOGIC)
# ------------------------------------------
@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        # 1. Base AI History Data (Always YFinance for safe historical candles)
        hist = yf.Ticker("^NSEI").history(interval="5m", period="5d")
        if hist.empty: return jsonify({"status": "error", "message": "No Market Data Available"})
        
        yf_ltp = round(float(hist['Close'].iloc[-1]), 2)
        
        # Default Price Action S&R (Fallback)
        recent_data = hist.tail(150)
        pa_resistance = int(recent_data['High'].max())
        pa_support = int(recent_data['Low'].min())
        
        final_ltp = None
        support = pa_support
        resistance = pa_resistance
        pcr = "N/A"
        data_source = ""

        # ========================================================
        # 🥇 OPTION 1: SAS ONLINE API (PRIMARY)
        # ========================================================
        if SAS_ACCESS_TOKEN:
            try:
                headers = {"Authorization": f"Bearer {SAS_ACCESS_TOKEN}"}
                # Request Timeout is set to 3 seconds to avoid freezing
                res = requests.get(f"{SAS_BASE_URL}/api/v1/marketdata/quote/NSE/NIFTY 50", headers=headers, timeout=3)
                if res.status_code == 200:
                    sas_data = res.json().get('data', {})
                    if sas_data.get('ltp'):
                        final_ltp = float(sas_data.get('ltp'))
                        data_source = "1. SAS Online API"
            except: pass

        # ========================================================
        # 🥈 OPTION 2: NSE WEBSITE (SECONDARY)
        # ========================================================
        if not final_ltp:
            try:
                session = requests.Session()
                session.get("https://www.nseindia.com", headers=HEADERS, timeout=3)
                nse_res = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", headers=HEADERS, timeout=4)
                
                if nse_res.status_code == 200:
                    raw = nse_res.json()
                    final_ltp = float(raw['records']['underlyingValue'])
                    data_source = "2. NSE Website"
                    
                    # NSE S&R Logic
                    records = raw['records']['data']
                    df_nse = pd.DataFrame([{'STRIKE': r['strikePrice'], 'CALL_OI': r.get('CE', {}).get('openInterest', 0), 'PUT_OI': r.get('PE', {}).get('openInterest', 0)} for r in records if 'CE' in r or 'PE' in r])
                    nearby = df_nse[(df_nse['STRIKE'] >= final_ltp - 500) & (df_nse['STRIKE'] <= final_ltp + 500)]
                    
                    calls = nearby[nearby['STRIKE'] > final_ltp]
                    if not calls.empty: resistance = int(calls.loc[calls['CALL_OI'].idxmax(), 'STRIKE'])
                    
                    puts = nearby[nearby['STRIKE'] <= final_ltp]
                    if not puts.empty: support = int(puts.loc[puts['PUT_OI'].idxmax(), 'STRIKE'])
                    
                    pcr = round(raw['filtered']['PE']['totOI'] / raw['filtered']['CE']['totOI'], 2)
            except: pass

        # ========================================================
        # 🥉 OPTION 3: YAHOO FINANCE (FALLBACK)
        # ========================================================
        if not final_ltp:
            final_ltp = yf_ltp
            data_source = "3. Yahoo Finance"

        # Update the final recognized LTP for ML accuracy
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
                if float(pcr) > 1.0: bullish = min(bullish + 10, 100); bearish = max(bearish - 10, 0)
                elif float(pcr) < 0.9: bearish = min(bearish + 10, 100); bullish = max(bullish - 10, 0)

        strike = round(final_ltp / 50) * 50
        op_type = "CE" if bullish > bearish else "PE"

        return jsonify({
            "status": "success", 
            "ltp": f"{final_ltp:,.2f}",
            "change": round(final_ltp - hist['Close'].iloc[-2], 2),
            "pct": round(((final_ltp - hist['Close'].iloc[-2])/hist['Close'].iloc[-2])*100, 2),
            "bullish": bullish, 
            "bearish": bearish,
            "support": support, 
            "resistance": resistance,
            "pcr": pcr, 
            "broker_symbol": f"NIFTY {get_next_expiry()} {strike} {op_type}",
            "entry": 130, "t1": 150, "t2": 175, "sl": 115, 
            "data_source": f"{data_source} + XGBoost",
            "auto_totp_status": "Active" if get_live_totp() else "Pending Setup"
        })
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
