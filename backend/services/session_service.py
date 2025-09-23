import json
import logging
from datetime import datetime, timedelta
from flask import current_app
from backend.services.connection_pool import get_pooled_connection, return_pooled_connection
from backend.utils.json_encoder import CustomJSONEncoder

logger = logging.getLogger(__name__)

def init_session_tables():
    """Initialize session and interview data tables"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        # Create sessions table
        cs.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id STRING PRIMARY KEY,
                user_id STRING,
                session_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Create interview_data table for concurrent interviews
        cs.execute("""
            CREATE TABLE IF NOT EXISTS interview_data (
                user_id STRING,
                session_id STRING,
                interview_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                PRIMARY KEY (user_id, session_id)
            );
        """)
        
        conn.commit()
        cs.close()
        logger.info("Session tables initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing session tables: {e}")
    finally:
        if conn:
            return_pooled_connection(conn)

def create_session(user_id, session_data, expires_in=4600):
    """Create a new session for a user"""
    conn = None
    try:
        session_id = f"{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        cs.execute("""
            INSERT INTO user_sessions (session_id, user_id, session_data, expires_at)
            VALUES (%s, %s, %s, %s)
        """, (session_id, user_id, json.dumps(session_data, cls=CustomJSONEncoder), expires_at))
        
        conn.commit()
        cs.close()
        
        return session_id
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return None
    finally:
        if conn:
            return_pooled_connection(conn)

def get_session(session_id):
    """Get session data by session ID"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        cs.execute("""
            SELECT session_data, expires_at 
            FROM user_sessions 
            WHERE session_id = %s AND expires_at > CURRENT_TIMESTAMP
        """, (session_id,))
        
        row = cs.fetchone()
        cs.close()
        
        if row:
            # Update last accessed time
            update_session_access(session_id)
            return json.loads(row[0])
        return None
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        return None
    finally:
        if conn:
            return_pooled_connection(conn)

def update_session_access(session_id):
    """Update last accessed time for session"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        cs.execute("""
            UPDATE user_sessions 
            SET last_accessed = CURRENT_TIMESTAMP 
            WHERE session_id = %s
        """, (session_id,))
        
        conn.commit()
        cs.close()
    except Exception as e:
        logger.error(f"Error updating session access: {e}")
    finally:
        if conn:
            return_pooled_connection(conn)

def delete_session(session_id):
    """Delete a session"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        cs.execute("DELETE FROM user_sessions WHERE session_id = %s", (session_id,))
        
        conn.commit()
        cs.close()
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
    finally:
        if conn:
            return_pooled_connection(conn)

def cleanup_expired_sessions():
    """Clean up expired sessions"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        cs.execute("DELETE FROM user_sessions WHERE expires_at <= CURRENT_TIMESTAMP")
        cs.execute("DELETE FROM interview_data WHERE expires_at <= CURRENT_TIMESTAMP")
        
        conn.commit()
        cs.close()
        logger.info("Expired sessions cleaned up")
    except Exception as e:
        logger.error(f"Error cleaning up expired sessions: {e}")
    finally:
        if conn:
            return_pooled_connection(conn)

def get_interview_data(user_id, session_id=None):
    """Get interview data for a user"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        if session_id:
            cs.execute("""
                SELECT interview_data 
                FROM interview_data 
                WHERE user_id = %s AND session_id = %s AND expires_at > CURRENT_TIMESTAMP
            """, (user_id, session_id))
        else:
            cs.execute("""
                SELECT interview_data 
                FROM interview_data 
                WHERE user_id = %s AND expires_at > CURRENT_TIMESTAMP
                ORDER BY updated_at DESC
                LIMIT 1
            """, (user_id,))
        
        row = cs.fetchone()
        cs.close()
        
        if row:
            return json.loads(row[0])
        return None
    except Exception as e:
        logger.error(f"Error getting interview data: {e}")
        return None
    finally:
        if conn:
            return_pooled_connection(conn)

def save_interview_data(user_id, interview_data, session_id=None, expires_in=4600):
    """Save interview data for a user"""
    conn = None
    try:
        if not session_id:
            session_id = f"{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        # Use MERGE for upsert operation
        cs.execute("""
            MERGE INTO interview_data AS target
            USING (SELECT %s as user_id, %s as session_id) AS source
            ON target.user_id = source.user_id AND target.session_id = source.session_id
            WHEN MATCHED THEN
                UPDATE SET 
                    interview_data = %s,
                    updated_at = CURRENT_TIMESTAMP,
                    expires_at = %s
            WHEN NOT MATCHED THEN
                INSERT (user_id, session_id, interview_data, expires_at)
                VALUES (%s, %s, %s, %s)
        """, (user_id, session_id, json.dumps(interview_data, cls=CustomJSONEncoder), 
              expires_at, user_id, session_id, json.dumps(interview_data, cls=CustomJSONEncoder), expires_at))
        
        conn.commit()
        cs.close()
        
        return session_id
    except Exception as e:
        logger.error(f"Error saving interview data: {e}")
        return None
    finally:
        if conn:
            return_pooled_connection(conn)

def clear_interview_data(user_id, session_id=None):
    """Clear interview data for a user"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        if session_id:
            cs.execute("DELETE FROM interview_data WHERE user_id = %s AND session_id = %s", (user_id, session_id))
        else:
            cs.execute("DELETE FROM interview_data WHERE user_id = %s", (user_id,))
        
        conn.commit()
        cs.close()
    except Exception as e:
        logger.error(f"Error clearing interview data: {e}")
    finally:
        if conn:
            return_pooled_connection(conn)

def get_active_interviews():
    """Get count of active interviews"""
    conn = None
    try:
        conn = get_pooled_connection()
        cs = conn.cursor()
        
        cs.execute("""
            SELECT COUNT(DISTINCT user_id) 
            FROM interview_data 
            WHERE expires_at > CURRENT_TIMESTAMP
        """)
        
        row = cs.fetchone()
        cs.close()
        
        return row[0] if row else 0
    except Exception as e:
        logger.error(f"Error getting active interviews count: {e}")
        return 0
    finally:
        if conn:
            return_pooled_connection(conn)
