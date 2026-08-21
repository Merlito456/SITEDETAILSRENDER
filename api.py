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
    """
    Get site data by PLAID with fixes for:
    - Barangay (Column L) - explicitly fetch from column index 11
    - Contact Number - restore leading zero
    """
    if df is None or df.empty:
        return None
    site = df[df['PLAID'].astype(str).str.strip() == str(plaid).strip()]
    if not site.empty:
        row = site.iloc[0]
        site_dict = row.to_dict()
        
        # ============================================================
        # FIX 1: Explicitly fetch Barangay from Column L (Index 11)
        # ============================================================
        if pd.isna(site_dict.get('BARANGAY')) or str(site_dict.get('BARANGAY')).strip() == "":
            try:
                val = row.iloc[11]  # Column L (0-indexed, so 11 is the 12th column)
                if not pd.isna(val):
                    site_dict['BARANGAY'] = str(val).strip()
                    print(f"✅ Barangay from Column L: {site_dict['BARANGAY']}")
            except Exception as e:
                print(f"⚠️ Could not fetch Barangay from Column L: {str(e)}")
        
        # ============================================================
        # FIX 2: Restore leading zero to Contact Number
        # ============================================================
        contact = site_dict.get('CONTACT NUMBER')
        if contact and not str(contact).startswith('0'):
            try:
                # Try to convert to integer to remove decimals, then add leading zero
                contact_str = str(contact).strip()
                # Remove any decimal points
                if '.' in contact_str:
                    contact_str = contact_str.split('.')[0]
                # Remove any non-digit characters except plus sign
                contact_str = ''.join(ch for ch in contact_str if ch.isdigit() or ch == '+')
                if contact_str and not contact_str.startswith('0'):
                    site_dict['CONTACT NUMBER'] = "0" + contact_str
                    print(f"✅ Fixed Contact Number: {site_dict['CONTACT NUMBER']}")
            except Exception as e:
                print(f"⚠️ Could not fix Contact Number: {str(e)}")
                # Fallback: just add leading zero
                site_dict['CONTACT NUMBER'] = "0" + str(contact)
        
        return site_dict
    return None

def get_site_by_name(df, site_name):
    if df is None or df.empty:
        return None
    site = df[df['SITE'].astype(str).str.strip().str.upper() == str(site_name).strip().upper()]
    if not site.empty:
        row = site.iloc[0]
        site_dict = row.to_dict()
        
        # Apply the same fixes for Barangay and Contact Number
        if pd.isna(site_dict.get('BARANGAY')) or str(site_dict.get('BARANGAY')).strip() == "":
            try:
                val = row.iloc[11]
                if not pd.isna(val):
                    site_dict['BARANGAY'] = str(val).strip()
            except:
                pass
        
        contact = site_dict.get('CONTACT NUMBER')
        if contact and not str(contact).startswith('0'):
            try:
                contact_str = str(contact).strip()
                if '.' in contact_str:
                    contact_str = contact_str.split('.')[0]
                contact_str = ''.join(ch for ch in contact_str if ch.isdigit() or ch == '+')
                if contact_str and not contact_str.startswith('0'):
                    site_dict['CONTACT NUMBER'] = "0" + contact_str
            except:
                site_dict['CONTACT NUMBER'] = "0" + str(contact)
        
        return site_dict
    return None

def safe_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip()

def validate_device(device_to_check, allowed_devices_hashed):
    """
    Validate a single device ID against allowed hashed devices.
    Supports MAC:XX:XX:XX:XX:XX:XX, IMEI:123456789012345, ANDROID:abc123def456
    """
    if not device_to_check or not allowed_devices_hashed:
        return False
    
    # Clean the device ID
    device_to_check = device_to_check.strip().upper()
    
    # Try exact match first
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
    
    # Also check if the entire device_to_check is a plain number (IMEI without prefix)
    if device_to_check.isdigit():
        hashed = hashlib.sha256(f"IMEI:{device_to_check}".encode()).hexdigest()
        if hashed in allowed_devices_hashed:
            return True
        hashed = hashlib.sha256(device_to_check.encode()).hexdigest()
        if hashed in allowed_devices_hashed:
            return True
    
    # Check if it's a MAC without prefix
    if ':' in device_to_check or '-' in device_to_check:
        mac_clean = device_to_check.replace('-', ':')
        hashed = hashlib.sha256(f"MAC:{mac_clean}".encode()).hexdigest()
        if hashed in allowed_devices_hashed:
            return True
        hashed = hashlib.sha256(mac_clean.encode()).hexdigest()
        if hashed in allowed_devices_hashed:
            return True
    
    return False

