import logging

def setup_logging(level=logging.DEBUG, log_file='interview_app.log'):
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(log_file, maxBytes=10000000, backupCount=5)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger = logging.getLogger()
    logger.setLevel(level)
    logger.addHandler(handler)
    return logger 