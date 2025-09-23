import snowflake.connector

SNOW_USER      = "Aishwarya16"
SNOW_PWD       = "Aishwarya#12345"
SNOW_ACCOUNT   = "DDSFRIC-WO13283"
SNOW_WAREHOUSE = "COMPUTE_WH"
SNOW_DATABASE  = "JETKINGINTERVIEW"
SNOW_SCHEMA    = "PUBLIC"

def list_tables():
    ctx = snowflake.connector.connect(
        user=SNOW_USER,
        password=SNOW_PWD,
        account=SNOW_ACCOUNT,
        warehouse=SNOW_WAREHOUSE,
        database=SNOW_DATABASE,
        schema=SNOW_SCHEMA,
    )
    cs = ctx.cursor()
    try:
        cs.execute("SHOW TABLES")
        tables = cs.fetchall()
        print("Tables in your schema:")
        for table in tables:
            print(table[1])  # table name
    finally:
        cs.close()
        ctx.close()

list_tables()
