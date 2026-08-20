from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sys
import traceback

app = Flask(__name__)
CORS(app)

# Global error handler to catch all exceptions
@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({
        'error': str(e),
        'traceback': traceback.format_exc(),
        'type': type(e).__name__
    }), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'status': 'healthy',
        'platform': 'Vercel',
        'python_version': sys.version,
        'cwd': os.getcwd(),
        'files': os.listdir('.') if os.path.exists('.') else [],
        'message': 'API is running'
    })

@app.route('/api/health', methods=['GET'])
def health():
    try:
        return jsonify({
            'status': 'healthy',
            'platform': 'Vercel',
            'python_version': sys.version,
            'message': 'API is working!'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/api/validate', methods=['GET', 'POST'])
def validate():
    try:
        token = request.args.get('token', '') or request.json.get('token', '') if request.is_json else ''
        return jsonify({
            'success': True,
            'message': 'Token received',
            'token_preview': token[:20] + '...' if len(token) > 20 else token,
            'length': len(token)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# For Vercel
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Starting Flask API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
