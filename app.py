import yfinance as yf
import pandas as pd
import numpy as np
import requests
import xgboost as xgb
import pyotp
import urllib3
import base64
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, redirect
from flask_cors import CORS

# 🔥 SSL Warnings & Proxy Fixes
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)

# ==========================================
# 🔐 STOCKO (SAS ONLINE) API CREDENTIALS
# ==========================================
SAS_CLIENT_ID = "SAS-CLIENT1"
SAS_SECRET = "Hhtg74iYYZY1nSJUvDBxKntGqfigem6yKyYw9rlb2qSXyhEEs8BZEtw27KsIE1UI"
SAS_BASE_URL = "https://api.stocko.in"

# ⚠️ VERY IMPORTANT: This MUST exactly match the link in your Stocko Dashboard!
SAS_REDIRECT_URI = "https://nifty-algo-api.onrender.com/api/sas_callback"

TOTP_SECRET = "మీ_16_అక్షరాల_సీక్రెట్_కీ_ఇక్కడ_పెట్టండి" 
SAS_ACCESS_TOKEN = None  

# ------------------------------------------
# 1. SAS API: Auto-Login & Callbacks
# ------------------------------------------
def get_live_totp():
    if TOTP_SECRET and TOTP_SECRET != "మీ_16_అక్షరాల_సీక్రెట్_కీ_ఇక్కడ_పెట్టండి":
        return pyotp.TOTP(TOTP_SECRET).now()
    return None

@app.route('/api/sas_login')
def sas_login():
    """Redirects to Official Stocko Login Page"""
    # ⚠️ Using exact params as per your python desktop code
    auth_url = f"{SAS_BASE_URL}/oauth2/auth?response_type=code&client_id={SAS_CLIENT_ID}&redirect_uri={SAS_REDIRECT_URI}&state=XGB_MASTER_SECURE_AUTH"
    return redirect(auth_url)

@app.route('/api/sas_callback')
def sas_callback():
    """Receives Auth Code and Generates Access Token"""
    global SAS_ACCESS_TOKEN
    auth_code = request.args.get('code')
    
    if not auth_code: 
        return "❌ Error: No Auth Code Received from Stocko. Close window and try again."
    
    # ⚠️ Using Base64 Encoding as per your python desktop code
    token_url = f"{SAS_BASE_URL}/oauth2/token"
    payload = {"grant_type": "authorization_code", "code": auth_code, "redirect_uri": SAS_REDIRECT_URI}
    auth_str = base64.b64encode(f"{SAS_CLIENT_ID}:{SAS_SECRET}".encode()).decode()
    headers = {'Authorization': f'Basic {auth_str}', 'Content-Type': 'application/x-www-form-urlencoded'}
    
    try:
        res = requests.post(token_url, data=payload, headers=headers, verify=False)
        if res.status_code == 200:
            SAS_ACCESS_TOKEN = res.json().get('access_token')
            return "✅ Stocko Login Successful! Access Token Generated. You can safely close this browser window and refresh your dashboard."
        else:
            return f"❌ Token Error: {res.text}"
    except Exception as e: 
        return f"❌ Request Error: {str(e)}"

# ------------------------------------------
# 2. SAS API: Option Chain (S&R) & Live Data
# ------------------------------------------
def get_current_expiry():
    now = datetime.now()
    days_ahead = 3 - now.weekday() # Thursday Expiry
    if days_ahead < 0 or (days_ahead == 0 and (now.hour > 15 or (now.hour == 15 and now.minute >= 30))):
        days_ahead += 7 
    return (now + timedelta(days=days_ahead)).strftime("%d %b").upper()

