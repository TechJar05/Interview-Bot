import snowflake.connector

try:
    conn = snowflake.connector.connect(
        user='Aishwarya16',
        password='Aishwarya#12345',
        account='DDSFRIC-WO13283',  # Use only account name, no region suffix here
        region='us-west-2',
        warehouse='COMPUTE_WH',
        database='JETKINGINTERVIEW',
        schema='PUBLIC',
        autocommit=True
    )
    cs = conn.cursor()
    cs.execute("SELECT current_version()")
    version = cs.fetchone()
    print("Connected to Snowflake, version:", version[0])
    cs.close()
    conn.close()
except Exception as e:
    print("Connection failed:", e)
