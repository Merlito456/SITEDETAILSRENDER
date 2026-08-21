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

print(f"🐍 Python version: {sys.version}")
print(f"📊 Pandas version: {pd.__version__}")

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
        print(f"✅ Loaded {len(df)} records from {file_path}")
        return df
    except Exception as e:
        print(f"❌ Error: {str(e)}")
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

def get_site_by_name(df, site_name):
    if df is None or df.empty:
        return None
    site = df[df['SITE'].astype(str).str.strip().str.upper() == str(site_name).strip().upper()]
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
    
    # Check without prefix (MAC, IMEI, ANDROID, ANDROIDID)
    prefixes = ['MAC:', 'IMEI:', 'ANDROID:', 'ANDROIDID:']
    for prefix in prefixes:
        if device_to_check.startswith(prefix):
            without_prefix = device_to_check[len(prefix):]
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
        site_name = payload.get('n', '')  # Site Name (optional - for backward compatibility)
        start_date_str = payload.get('sd', '')  # Start Date
        end_date_str = payload.get('ed', '')    # End Date
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
        
        # Check if token is expired
        if current_time > expires:
            return None, f"Token expired on {expires.strftime('%B %d, %Y at %I:%M %p')}"
        
        # Check if token is used too early (fraud prevention)
        if current_time < created - timedelta(minutes=5):
            return None, "Token is from the future - possible fraud attempt"
        
        # Check date range if provided
        if start_date_str and end_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str)
                end_date = datetime.fromisoformat(end_date_str)
                if current_time < start_date:
                    return None, f"Token not yet valid. Valid from {start_date.strftime('%B %d, %Y')}"
                if current_time > end_date:
                    return None, f"Token expired on {end_date.strftime('%B %d, %Y')}"
            except:
                pass
        
        # DEVICE VALIDATION
        if allowed_devices and device_fingerprint:
            if not validate_device(device_fingerprint, allowed_devices):
                return None, "Device not authorized. MAC Address, IMEI, or Android ID not recognized."
        elif allowed_devices:
            return None, "Device verification required. Please ensure your device is registered."
        
        # Get site data
        site_data = get_site_by_plaid(df, site_plaid)
        if site_data is None:
            return None, f"Site not found: {site_plaid}"
        
        # Add metadata to response
        site_data['_token_created'] = created_str
        site_data['_token_expires'] = expires_str
        site_data['_device_restricted'] = bool(allowed_devices)
        site_data['_device_count'] = len(allowed_devices)
        site_data['_raw_devices'] = raw_devices
        site_data['_start_date'] = start_date_str
        site_data['_end_date'] = end_date_str
        
        # If site_name is in token, use it (for backward compatibility)
        if site_name:
            site_data['_site_name'] = site_name
        
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
            # Remove internal fields before sending
            clean_data = {k: v for k, v in site_data.items() if not k.startswith('_')}
            
            # Add token metadata to response
            response_data = {
                'success': True,
                'data': clean_data,
                'token_info': {
                    'created': site_data.get('_token_created', ''),
                    'expires': site_data.get('_token_expires', ''),
                    'start_date': site_data.get('_start_date', ''),
                    'end_date': site_data.get('_end_date', ''),
                    'device_restricted': site_data.get('_device_restricted', False),
                    'device_count': site_data.get('_device_count', 0),
                    'site_name': site_data.get('_site_name', site_data.get('SITE', ''))
                }
            }
            
            return jsonify(response_data)
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
    """Health check endpoint - returns API status"""
    return jsonify({
        'status': 'healthy',
        'message': 'Flask API is running',
        'python_version': sys.version,
        'pandas_version': str(pd.__version__),
        'endpoints': {
            '/': 'API Info',
            '/api/health': 'Health check',
            '/api/validate': 'Token validation (GET/POST)'
        }
    })

@app.route('/api/validate-token', methods=['POST'])
def validate_token_endpoint():
    """
    Alternative endpoint for token validation with request body
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No JSON data provided'
            })
        
        token = data.get('token', '')
        device_fingerprint = data.get('device_fingerprint', '')
        
        if not token:
            return jsonify({
                'success': False,
                'error': 'Missing token parameter'
            })
        
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
            response_data = {
                'success': True,
                'data': clean_data,
                'token_info': {
                    'created': site_data.get('_token_created', ''),
                    'expires': site_data.get('_token_expires', ''),
                    'start_date': site_data.get('_start_date', ''),
                    'end_date': site_data.get('_end_date', ''),
                    'device_restricted': site_data.get('_device_restricted', False),
                    'device_count': site_data.get('_device_count', 0),
                    'site_name': site_data.get('_site_name', site_data.get('SITE', ''))
                }
            }
            return jsonify(response_data)
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

@app.route('/api/decode-token', methods=['POST'])
def decode_token():
    """
    Debug endpoint to decode a token without validation
    """
    try:
        data = request.get_json()
        token = data.get('token', '')
        
        if not token:
            return jsonify({
                'success': False,
                'error': 'Missing token parameter'
            })
        
        token = token.strip()
        if '%' in token:
            token = urllib.parse.unquote(token)
        
        try:
            token_data = bytes.fromhex(token).decode('utf-8')
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Invalid token format - not valid hex'
            })
        
        parts = token_data.rsplit('|', 1)
        if len(parts) != 2:
            return jsonify({
                'success': False,
                'error': 'Invalid token format - expected 2 parts'
            })
        
        payload_json, signature = parts
        
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            return jsonify({
                'success': False,
                'error': 'Invalid token payload'
            })
        
        return jsonify({
            'success': True,
            'payload': payload,
            'signature': signature
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'name': 'GPS Extractor API',
        'version': '2.0',
        'status': 'running',
        'endpoints': {
            '/': 'This info page',
            '/api/health': 'Health check',
            '/api/validate': 'Validate token with device fingerprint (GET/POST)',
            '/api/validate-token': 'Validate token with request body (POST)',
            '/api/decode-token': 'Decode token without validation (POST)'
        },
        'token_features': {
            'site_plaid': 'Site PLAID identifier',
            'site_name': 'Site name from Column B',
            'start_date': 'Token validity start date',
            'end_date': 'Token validity end date',
            'device_fingerprint': 'MAC Address, IMEI, or Android ID'
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Starting Flask API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
