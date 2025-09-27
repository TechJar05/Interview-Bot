from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
from backend.services.snowflake_service import get_snowflake_connection
from backend.services.redis_service import clear_interview_data
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import random
import string
import logging
import smtplib
from email.mime.text import MIMEText
from config import Config

from urllib.parse import urlparse, urljoin




logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)


def is_safe_url(target):
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target or ""))
    return (test.scheme in ("http", "https")) and (ref.netloc == test.netloc)

@auth_bp.route("/login", methods=["GET", "POST"])
@auth_bp.route("/", methods=["GET", "POST"])
def login():
    logger.debug(f"Login route accessed with method: {request.method}")

    # Pull ?next=... on GET (for template) OR on POST (from form)
    next_url = request.args.get("next") if request.method == "GET" else (request.form.get("next") or request.args.get("next"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        logger.debug(f"Login attempt for username: {username}")

        # --- Admin static login ---
        if username == "TechjarTech" and password == "Techjar@789":
            logger.debug("Admin login successful")
            session["user"] = username
            session["role"] = "recruiter"     # adjust if you have a separate 'admin' role
            session.permanent = False

            # Honor 'next' if it is safe (local to same host)
            if next_url and is_safe_url(next_url):
                return redirect(next_url)
            return redirect(url_for("dashboard.recruiter_home"))  # or your admin landing

        # --- Student / normal login via Snowflake ---
        try:
            conn = get_snowflake_connection()
            if not conn:
                raise Exception("Could not connect to Snowflake")

            cs = conn.cursor()
            logger.debug(f"Checking credentials for user: {username}")
            cs.execute("SELECT PASSWORD FROM REGISTER WHERE EMAIL_ID=%s;", (username,))
            row = cs.fetchone()
            cs.close()
            conn.close()

            if row and check_password_hash(row[0], password):
                logger.debug("User login successful")
                session["user"] = username     # this matches guards: if 'user' not in session: ...
                session["role"] = "student"    # set role as needed
                session.permanent = False

                if next_url and is_safe_url(next_url):
                    return redirect(next_url)
                return redirect(url_for("dashboard.dashboard"))   # student landing
            else:
                logger.warning("Invalid credentials")
                # keep 'next' so the form retains it
                return render_template("login.html", error="Invalid credentials", next=next_url or "")
        except Exception as e:
            logger.error(f"Login error: {e}")
            return render_template("login.html", error="Error during login", next=next_url or "")

    # GET: render login, pass next so template can keep it
    return render_template("login.html", next=next_url or "")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    logger.debug(f"Register route accessed with method: {request.method}")
    if request.method == "POST":
        name = request.form.get("name")
        course_name = request.form.get("course_name")
        email_id = request.form.get("email_id")
        mobile_no = request.form.get("mobile_no")
        center = request.form.get("center")
        batch_no = request.form.get("batch_no")
        password = request.form.get("password")
        logger.debug(f"Registration attempt for email: {email_id}")
        hashed_password = generate_password_hash(password)
        student_id = str(uuid.uuid4())
        try:
            conn = get_snowflake_connection()
            if not conn:
                raise Exception("Could not connect to Snowflake")
            cs = conn.cursor()
            logger.debug("Creating REGISTER table if not exists")
            cs.execute("""
                CREATE TABLE IF NOT EXISTS REGISTER (
                    STUDENT_ID STRING PRIMARY KEY,
                    NAME STRING,
                    COURSE_NAME STRING,
                    EMAIL_ID STRING,
                    MOBILE_NO STRING,
                    CENTER STRING,
                    BATCH_NO STRING,
                    PASSWORD STRING,
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)
            logger.debug("Inserting new user registration")
            cs.execute("""
                INSERT INTO REGISTER (STUDENT_ID, NAME, COURSE_NAME, EMAIL_ID, MOBILE_NO, CENTER, BATCH_NO, PASSWORD)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, (student_id, name, course_name, email_id, mobile_no, center, batch_no, hashed_password))
            conn.commit()
            cs.close()
            conn.close()
            logger.info(f"Registration successful for email: {email_id}")
            return render_template("login.html", message="Registration successful! Please login.")
        except Exception as e:
            logger.error(f"Error during registration: {e}")
            return render_template("register.html", error="Registration failed. Please try again.")
    return render_template("register.html")

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    logger.debug(f"Forgot password route accessed with method: {request.method}")
    if request.method == "POST":
        email = request.form.get("username")
        if not email:
            logger.warning("Empty email in forgot password request")
            return render_template("forgot_password.html", error="Please enter your email ID")
        
        # Generate new random password
        new_password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%^&*", k=10))
        hashed_password = generate_password_hash(new_password)
        logger.debug(f"Password reset for email: {email}")
        
        try:
            # Connect to Snowflake
            conn = get_snowflake_connection()
            if not conn:
                raise Exception("Could not connect to Snowflake")
            cs = conn.cursor()

            # Check if user exists
            cs.execute("SELECT * FROM REGISTER WHERE EMAIL_ID=%s", (email,))
            user = cs.fetchone()
            if not user:
                logger.warning(f"No account found for email: {email}")
                return render_template("forgot_password.html", error="No account found with that email.")

            # Update new password
            logger.debug(f"Updating password for email: {email}")
            cs.execute("UPDATE REGISTER SET PASSWORD=%s WHERE EMAIL_ID=%s", (hashed_password, email))
            conn.commit()

            # Prepare email
            msg = MIMEText(
                f"""Hello,\n\nYour new password is: {new_password}\n\nPlease login here: {Config.LOGIN_URL}\n
                For security, please change your password after login.\n\nThanks,\nInterview Bot Team\n"""
            )
            msg['Subject'] = 'Password Reset - Interview Bot'
            msg['From'] = Config.GMAIL_EMAIL  # Replace with your Gmail
            msg['To'] = email

            # Send email via Gmail SMTP
            logger.debug(f"Sending password reset email to: {email}")
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(Config.GMAIL_EMAIL, Config.GMAIL_APP_PASSWORD)  # Use Gmail App Password
                server.send_message(msg)

            cs.close()
            conn.close()

            logger.info(f"Password reset email sent to: {email}")
            return render_template("forgot_password.html", message="A new password has been sent to your email.")
        
        except Exception as e:
            logger.error(f"Error resetting password: {e}")
            return render_template("forgot_password.html", error="Error resetting password.")

    return render_template("forgot_password.html")

@auth_bp.route("/logout")
def logout():
    user_id = session.get("user")
    logger.debug(f"Logout requested by user: {user_id}")
    if user_id:
        clear_interview_data(user_id)
    session.clear()
    return redirect(url_for("auth.login")) 