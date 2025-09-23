"""
Session utils: For future session management helpers.
"""
 
def ensure_session_key(session, key, default):
    if key not in session:
        session[key] = default
    return session[key] 