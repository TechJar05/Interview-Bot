import os
from dotenv import load_dotenv
import logging

# Load environment variables from .env file
load_dotenv()

# API Keys and credentials
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OUTLOOK_EMAIL = os.getenv("OUTLOOK_EMAIL")
OUTLOOK_PASSWORD = os.getenv("OUTLOOK_PASSWORD")
LOGIN_URL = os.getenv("LOGIN_URL", "http://localhost:5000")

# Snowflake connection settings
SNOW_USER = os.getenv("SNOW_USER", "Aishwarya1212")
SNOW_PWD = os.getenv("SNOW_PWD", "Aishwaryatechjar@2025")
SNOW_ACCOUNT = os.getenv("SNOW_ACCOUNT", "XIMRCYJ-PZ75081")
SNOW_WAREHOUSE = os.getenv("SNOW_WAREHOUSE", "COMPUTE_WH")
SNOW_DATABASE = os.getenv("SNOW_DATABASE", "JETKINGINTERVIEW")
SNOW_SCHEMA = os.getenv("SNOW_SCHEMA", "PUBLIC")

logging.basicConfig(level=logging.DEBUG, filename='interview_app.log', filemode='a',
                    format='%(asctime)s %(levelname)s %(name)s %(message)s')

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", 'your-secret-key-here')
    PERMANENT_SESSION_LIFETIME = 4600
    SESSION_COOKIE_MAX_SIZE = 4093
    # Database-based session configuration for concurrent interviews
    SESSION_COOKIE_NAME = 'interview_session'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Concurrent interview settings
    MAX_CONCURRENT_INTERVIEWS = 10
    INTERVIEW_SESSION_TIMEOUT = 4600  # 76 minutes
    SESSION_CLEANUP_INTERVAL = 300  # 5 minutes
    
    # Server settings to handle Windows socket issues
    USE_RELOADER = False  # Disable reloader to avoid socket issues on Windows
    THREADED = True  # Enable threading for better concurrent support
    PORT = 5000
    HOST = '0.0.0.0'
    SESSION_PERMANENT = True
    SESSION_USE_SIGNER = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # OpenAI Configuration
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "Unknown")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "Unknown")
    
    # Snowflake Configuration
    SNOW_USER = os.getenv("SNOW_USER", "Aishwarya1212")
    SNOW_PWD = os.getenv("SNOW_PWD", "Aishwaryatechjar@2025")
    SNOW_ACCOUNT = os.getenv("SNOW_ACCOUNT", "XIMRCYJ-PZ75081")
    SNOW_WAREHOUSE = os.getenv("SNOW_WAREHOUSE", "COMPUTE_WH")
    SNOW_DATABASE = os.getenv("SNOW_DATABASE", "JETKINGINTERVIEW")
    SNOW_SCHEMA = os.getenv("SNOW_SCHEMA", "PUBLIC")
    
    # Interview Configuration
    MAX_FRAME_SIZE = 500
    FRAME_CAPTURE_INTERVAL = 5
    INTERVIEW_DURATION = 900
    PAUSE_THRESHOLD = 40
    ENABLE_VISUAL_ANALYSIS = True  # Set to True to enable visual analysis and feedback storage
    VISUAL_ANALYSIS_FREQUENCY = 3  # Process visual feedback every N answers (only if enabled)
    ENABLE_PERFORMANCE_MONITORING = True  # Enable performance timing logs
    VAD_SAMPLING_RATE = 16000
    VAD_FRAME_DURATION = 30
    VAD_MODE = 2
    
    # Email Configuration
    OUTLOOK_EMAIL = os.getenv("OUTLOOK_EMAIL")
    OUTLOOK_PASSWORD = os.getenv("OUTLOOK_PASSWORD")
    LOGIN_URL = os.getenv("LOGIN_URL", "http://localhost:5000")

    #Audio Services 
    DEEPGRAM_STT = os.getenv("DEEPGRAM_STT","Unknown")
    ELEVENLABS_TTS = os.getenv("ELEVENLABS_TTS","Unknown")

    GMAIL_EMAIL = "aaditya.patil.m@gmail.com"
    GMAIL_APP_PASSWORD = "cmys gqxe udhi utto"
    LOGIN_URL = "http://localhost:5000/login"