from flask import Flask, jsonify
from config import Config
import logging
from backend.utils.json_encoder import CustomJSONEncoder
from backend.utils.session_interface import setup_database_sessions

# Create Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Setup database-based sessions for concurrent support
setup_database_sessions(app)

# Setup monitoring for concurrent interviews
from backend.services.monitoring_service import start_monitoring
start_monitoring()

# Setup logging (optional, can be improved)
logging.basicConfig(level=logging.DEBUG)

# Register blueprints
from backend.routes.auth import auth_bp
from backend.routes.dashboard import dashboard_bp
from backend.routes.interview import interview_bp
from backend.routes.monitoring import monitoring_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(interview_bp)
app.register_blueprint(monitoring_bp)

# Set custom JSON encoder
app.json_encoder = CustomJSONEncoder

# Error handlers
@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({"error": "Resource not found"}), 404

if __name__ == '__main__':
    print('\nApp running! Open http://localhost:5000/ in your browser.\n')
    app.run(host="0.0.0.0", port=5000, debug=True)
