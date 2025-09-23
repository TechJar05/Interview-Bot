import logging
import threading
import time
from datetime import datetime, timedelta
from backend.services.session_service import get_active_interviews
from backend.services.connection_pool import get_connection_pool

logger = logging.getLogger(__name__)

class InterviewMonitor:
    def __init__(self):
        self.active_interviews = {}
        self.interview_history = []
        self.lock = threading.Lock()
        self.monitoring_active = False
        self.monitor_thread = None
    
    def start_interview(self, user_id, session_id, interview_data):
        """Track the start of an interview"""
        with self.lock:
            self.active_interviews[user_id] = {
                'session_id': session_id,
                'start_time': datetime.now(),
                'last_activity': datetime.now(),
                'questions_answered': 0,
                'total_questions': len(interview_data.get('questions', [])),
                'jd_id': interview_data.get('jd_id'),
                'difficulty_level': interview_data.get('difficulty_level'),
                'status': 'active'
            }
            logger.info(f"Interview started for user {user_id}. Active interviews: {len(self.active_interviews)}")
    
    def update_interview_activity(self, user_id, questions_answered=None):
        """Update interview activity"""
        with self.lock:
            if user_id in self.active_interviews:
                self.active_interviews[user_id]['last_activity'] = datetime.now()
                if questions_answered is not None:
                    self.active_interviews[user_id]['questions_answered'] = questions_answered
    
    def end_interview(self, user_id):
        """Track the end of an interview"""
        with self.lock:
            if user_id in self.active_interviews:
                interview_info = self.active_interviews[user_id]
                interview_info['end_time'] = datetime.now()
                interview_info['duration'] = (interview_info['end_time'] - interview_info['start_time']).total_seconds()
                interview_info['status'] = 'completed'
                
                # Move to history
                self.interview_history.append(interview_info)
                
                # Keep only last 100 interviews in history
                if len(self.interview_history) > 100:
                    self.interview_history = self.interview_history[-100:]
                
                del self.active_interviews[user_id]
                logger.info(f"Interview ended for user {user_id}. Active interviews: {len(self.active_interviews)}")
    
    def get_active_interview_count(self):
        """Get current number of active interviews"""
        with self.lock:
            return len(self.active_interviews)
    
    def get_interview_stats(self):
        """Get interview statistics"""
        with self.lock:
            active_count = len(self.active_interviews)
            total_history = len(self.interview_history)
            
            # Calculate average duration
            if self.interview_history:
                avg_duration = sum(i.get('duration', 0) for i in self.interview_history) / len(self.interview_history)
            else:
                avg_duration = 0
            
            return {
                'active_interviews': active_count,
                'total_completed': total_history,
                'average_duration_minutes': round(avg_duration / 60, 2),
                'active_interview_details': list(self.active_interviews.keys())
            }
    
    def get_user_interview_status(self, user_id):
        """Get interview status for a specific user"""
        with self.lock:
            if user_id in self.active_interviews:
                return self.active_interviews[user_id]
            return None
    
    def cleanup_stale_interviews(self, timeout_minutes=30):
        """Clean up interviews that haven't had activity for a while"""
        cutoff_time = datetime.now() - timedelta(minutes=timeout_minutes)
        stale_users = []
        
        with self.lock:
            for user_id, interview_info in self.active_interviews.items():
                if interview_info['last_activity'] < cutoff_time:
                    stale_users.append(user_id)
            
            for user_id in stale_users:
                interview_info = self.active_interviews[user_id]
                interview_info['end_time'] = datetime.now()
                interview_info['duration'] = (interview_info['end_time'] - interview_info['start_time']).total_seconds()
                interview_info['status'] = 'timeout'
                self.interview_history.append(interview_info)
                del self.active_interviews[user_id]
                logger.warning(f"Interview timed out for user {user_id}")
        
        return len(stale_users)

class SystemMonitor:
    def __init__(self):
        self.start_time = datetime.now()
        self.request_count = 0
        self.error_count = 0
        self.lock = threading.Lock()
    
    def increment_request(self):
        """Increment request counter"""
        with self.lock:
            self.request_count += 1
    
    def increment_error(self):
        """Increment error counter"""
        with self.lock:
            self.error_count += 1
    
    def get_system_stats(self):
        """Get system statistics"""
        with self.lock:
            uptime = (datetime.now() - self.start_time).total_seconds()
            error_rate = (self.error_count / max(self.request_count, 1)) * 100
            
            # Get connection pool stats
            pool_stats = get_connection_pool().get_pool_stats()
            
            return {
                'uptime_seconds': round(uptime, 2),
                'uptime_hours': round(uptime / 3600, 2),
                'total_requests': self.request_count,
                'total_errors': self.error_count,
                'error_rate_percent': round(error_rate, 2),
                'requests_per_minute': round(self.request_count / max(uptime / 60, 1), 2),
                'connection_pool': pool_stats
            }

# Global instances
interview_monitor = InterviewMonitor()
system_monitor = SystemMonitor()

def start_monitoring():
    """Start the monitoring system"""
    if not interview_monitor.monitoring_active:
        interview_monitor.monitoring_active = True
        interview_monitor.monitor_thread = threading.Thread(target=_monitoring_loop, daemon=True)
        interview_monitor.monitor_thread.start()
        logger.info("Interview monitoring started")

def stop_monitoring():
    """Stop the monitoring system"""
    interview_monitor.monitoring_active = False
    if interview_monitor.monitor_thread:
        interview_monitor.monitor_thread.join(timeout=5)
    logger.info("Interview monitoring stopped")

def _monitoring_loop():
    """Main monitoring loop"""
    while interview_monitor.monitoring_active:
        try:
            # Clean up stale interviews every 5 minutes
            stale_count = interview_monitor.cleanup_stale_interviews()
            if stale_count > 0:
                logger.info(f"Cleaned up {stale_count} stale interviews")
            
            # Log system stats every 10 minutes
            if int(time.time()) % 600 == 0:  # Every 10 minutes
                stats = system_monitor.get_system_stats()
                interview_stats = interview_monitor.get_interview_stats()
                logger.info(f"System Stats: {stats}")
                logger.info(f"Interview Stats: {interview_stats}")
            
            time.sleep(300)  # Sleep for 5 minutes
        except KeyboardInterrupt:
            logger.info("Monitoring loop interrupted")
            break
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            time.sleep(60)  # Sleep for 1 minute on error

def get_monitoring_dashboard_data():
    """Get data for monitoring dashboard"""
    return {
        'system': system_monitor.get_system_stats(),
        'interviews': interview_monitor.get_interview_stats(),
        'active_interviews': interview_monitor.active_interviews.copy()
    }
