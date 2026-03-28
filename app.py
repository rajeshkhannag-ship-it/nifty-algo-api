from flask import Flask, jsonify
from flask_cors import CORS
import random

app = Flask(__name__)
# ఫ్రంట్‌ఎండ్ నుండి వచ్చే రిక్వెస్ట్‌లను యాక్సెప్ట్ చేయడానికి CORS వాడుతున్నాం
CORS(app) 

# లైవ్ డేటాను లెక్కించి పంపే API ఎండ్‌పాయింట్ (API Endpoint)
@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    """
    ఇక్కడ బ్యాక్‌గ్రౌండ్‌లో NSE డేటా, PCR, S&R లెక్కింపులు జరుగుతాయి.
    (ప్రస్తుతానికి డెమో లైవ్ డేటాను పంపుతున్నాము. ఇక్కడ మీరు nsepython యాడ్ చేసుకోవచ్చు)
    """
    try:
        # ఉదాహరణకు PCR మరియు Spot Price లెక్కింపు
        pcr = round(random.uniform(0.6, 1.6), 2) # 0.6 నుండి 1.6 మధ్య రండమ్ PCR
        spot_price = 23415
        expiry_date = "02-Apr-2026"
        
        # మార్కెట్ ట్రెండ్ (PCR) ఆధారంగా CE లేదా PE నిర్ణయించడం
        if pcr >= 1.05:
            option_type = "CE"
            target_strike = 23500 # రెసిస్టెన్స్
        else:
            option_type = "PE"
            target_strike = 23300 # సపోర్ట్
            
        # ఆప్షన్ ప్రీమియం ఎంట్రీ ధర
        entry_price = random.randint(100, 150)
        
        # టార్గెట్ మరియు స్టాప్ లాస్ లెక్కించడం
        target_1 = entry_price + 20
        target_2 = entry_price + 45
        stop_loss = entry_price - 15
        
        # ఈ డేటాను JSON ఫార్మాట్‌లో వెబ్‌పేజీకి పంపుతాము
        return jsonify({
            "status": "success",
            "scrip": "NIFTY",
            "expiry": expiry_date,
            "strike": target_strike,
            "option_type": option_type,
            "entry": entry_price,
            "t1": target_1,
            "t2": target_2,
            "sl": stop_loss,
            "pcr_value": pcr
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    print("🚀 Flask సర్వర్ స్టార్ట్ అయింది! వెబ్‌పేజీని ఓపెన్ చేయండి.")
    # పోర్ట్ 5000 పై లోకల్ సర్వర్‌ను రన్ చేస్తున్నాం
    app.run(host='0.0.0.0', port=5000, debug=True)