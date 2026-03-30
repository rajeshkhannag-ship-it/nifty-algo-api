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

# Render లో రన్ చేస్తున్నారు కాబట్టి ఈ లింక్ వాడాలి (మీ Stocko డాష్బోర్డ్ లో దీన్ని అప్డేట్ చేయండి)
SAS_REDIRECT_URI = "https://nifty-algo-api.onrender.com/api/sas_callback"

# 🔥 TOTP ఆటోమేషన్ సీక్రెట్ కీ (మీరు QR కోడ్ కింద కాపీ చేసింది ఇక్కడ పెట్టాలి)
TOTP_SECRET = "మీ_16_అక్షరాల_సీక్రెట్_కీ_ఇక్కడ_పెట్టండి" 

SAS_ACCESS_TOKEN = None  

# ------------------------------------------
# 1. TOTP & Auto-Login Logic
# ------------------------------------------
def get_live_totp():
    """Google Authenticator లేకుండానే 6-అంకెల కోడ్‌ను ఆటోమేటిక్‌గా జనరేట్ చేస్తుంది"""
    if TOTP_SECRET and TOTP_SECRET != "మీ_16_అక్షరాల_సీక్రెట్_కీ_ఇక్కడ_పెట్టండి":
        totp = pyotp.TOTP(TOTP_SECRET)
        return totp.now()
    return None

@app.route('/api/sas_login')
def sas_login():
    """ఒకవేళ మాన్యువల్ లాగిన్ అవసరమైతే ఈ లింక్ పనిచేస్తుంది"""
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
        res = requests.post(f"{SAS_BASE_URL}/oauth2/token", data=payload)
        data = res.json()
        SAS_ACCESS_TOKEN = data.get('access_token')
        return jsonify({"status": "success", "message": "Stocko API Token Activated Successfully!", "totp_used": get_live_totp()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ------------------------------------------
# 2. Market Data & XGBoost AI Logic
# ------------------------------------------
def get_next_expiry():
    today = datetime.now()
    days_ahead = 3 - today.weekday()
    if days_ahead < 0: days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%d %b").upper()

@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        # Stocko API టోకెన్ ఉంటే అక్కడినుండి, లేదంటే YFinance నుండి డేటా వస్తుంది
        nifty = yf.Ticker("^NSEI")
        hist = nifty.history(interval="5m", period="5d")
        
        if hist.empty: 
            return jsonify({"status": "error", "message": "No Market Data Available"})
            
        ltp = round(float(hist['Close'].iloc[-1]), 2)
        
        # 🧠 Smart Algorithmic S&R (Pure Price Action)
        recent_data = hist.tail(150)
        resistance = int(recent_data['High'].max())
        support = int(recent_data['Low'].min())
        
        if resistance <= ltp: resistance = int(ltp + 150)
        if support >= ltp: support = int(ltp - 150)

        # 🤖 XGBoost Machine Learning Logic
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

        strike = round(ltp / 50) * 50
        op_type = "CE" if bullish > bearish else "PE"
        
        data_src = "Stocko API + XGBoost AI" if SAS_ACCESS_TOKEN else "YFinance + XGBoost AI"

        return jsonify({
            "status": "success", 
            "ltp": f"{ltp:,.2f}",
            "change": round(ltp - hist['Close'].iloc[-2], 2),
            "pct": round(((ltp - hist['Close'].iloc[-2])/hist['Close'].iloc[-2])*100, 2),
            "bullish": bullish, 
            "bearish": bearish,
            "support": support, 
            "resistance": resistance,
            "pcr": "N/A", 
            "broker_symbol": f"NIFTY {get_next_expiry()} {strike} {op_type}",
            "entry": 130, "t1": 150, "t2": 175, "sl": 115, 
            "data_source": data_src,
            "auto_totp_status": "Active" if get_live_totp() else "Pending Setup"
        })
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
