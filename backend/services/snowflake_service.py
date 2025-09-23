import snowflake.connector
import logging
from config import Config

def get_snowflake_connection():
    try:
        logging.debug(f"Attempting to connect to Snowflake with user={Config.SNOW_USER}, account={Config.SNOW_ACCOUNT}, warehouse={Config.SNOW_WAREHOUSE}, database={Config.SNOW_DATABASE}, schema={Config.SNOW_SCHEMA}")
        conn = snowflake.connector.connect(
            user=Config.SNOW_USER,
            password=Config.SNOW_PWD,
            account=Config.SNOW_ACCOUNT,
            warehouse=Config.SNOW_WAREHOUSE,
            database=Config.SNOW_DATABASE,
            schema=Config.SNOW_SCHEMA,
            autocommit=True,
            client_session_keep_alive=True
        )
        logging.debug("Successfully connected to Snowflake")
        return conn
    except Exception as e:
        import traceback
        logging.error(f"Snowflake connection error: {str(e)}\n" + traceback.format_exc())
        raise  # Show the real error in the browser for debugging 