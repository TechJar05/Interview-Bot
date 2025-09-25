import os
from dotenv import load_dotenv
import logging

# Load environment variables from .env file
load_dotenv()

# Logging
logging.basicConfig(
    level=logging.DEBUG,
    filename='interview_app.log',
    filemode='a',
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)

class Config:
    # --- Core secrets ---
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")

    # --- Session / Cookies (HTTP testing on EC2 IP) ---
    # For production with HTTPS, set SESSION_COOKIE_SECURE=1 in environment.
    SESSION_COOKIE_NAME = "interview_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") in ("1", "true", "True")
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_DOMAIN = None          # important when accessing via raw IP
    SESSION_COOKIE_MAX_SIZE = 4093
    SESSION_USE_SIGNER = True
    SESSION_PERMANENT = False             # non-permanent for now (safer on shared machines)
    PERMANENT_SESSION_LIFETIME = 4600     # seconds

    # --- Concurrency / Interview settings ---
    MAX_CONCURRENT_INTERVIEWS = 10
    INTERVIEW_SESSION_TIMEOUT = 4600      # 76 minutes
    SESSION_CLEANUP_INTERVAL = 300        # 5 minutes

    # --- Server settings ---
    USE_RELOADER = False                  # avoid duplicate threads/processes
    THREADED = True
    PORT = int(os.getenv("PORT", "5000"))
    HOST = os.getenv("HOST", "0.0.0.0")

    # --- Routing / Auth ---
    # Use a relative path to avoid "localhost" redirects from remote clients
    LOGIN_URL = os.getenv("LOGIN_URL", "/login")

    # --- OpenAI ---
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "Unknown")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "Unknown")

    # --- Snowflake ---
    SNOW_USER = os.getenv("SNOW_USER", "")
    SNOW_PWD = os.getenv("SNOW_PWD", "")
    SNOW_ACCOUNT = os.getenv("SNOW_ACCOUNT", "")
    SNOW_WAREHOUSE = os.getenv("SNOW_WAREHOUSE", "")
    SNOW_DATABASE = os.getenv("SNOW_DATABASE", "")
    SNOW_SCHEMA = os.getenv("SNOW_SCHEMA", "PUBLIC")

    # --- Interview runtime options ---
    MAX_FRAME_SIZE = 500
    FRAME_CAPTURE_INTERVAL = 5
    INTERVIEW_DURATION = 900
    PAUSE_THRESHOLD = 40
    ENABLE_VISUAL_ANALYSIS = True
    VISUAL_ANALYSIS_FREQUENCY = 3
    ENABLE_PERFORMANCE_MONITORING = True
    VAD_SAMPLING_RATE = 16000
    VAD_FRAME_DURATION = 30
    VAD_MODE = 2

    # --- Email / Voice ---
    OUTLOOK_EMAIL = os.getenv("OUTLOOK_EMAIL")
    OUTLOOK_PASSWORD = os.getenv("OUTLOOK_PASSWORD")
    DEEPGRAM_STT = os.getenv("DEEPGRAM_STT", "Unknown")
    ELEVENLABS_TTS = os.getenv("ELEVENLABS_TTS", "Unknown")
    GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "Unknown")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "Unknown")
