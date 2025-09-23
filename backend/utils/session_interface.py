import json
import logging
from datetime import datetime, timedelta
from flask.sessions import SessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict
from backend.services.session_service import (
    create_session, get_session, update_session_access, 
    delete_session, cleanup_expired_sessions, init_session_tables
)

logger = logging.getLogger(__name__)

class DatabaseSession(CallbackDict, SessionMixin):
    def __init__(self, initial=None, session_id=None, user_id=None):
        def on_update(self):
            self.modified = True
        CallbackDict.__init__(self, initial, on_update)
        self.session_id = session_id
        self.user_id = user_id
        self.modified = False
        self.new = False

class DatabaseSessionInterface(SessionInterface):
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize the session interface with the app"""
        self.app = app
        # Initialize session tables
        with app.app_context():
            init_session_tables()
        
        # Set up periodic cleanup of expired sessions
        if not hasattr(app, 'session_cleanup_task'):
            app.session_cleanup_task = None
    
    def open_session(self, app, request):
        """Open a session for the request"""
        session_id = request.cookies.get(app.config.get('SESSION_COOKIE_NAME', 'session'))
        user_id = None
        
        if session_id:
            session_data = get_session(session_id)
            if session_data:
                user_id = session_data.get('user')
                return DatabaseSession(
                    initial=session_data,
                    session_id=session_id,
                    user_id=user_id
                )
        
        # Create new session
        return DatabaseSession(session_id=None, user_id=None)
    
    def save_session(self, app, session, response):
        """Save the session data"""
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        
        if not session:
            if session.modified:
                # Delete the session
                if session.session_id:
                    delete_session(session.session_id)
                response.delete_cookie(
                    app.config.get('SESSION_COOKIE_NAME', 'session'),
                    domain=domain,
                    path=path
                )
            return
        
        # Set session expiry
        expires = self.get_expiration_time(app, session)
        
        # Save session data
        if session.modified:
            session_data = dict(session)
            if session.session_id:
                # Update existing session
                update_session_access(session.session_id)
            else:
                # Create new session
                if session.get('user'):
                    session_id = create_session(
                        session.get('user'), 
                        session_data, 
                        expires_in=app.config.get('PERMANENT_SESSION_LIFETIME', 4600)
                    )
                    session.session_id = session_id
                    session.user_id = session.get('user')
        
        # Set cookie
        if session.session_id:
            response.set_cookie(
                app.config.get('SESSION_COOKIE_NAME', 'session'),
                session.session_id,
                expires=expires,
                httponly=self.get_cookie_httponly(app),
                domain=domain,
                path=path,
                secure=self.get_cookie_secure(app),
                samesite=self.get_cookie_samesite(app)
            )
    
    def get_cookie_domain(self, app):
        """Get the cookie domain"""
        return app.config.get('SESSION_COOKIE_DOMAIN')
    
    def get_cookie_path(self, app):
        """Get the cookie path"""
        return app.config.get('SESSION_COOKIE_PATH', '/')
    
    def get_cookie_httponly(self, app):
        """Get the cookie httponly setting"""
        return app.config.get('SESSION_COOKIE_HTTPONLY', True)
    
    def get_cookie_secure(self, app):
        """Get the cookie secure setting"""
        return app.config.get('SESSION_COOKIE_SECURE', False)
    
    def get_cookie_samesite(self, app):
        """Get the cookie samesite setting"""
        return app.config.get('SESSION_COOKIE_SAMESITE', 'Lax')
    
    def get_expiration_time(self, app, session):
        """Get the session expiration time"""
        if session.permanent:
            lifetime = app.config.get('PERMANENT_SESSION_LIFETIME', 4600)
        else:
            lifetime = app.config.get('SESSION_LIFETIME', 3600)
        
        return datetime.utcnow() + timedelta(seconds=lifetime)

def setup_database_sessions(app):
    """Setup database-based sessions for the Flask app"""
    app.session_interface = DatabaseSessionInterface(app)
    logger.info("Database-based session interface initialized")
