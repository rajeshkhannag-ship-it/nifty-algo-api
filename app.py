from flask import Flask, jsonify
from flask_cors import CORS
import random
from datetime import datetime

app = Flask(__name__)
CORS(app)

@app.route('/api/get_call', methods=['GET'])
def get_live_call():
    try:
        # 1. నిఫ్టీ 50 ఇండెక్స్ లైవ్ డేటా (Mock Data)
        # రియల్ టైమ్ లో ఇక్కడ nsepython ద్వారా డేటా తీసుకోవాలి
        index_ltp = 23415.50
        index_change = 503.10
        index_pct = 2.19
        
        # 2. ఆల్గో ట్రేడ్ కాల్ డేటా
        pcr = 1.27
        entry_price = 131
        target_strike = 23500
        option_type = "CE"
        expiry_date = "02-Apr-2026"

        return jsonify({
            "status": "success",
            "index_data": {
                "ltp": f"{index_ltp:,.2f}",
                "change": f"+{index_change:,.2f}",
                "pct": f"{index_pct}% లాభం"
            },
            "scrip": "NIFTY",
            "expiry": expiry_date,
            "strike": target_strike,
            "option_type": option_type,
            "entry": entry_price,
            "t1": entry_price + 20,
            "t2": entry_price + 45,
            "sl": entry_price - 15,
            "pcr_value": pcr
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)