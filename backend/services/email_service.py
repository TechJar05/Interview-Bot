import smtplib
from email.mime.text import MIMEText
from config import Config

def send_email(to, subject, body):
    smtp_server = getattr(Config, 'SMTP_SERVER', 'smtp.example.com')
    smtp_port = getattr(Config, 'SMTP_PORT', 587)
    smtp_user = getattr(Config, 'OUTLOOK_EMAIL', None)
    smtp_password = getattr(Config, 'OUTLOOK_PASSWORD', None)
    if not smtp_user or not smtp_password:
        raise Exception('SMTP credentials not set in config.')
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, [to], msg.as_string()) 