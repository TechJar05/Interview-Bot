import time
import logging
from functools import wraps
from config import Config

logger = logging.getLogger(__name__)

def timing_decorator(func_name=None):
    """Decorator to measure function execution time"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not Config.ENABLE_PERFORMANCE_MONITORING:
                return func(*args, **kwargs)
            
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                name = func_name or func.__name__
                # Fixed: Removed Unicode emoji, replaced with [TIMER]
                logger.info(f"[TIMER] {name} completed in {execution_time:.2f} seconds")
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                name = func_name or func.__name__
                # Fixed: Removed Unicode emoji, replaced with [ERROR]
                logger.error(f"[ERROR] {name} failed after {execution_time:.2f} seconds: {str(e)}")
                raise
        return wrapper
    return decorator

class PerformanceMonitor:
    """Simple performance monitoring class"""
    
    def __init__(self, operation_name):
        self.operation_name = operation_name
        self.start_time = None
        
    def __enter__(self):
        if Config.ENABLE_PERFORMANCE_MONITORING:
            self.start_time = time.time()
            # Fixed: Removed Unicode emoji, replaced with [START]
            logger.debug(f"[START] Starting {self.operation_name}")
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if Config.ENABLE_PERFORMANCE_MONITORING and self.start_time:
            execution_time = time.time() - self.start_time
            if exc_type:
                # Fixed: Removed Unicode emoji, replaced with [FAILED]
                logger.error(f"[FAILED] {self.operation_name} failed after {execution_time:.2f} seconds")
            else:
                # Fixed: Removed Unicode emoji, replaced with [COMPLETED]
                logger.info(f"[COMPLETED] {self.operation_name} completed in {execution_time:.2f} seconds")

def log_performance(operation_name):
    """Context manager for performance logging"""
    return PerformanceMonitor(operation_name)