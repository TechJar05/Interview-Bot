#!/usr/bin/env python3
"""
Robust startup script for the interview bot application
Handles Windows socket errors and provides better error handling
"""
import os
import sys
import logging
import signal
import time
from contextlib import contextmanager

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def setup_logging():
    """Setup logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('app_startup.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )

@contextmanager
def graceful_shutdown():
    """Context manager for graceful shutdown"""
    def signal_handler(signum, frame):
        print("\n🛑 Shutting down gracefully...")
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        yield
    except KeyboardInterrupt:
        print("\n🛑 Application interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)

def check_dependencies():
    """Check if all required dependencies are available"""
    try:
        import flask
        import snowflake.connector
        import openai
        print("✅ All dependencies are available")
        return True
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("Please install missing dependencies with: pip install -r requirements.txt")
        return False

def test_database_connection():
    """Test database connection"""
    try:
        from backend.services.snowflake_service import get_snowflake_connection
        conn = get_snowflake_connection()
        if conn:
            conn.close()
            print("✅ Database connection successful")
            return True
        else:
            print("❌ Database connection failed")
            return False
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return False

def start_application():
    """Start the Flask application with error handling"""
    try:
        from app import app
        
        print("\n🚀 Starting Interview Bot Application...")
        print("=" * 50)
        
        # Test database connection
        if not test_database_connection():
            print("⚠️  Warning: Database connection failed, but continuing...")
        
        # Start the application
        print("\n🌐 Application is starting...")
        print("📱 Open http://localhost:5000/ in your browser")
        print("📊 Monitoring dashboard: http://localhost:5000/monitoring_dashboard")
        print("\n" + "=" * 50)
        
        # Run the application with Windows-specific settings
        app.run(
            host='0.0.0.0',
            port=5000,
            debug=True,
            use_reloader=False,  # Disable reloader to avoid socket issues
            threaded=True
        )
        
    except OSError as e:
        if "Address already in use" in str(e):
            print("❌ Port 5000 is already in use!")
            print("💡 Try one of these solutions:")
            print("   1. Stop other applications using port 5000")
            print("   2. Change the port in app.py")
            print("   3. Run: netstat -ano | findstr :5000")
        else:
            print(f"❌ Socket error: {e}")
        return False
    except Exception as e:
        print(f"❌ Application startup error: {e}")
        return False

def main():
    """Main function"""
    setup_logging()
    
    print("🎯 Interview Bot Application Startup")
    print("=" * 50)
    
    # Check dependencies
    if not check_dependencies():
        return False
    
    # Start application with graceful shutdown
    with graceful_shutdown():
        return start_application()

if __name__ == "__main__":
    success = main()
    if not success:
        print("\n❌ Application failed to start")
        print("📋 Check the logs for more details")
        sys.exit(1)