def validate_token(token, df, device_fingerprint=None, search_site=None):
    """
    Validate a token and optionally search for a site by name or PLAID.
    """
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
        site_name = payload.get('n', '')
        start_date_str = payload.get('sd', '')
        end_date_str = payload.get('ed', '')
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
        
        # ============================================================
        # DEVICE VALIDATION - CORRECTED FOR MULTIPLE IDS
        # ============================================================
        if allowed_devices and device_fingerprint:
            ids_to_check = [fp.strip().upper() for fp in device_fingerprint.split(',') if fp.strip()]
            authorized = False
            matched_device = None
            
            for device_id in ids_to_check:
                if validate_device(device_id, allowed_devices):
                    authorized = True
                    matched_device = device_id
                    break
            
            if not authorized:
                return None, "Device not authorized. MAC Address, IMEI, or Android ID not recognized."
            
            print(f"✅ Device authorized: {matched_device[:20] if matched_device else 'Unknown'}...")
            
        elif allowed_devices:
            return None, "Device verification required. Please ensure your device is registered."
        
        # ============================================================
        # SITE SEARCH
        # ============================================================
        if search_site and not df.empty:
            # Search by Site Name (Column B) first
            site_data = get_site_by_name(df, search_site)
            if site_data is None:
                # If not found by name, try PLAID (Column A)
                site_data = get_site_by_plaid(df, search_site)
            if site_data is None:
                return None, f"Site not found: {search_site}"
        else:
            # Standard login: return the site bound to this token (site_plaid)
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
                search_site = data.get('search_site', '')
            else:
                token = request.form.get('token', '')
                device_fingerprint = request.form.get('device_fingerprint', '')
                device_id = request.form.get('device_id', '')
                search_site = request.form.get('search_site', '')
        else:
            token = request.args.get('token', '')
            device_fingerprint = request.args.get('device_fp', '')
            device_id = request.args.get('device_id', '')
            search_site = request.args.get('search_site', '')
        
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
        
        site_data, error = validate_token(token, df, device_fingerprint, search_site)
        
        if site_data:
            # Remove internal fields before sending
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
        search_site = data.get('search_site', '')
        
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
        
        site_data, error = validate_token(token, df, device_fingerprint, search_site)
        
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

@app.route('/api/search-site', methods=['GET', 'POST'])
def search_site_endpoint():
    """
    Search for a site by name or PLAID without requiring a token.
    Useful for HUB/ASN lookups.
    """
    try:
        if request.method == 'POST':
            data = request.get_json()
            if data:
                search_term = data.get('search_term', '')
            else:
                search_term = request.form.get('search_term', '')
        else:
            search_term = request.args.get('search_term', '')
        
        if not search_term:
            return jsonify({
                'success': False,
                'error': 'Missing search_term parameter'
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
        
        # Search by Site Name first
        site_data = get_site_by_name(df, search_term)
        if site_data is None:
            # Try PLAID
            site_data = get_site_by_plaid(df, search_term)
        
        if site_data:
            clean_data = {k: v for k, v in site_data.items() if not k.startswith('_')}
            return jsonify({
                'success': True,
                'data': clean_data
            })
        else:
            return jsonify({
                'success': False,
                'error': f'Site not found: {search_term}'
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
            '/api/validate': 'Token validation with search support (GET/POST)',
            '/api/validate-token': 'Token validation with request body (POST)',
            '/api/search-site': 'Search site by name or PLAID (GET/POST)',
            '/api/decode-token': 'Decode token without validation (POST)'
        },
        'fixes_applied': {
            'barangay': 'Fetches Barangay from Column L (index 11)',
            'contact_number': 'Restores leading zero to contact numbers'
        }
    })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'name': 'GPS Extractor API',
        'version': '2.1',
        'status': 'running',
        'endpoints': {
            '/': 'This info page',
            '/api/health': 'Health check',
            '/api/validate': 'Validate token with device fingerprint and search support',
            '/api/validate-token': 'Validate token with request body (POST)',
            '/api/search-site': 'Search site by name or PLAID (GET/POST)',
            '/api/decode-token': 'Decode token without validation (POST)'
        },
        'token_features': {
            'site_plaid': 'Site PLAID identifier',
            'site_name': 'Site name from Column B',
            'start_date': 'Token validity start date',
            'end_date': 'Token validity end date',
            'device_fingerprint': 'MAC Address, IMEI, or Android ID'
        },
        'search_features': {
            'search_site': 'Search by Site Name (Column B) or PLAID (Column A)',
            'supports_hub_asn': 'Search for HUB/ASN sites'
        },
        'data_fixes': {
            'barangay': '✅ Barangay fetched from Column L (index 11)',
            'contact_number': '✅ Leading zero restored to contact numbers'
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Starting Flask API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
