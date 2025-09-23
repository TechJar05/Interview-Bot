import snowflake.connector
import logging

SNOW_USER = "Aishwarya1212"
SNOW_PWD = "Aishwaryatechjar@2025"
SNOW_ACCOUNT = "XIMRCYJ-PZ75081"  # Corrected: no region suffix here
SNOW_REGION = "us-west-2"          # Region separately
SNOW_WAREHOUSE = "COMPUTE_WH"
SNOW_DATABASE = "JETKINGINTERVIEW"
SNOW_SCHEMA = "PUBLIC"
logger = logging.getLogger(__name__)

def get_snowflake_connection():
    try:
        conn = snowflake.connector.connect(
            user=SNOW_USER,
            password=SNOW_PWD,
            account=SNOW_ACCOUNT,
            region=SNOW_REGION,
            warehouse=SNOW_WAREHOUSE,
            database=SNOW_DATABASE,
            schema=SNOW_SCHEMA,
            autocommit=True,
            client_session_keep_alive=True,
            ocsp_fail_open=True
        )
        return conn
    except snowflake.connector.errors.Error as e:
        logger.error(f"Snowflake connection error: {str(e)}")
        return None