def get_sas_live_spot():
    """Fetch NIFTY 50 Live Price from Stocko"""
    global SAS_ACCESS_TOKEN
    if not SAS_ACCESS_TOKEN: return None
    
    headers = {"Authorization": f"Bearer {SAS_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"instruments": ["NSE_IDX:Nifty 50"]}
    
    try:
        res = requests.post(f"{SAS_BASE_URL}/quotes", headers=headers, json=payload, timeout=4, verify=False)
        if res.status_code == 200:
            data = res.json()
            for k, v in data.items():
                if 'Nifty 50' in k: 
                    return float(v.get('ltp'))
        return None
    except: return None

def fetch_sas_option_chain_snr(atm):
    """Strict ±500 Range Option Chain S&R from Stocko"""
    global SAS_ACCESS_TOKEN
    if not SAS_ACCESS_TOKEN: return atm + 100, atm - 100
    
    exp_code = get_current_expiry().replace(" ", "") 
    strikes = range(atm - 500, atm + 550, 50) 
    
    instruments = []
    for s in strikes:
        instruments.append(f"NSE_FO:NIFTY{exp_code}{s}CE")
        instruments.append(f"NSE_FO:NIFTY{exp_code}{s}PE")
        
    headers = {"Authorization": f"Bearer {SAS_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"instruments": instruments} 
    
    try:
        res = requests.post(f"{SAS_BASE_URL}/quotes", headers=headers, json=payload, timeout=4, verify=False)
        if res.status_code == 200:
            data = res.json()
            ce_list, pe_list = [], []
            
            for k, v in data.items():
                if 'oi' in v:
                    strike_val = int(''.join(filter(str.isdigit, k.split(exp_code)[-1]))) 
                    if 'CE' in k and strike_val > atm: ce_list.append((strike_val, v.get('oi', 0)))
                    if 'PE' in k and strike_val <= atm: pe_list.append((strike_val, v.get('oi', 0)))
            
            ce_list.sort(key=lambda x: x[1], reverse=True)
            pe_list.sort(key=lambda x: x[1], reverse=True)
            
            res_strike = ce_list[0][0] if ce_list else atm + 100
            sup_strike = pe_list[0][0] if pe_list else atm - 100
            
            return res_strike, sup_strike
        return atm + 100, atm - 100
    except: return atm + 100, atm - 100

# ------------------------------------------
# 3. Main Nifty Algo Endpoint (Waterfall Logic)
# ------------------------------------------
@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        # Step 1: Always get YFinance Historical Data for XGBoost ML
        hist = yf.Ticker("^NSEI").history(interval="5m", period="5d")
        if hist.empty: return jsonify({"status": "error", "message": "No Market Data Available"})
        
        yf_ltp = round(float(hist['Close'].iloc[-1]), 2)
        
        # Step 2: Set Defaults
        recent_data = hist.tail(150)
        pa_resistance = int(recent_data['High'].max())
        pa_support = int(recent_data['Low'].min())
        
        final_ltp = None
        support = pa_support
        resistance = pa_resistance
        data_source = ""

        # ========================================================
        # 🥇 OPTION 1: SAS ONLINE API (PRIMARY - ZERO DELAY)
        # ========================================================
        if SAS_ACCESS_TOKEN:
            live_spot = get_sas_live_spot()
            if live_spot:
                final_ltp = live_spot
                data_source = "1. SAS Online Live API"
                
                atm = int(round(final_ltp / 50) * 50)
                sas_res, sas_sup = fetch_sas_option_chain_snr(atm)
                resistance = sas_res
                support = sas_sup

        # ========================================================
        # 🥈 OPTION 2: YAHOO FINANCE (FALLBACK)
        # ========================================================
        if not final_ltp:
            final_ltp = yf_ltp
            data_source = "2. Yahoo Finance Data"
            
            if resistance <= final_ltp: resistance = int(final_ltp + 150)
            if support >= final_ltp: support = int(final_ltp - 150)

        # Update historical df with our chosen Live LTP
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

        strike = round(final_ltp / 50) * 50
        op_type = "CE" if bullish > bearish else "PE"

        return jsonify({
            "status": "success", 
            "ltp": f"{final_ltp:,.2f}",
            "change": round(final_ltp - hist['Close'].iloc[-2], 2),
            "pct": round(((final_ltp - final_ltp)/final_ltp)*100, 2), # Placeholder for PCT
            "bullish": bullish, 
            "bearish": bearish,
            "support": support, 
            "resistance": resistance,
            "broker_symbol": f"NIFTY {get_current_expiry()} {strike} {op_type}",
            "entry": 130, "t1": 150, "t2": 175, "sl": 115, 
            "data_source": f"{data_source} + XGBoost ML",
            "auto_totp_status": "Active" if get_live_totp() else "Pending Setup"
        })
    except Exception as e: 
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
