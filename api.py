from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import hashlib
import json
import os
from datetime import datetime, timedelta
import urllib.parse
import requests
import sys

print(f"Python version: {sys.version}")
print(f"Pandas version: {pd.__version__}")

app = Flask(__name__)
CORS(app)

# ============================================================
# SECURITY CONFIG
# ============================================================
SECRET_KEY = "YOUR_SECRET_KEY_HERE_CHANGE_THIS_TO_A_RANDOM_STRING_12345"
TOKEN_EXPIRY_DAYS = 30

# ============================================================
# SECURITY FUNCTIONS
# ============================================================
def load_excel_data(file_path):
    try:
        if not os.path.exists(file_path):
            return None
        df = pd.read_excel(file_path, engine='openpyxl')
        print(f"Loaded {len(df)} records from {file_path}")
        return df
    except Exception as e:
        print(f"Error loading Excel: {str(e)}")
        return None

def get_online_time():
    try:
        time_apis = [
            "https://worldtimeapi.org/api/timezone/Etc/UTC",
            "https://timeapi.io/api/time/current/utc",
        ]
        for api in time_apis:
            try:
                response = requests.get(api, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if 'utc_datetime' in data:
                        return datetime.fromisoformat(data['utc_datetime'].replace('Z', '+00:00'))
                    elif 'dateTime' in data:
                        return datetime.fromisoformat(data['dateTime'].replace('Z', '+00:00'))
                    elif 'unixtime' in data:
                        return datetime.fromtimestamp(data['unixtime'])
            except:
                continue
        return None
    except:
        return None

def get_site_by_plaid(df, plaid):
    if df is None or df.empty:
        return None
    site = df[df['PLAID'].astype(str).str.strip() == str(plaid).strip()]
    if not site.empty:
        return site.iloc[0].to_dict()
    return None

def safe_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip()

def validate_device(device_to_check, allowed_devices_hashed):
    if not device_to_check or not allowed_devices_hashed:
        return False
    
    hashed = hashlib.sha256(device_to_check.encode()).hexdigest()
    if hashed in allowed_devices_hashed:
        return True
    
    if device_to_check.startswith('MAC:'):
        without_prefix = device_to_check[4:]
        hashed = hashlib.sha256(without_prefix.encode()).hexdigest()
        if hashed in allowed_devices_hashed:
            return True
    elif device_to_check.startswith('IMEI:'):
        without_prefix = device_to_check[5:]
        hashed = hashlib.sha256(without_prefix.encode()).hexdigest()
        if hashed in allowed_devices_hashed:
            return True
    
    return False

def validate_token(token, df, device_fingerprint=None):
    try:
        token = token.strip()
        if '%' in token:
            token = urllib.parse.unquote(token)
        
        try:
            token_data = bytes.fromhex(token).decode('utf-8')
        except ValueError:
            return None, "Invalid token format - not valid hex"
        
        parts = token_data.rsplit('|', 1)
        if len(parts) != 2:
            return None, "Invalid token format - expected 2 parts"
        
        payload_json, signature = parts
        expected_signature = hashlib.sha256(f"{payload_json}|{SECRET_KEY}".encode()).hexdigest()
        if signature != expected_signature:
            return None, "Invalid token signature - token may be tampered"
        
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            return None, "Invalid token payload"
        
        created_str = payload.get('c')
        expires_str = payload.get('e')
        site_plaid = payload.get('s')
        user_name = payload.get('u')
        user_email = payload.get('a')
        allowed_devices = payload.get('d', [])
        raw_devices = payload.get('raw', '')
        
        if not all([created_str, expires_str, site_plaid]):
            return None, "Missing required token data"
        
        current_time = get_online_time()
        if current_time is None:
            current_time = datetime.utcnow()
        
        try:
            created = datetime.fromisoformat(created_str)
            expires = datetime.fromisoformat(expires_str)
        except ValueError:
            return None, "Invalid date format"
        
        if current_time > expires:
            return None, f"Token expired on {expires.strftime('%B %d, %Y')}"
        
        if current_time < created - timedelta(minutes=5):
            return None, "Token is from the future - possible fraud"
        
        if allowed_devices and device_fingerprint:
            if not validate_device(device_fingerprint, allowed_devices):
                return None, "Device not authorized. MAC Address or IMEI not recognized."
        elif allowed_devices:
            return None, "Device verification required. Please ensure your device is registered."
        
        site_data = get_site_by_plaid(df, site_plaid)
        if site_data is None:
            return None, f"Site not found: {site_plaid}"
        
        site_data['_user_email'] = user_email
        site_data['_user_name'] = user_name
        site_data['_token_created'] = created_str
        site_data['_token_expires'] = expires_str
        site_data['_device_restricted'] = bool(allowed_devices)
        site_data['_device_count'] = len(allowed_devices)
        site_data['_raw_devices'] = raw_devices
        
        return site_data, None
        
    except Exception as e:
        return None, f"Validation error: {str(e)}"

# ============================================================
# API ENDPOINTS
# ============================================================
@app.route('/api/validate', methods=['GET', 'POST'])
def api_validate():
    try:
        if request.method == 'POST':
            data = request.get_json()
            if data:
                token = data.get('token', '')
                device_fingerprint = data.get('device_fingerprint', '')
                device_id = data.get('device_id', '')
            else:
                token = request.form.get('token', '')
                device_fingerprint = request.form.get('device_fingerprint', '')
                device_id = request.form.get('device_id', '')
        else:
            token = request.args.get('token', '')
            device_fingerprint = request.args.get('device_fp', '')
            device_id = request.args.get('device_id', '')
        
        if not token:
            return jsonify({
                'success': False,
                'error': 'Missing token parameter'
            })
        
        # Try multiple paths for database file
        df = None
        possible_paths = [
            "database.xlsx",
            "data/database.xlsx",
            "/opt/render/project/src/database.xlsx",
            "/opt/render/project/src/data/database.xlsx"
        ]
        
        for path in possible_paths:
            df = load_excel_data(path)
            if df is not None:
                break
        
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'error': 'No data available. Please upload database.xlsx'
            })
        
        site_data, error = validate_token(token, df, device_fingerprint)
        
        if site_data:
            clean_data = {k: v for k, v in site_data.items() if not k.startswith('_')}
            return jsonify({
                'success': True,
                'data': clean_data
            })
        else:
            return jsonify({
                'success': False,
                'error': error or 'Validation failed'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        })

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'message': 'Flask API is running',
        'python_version': sys.version,
        'pandas_version': pd.__version__
    })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'name': 'GPS Extractor API',
        'version': '1.0',
        'endpoints': {
            '/api/validate': 'Validate token with device fingerprint',
            '/api/health': 'Health check',
            '/': 'This info page'
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Flask API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
