import threading
import logging
import time
from queue import Queue, Empty
from backend.services.snowflake_service import get_snowflake_connection

logger = logging.getLogger(__name__)

class ConnectionPool:
    def __init__(self, max_connections=20, connection_timeout=30):
        self.max_connections = max_connections
        self.connection_timeout = connection_timeout
        self.pool = Queue(maxsize=max_connections)
        self.active_connections = 0
        self.lock = threading.Lock()
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize the connection pool with some connections"""
        try:
            for _ in range(min(5, self.max_connections)):
                conn = get_snowflake_connection()
                if conn:
                    self.pool.put(conn)
                    with self.lock:
                        self.active_connections += 1
            logger.info(f"Connection pool initialized with {self.active_connections} connections")
        except Exception as e:
            logger.error(f"Error initializing connection pool: {e}")
    
    def get_connection(self):
        """Get a connection from the pool"""
        try:
            # Try to get an existing connection
            try:
                conn = self.pool.get(timeout=5)
                if conn and self._test_connection(conn):
                    return conn
                else:
                    # Connection is invalid, create a new one
                    if conn:
                        self._close_connection(conn)
                    return self._create_new_connection()
            except Empty:
                # No connections available, create a new one
                return self._create_new_connection()
        except Exception as e:
            logger.error(f"Error getting connection from pool: {e}")
            return get_snowflake_connection()  # Fallback to direct connection
    
    def _create_new_connection(self):
        """Create a new connection if pool is not full"""
        with self.lock:
            if self.active_connections < self.max_connections:
                conn = get_snowflake_connection()
                if conn:
                    self.active_connections += 1
                    logger.debug(f"Created new connection. Active: {self.active_connections}")
                    return conn
        
        # Pool is full, wait for a connection
        try:
            conn = self.pool.get(timeout=self.connection_timeout)
            if conn and self._test_connection(conn):
                return conn
            else:
                if conn:
                    self._close_connection(conn)
                return self._create_new_connection()
        except Empty:
            logger.warning("Connection pool timeout, creating direct connection")
            return get_snowflake_connection()
    
    def return_connection(self, conn):
        """Return a connection to the pool"""
        if conn is None:
            return
        
        try:
            if self._test_connection(conn):
                # Connection is still valid, return to pool
                try:
                    self.pool.put(conn, timeout=1)
                    logger.debug("Connection returned to pool")
                except:
                    # Pool is full, close the connection
                    self._close_connection(conn)
            else:
                # Connection is invalid, close it
                self._close_connection(conn)
        except Exception as e:
            logger.error(f"Error returning connection to pool: {e}")
            self._close_connection(conn)
    
    def _test_connection(self, conn):
        """Test if a connection is still valid"""
        try:
            if conn is None:
                return False
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True
        except Exception:
            return False
    
    def _close_connection(self, conn):
        """Close a connection and update pool stats"""
        try:
            if conn:
                conn.close()
        except Exception as e:
            logger.error(f"Error closing connection: {e}")
        finally:
            with self.lock:
                self.active_connections = max(0, self.active_connections - 1)
    
    def get_pool_stats(self):
        """Get current pool statistics"""
        with self.lock:
            return {
                'active_connections': self.active_connections,
                'pool_size': self.pool.qsize(),
                'max_connections': self.max_connections
            }
    
    def cleanup(self):
        """Clean up all connections in the pool"""
        logger.info("Cleaning up connection pool")
        while not self.pool.empty():
            try:
                conn = self.pool.get_nowait()
                self._close_connection(conn)
            except Empty:
                break
        with self.lock:
            self.active_connections = 0

# Global connection pool instance
_connection_pool = None
_pool_lock = threading.Lock()

def get_connection_pool():
    """Get the global connection pool instance"""
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                _connection_pool = ConnectionPool()
    return _connection_pool

def get_pooled_connection():
    """Get a connection from the pool"""
    pool = get_connection_pool()
    return pool.get_connection()

def return_pooled_connection(conn):
    """Return a connection to the pool"""
    pool = get_connection_pool()
    pool.return_connection(conn)

def cleanup_connection_pool():
    """Clean up the connection pool"""
    global _connection_pool
    if _connection_pool:
        _connection_pool.cleanup()
        _connection_pool = None
