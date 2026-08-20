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
import tempfile

app = Flask(__name__)
CORS(app)

SECRET_KEY = os.environ.get('SECRET_KEY', "YOUR_SECRET_KEY_HERE_CHANGE_THIS_TO_A_RANDOM_STRING_12345")
TOKEN_EXPIRY_DAYS = 30

# All your existing functions go here...

@app.route('/api/validate', methods=['GET', 'POST'])
def api_validate():
    try:
        if request.method == 'POST':
            data = request.get_json()
            if data:
                token = data.get('token', '')
                device_fingerprint = data.get('device_fingerprint', '')
            else:
                token = request.form.get('token', '')
                device_fingerprint = request.form.get('device_fingerprint', '')
        else:
            token = request.args.get('token', '')
            device_fingerprint = request.args.get('device_fp', '')
        
        if not token:
            return jsonify({'success': False, 'error': 'Missing token parameter'})
        
        # Handle database file - for Vercel, we need to handle this differently
        df = None
        possible_paths = [
            "/tmp/database.xlsx",  # Vercel temp dir
            "database.xlsx",
            "data/database.xlsx"
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
            return jsonify({'success': True, 'data': clean_data})
        else:
            return jsonify({'success': False, 'error': error or 'Validation failed'})
            
    except Exception as e:
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'platform': 'Vercel',
        'python_version': sys.version,
        'pandas_version': str(pd.__version__)
    })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'name': 'GPS Extractor API (Vercel)',
        'version': '1.0',
        'status': 'running'
    })

# For Vercel serverless
def handler(request, context):
    return app(request.environ, start_response)

def start_response(status, headers):
    return status, headers
