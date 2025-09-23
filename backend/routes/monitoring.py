from flask import Blueprint, render_template, jsonify, session
from backend.services.monitoring_service import get_monitoring_dashboard_data, interview_monitor, system_monitor
import logging

logger = logging.getLogger(__name__)
monitoring_bp = Blueprint('monitoring', __name__)

@monitoring_bp.route('/monitoring_dashboard')
def monitoring_dashboard():
    """Monitoring dashboard for concurrent interviews"""
    if 'user' not in session or session.get('role') != 'recruiter':
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        dashboard_data = get_monitoring_dashboard_data()
        return render_template('monitoring_dashboard.html', data=dashboard_data)
    except Exception as e:
        logger.error(f"Error loading monitoring dashboard: {e}")
        return jsonify({"error": "Failed to load dashboard"}), 500

@monitoring_bp.route('/api/monitoring/stats')
def get_monitoring_stats():
    """API endpoint to get monitoring statistics"""
    if 'user' not in session or session.get('role') != 'recruiter':
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        dashboard_data = get_monitoring_dashboard_data()
        return jsonify(dashboard_data)
    except Exception as e:
        logger.error(f"Error getting monitoring stats: {e}")
        return jsonify({"error": "Failed to get stats"}), 500

@monitoring_bp.route('/api/monitoring/active_interviews')
def get_active_interviews():
    """API endpoint to get active interviews"""
    if 'user' not in session or session.get('role') != 'recruiter':
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        active_interviews = interview_monitor.get_interview_stats()
        return jsonify(active_interviews)
    except Exception as e:
        logger.error(f"Error getting active interviews: {e}")
        return jsonify({"error": "Failed to get active interviews"}), 500

@monitoring_bp.route('/api/monitoring/system_stats')
def get_system_stats():
    """API endpoint to get system statistics"""
    if 'user' not in session or session.get('role') != 'recruiter':
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        system_stats = system_monitor.get_system_stats()
        return jsonify(system_stats)
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        return jsonify({"error": "Failed to get system stats"}), 500

@monitoring_bp.route('/api/monitoring/interview/<user_id>')
def get_user_interview_status(user_id):
    """API endpoint to get interview status for a specific user"""
    if 'user' not in session or session.get('role') != 'recruiter':
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        interview_status = interview_monitor.get_user_interview_status(user_id)
        if interview_status:
            return jsonify(interview_status)
        else:
            return jsonify({"error": "Interview not found"}), 404
    except Exception as e:
        logger.error(f"Error getting interview status for user {user_id}: {e}")
        return jsonify({"error": "Failed to get interview status"}), 500
