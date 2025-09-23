from flask import Blueprint, render_template, session, redirect, url_for, send_file, jsonify, send_from_directory, flash, request
from werkzeug.utils import secure_filename
from backend.services.snowflake_service import get_snowflake_connection
import pandas as pd
import io
import logging
import json
from backend.utils.file_utils import extract_text_from_file
from backend.services.redis_service import save_interview_data, get_interview_data
import os
from backend.services.email_service import send_email
from backend.routes.interview import insert_jd
from datetime import datetime

logger = logging.getLogger(__name__)
dash_bp = Blueprint('dashboard', __name__)

def serialize_row(row):
    # Convert each item in the row: if it's a datetime, convert to string
    return [
        item.isoformat() if hasattr(item, 'isoformat') else item
        for item in row
    ]

@dash_bp.route("/dashboard")
def dashboard():
    logger.debug("Dashboard route accessed")
    if "user" not in session or session.get("role") != "student":
        logger.warning("Unauthorized access to dashboard")
        return redirect(url_for("auth.login"))
    try:
        conn = get_snowflake_connection()
        if not conn:
            raise Exception("Could not connect to Snowflake")
        cs = conn.cursor()
        # Only fetch ratings for the logged-in student
        cs.execute("""
            SELECT roll_no, technical_rating, communication_rating, problem_solving_rating,
                   time_management_rating, total_rating, interview_ts
            FROM interview_rating
            WHERE roll_no = %s AND total_rating IS NOT NULL
            ORDER BY interview_ts
            LIMIT 200
        """, (session["user"],))
        rows = cs.fetchall()
        cols = [desc[0].lower() for desc in cs.description]
        df_ratings = pd.DataFrame(rows, columns=cols)
        df_ratings['interview_number'] = range(1, len(df_ratings) + 1)
        skill_avg = {
            "Technical Knowledge": df_ratings["technical_rating"].mean(),
            "Communication": df_ratings["communication_rating"].mean(),
            "Problem Solving": df_ratings["problem_solving_rating"].mean(),
            "Time Management": df_ratings["time_management_rating"].mean()
        }
        line_data = {
            "interview_numbers": df_ratings["interview_number"].tolist(),
            "avg_ratings": df_ratings["total_rating"].tolist()
        }
        average_rating = round(df_ratings["total_rating"].mean(), 1) if not df_ratings.empty else 0
        completed_interviews = len(df_ratings)
        # Fetch 3 most recent interviews for the student
        recent_interviews = []
        if not df_ratings.empty:
            recent_interviews = df_ratings.sort_values('interview_ts', ascending=False).head(3).to_dict('records')
        cs.close()
        conn.close()
        # skill_avg_json = json.dumps(skill_avg)
        line_data_json = json.dumps(line_data)
        return render_template(
            "dashboard.html",
            user_name=session.get("user"),
            skill_avg=skill_avg,  # pass as dict
            line_data=line_data_json,
            average_rating=average_rating,
            completed_interviews=completed_interviews,
            recent_interviews=recent_interviews
        )
    except Exception as e:
        logger.error(f"Error in dashboard: {str(e)}", exc_info=True)
        return f"Error: {e}"

@dash_bp.route("/recruiter_home")
def recruiter_home():
    logger.debug("Recruiter home route accessed")
    if "user" not in session or session.get("role") != "recruiter":
        logger.warning(f"{session.get('role')} Unauthorized access to recruiter home")
        return redirect(url_for("login"))
    try:
        conn = get_snowflake_connection()
        if not conn:
            raise Exception("Could not connect to Snowflake")
        cs = conn.cursor()
        cs.execute("""
            SELECT roll_no, technical_rating, communication_rating, problem_solving_rating,
                   time_management_rating, total_rating, interview_ts
            FROM interview_rating
            ORDER BY interview_ts DESC
            LIMIT 100
        """)
        ratings_rows = cs.fetchall()
        ratings_cols = [desc[0].lower() for desc in cs.description]
        interview_ratings_json = [
            {
                "roll_no": row[0],
                "technical_rating": row[1],
                "communication_rating": row[2],
                "problem_solving_rating": row[3],
                "time_management_rating": row[4],
                "total_rating": row[5],
                "interview_ts": row[6].strftime('%Y-%m-%d') if row[6] else None
            }
            for row in ratings_rows
        ]
        cs.execute("""
            SELECT student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, interview_ts, jd_id, status
            FROM interview
            ORDER BY interview_ts DESC
            LIMIT 100
        """)
        interview_table_rows = cs.fetchall()
        interview_table_cols = [desc[0].lower() for desc in cs.description]
        # Prepare interview_table_json for JS graphs
        interview_table_json = [
            {col: row[i] for i, col in enumerate(interview_table_cols)}
            for row in interview_table_rows
        ]
        cs.execute("""
            SELECT roll_no, professional_appearance, body_language, environment, 
                   distractions, interview_ts
            FROM visual_feedback
            ORDER BY interview_ts DESC
            LIMIT 100
        """)
        visual_feedback_rows = cs.fetchall()
        visual_feedback_cols = [desc[0].lower() for desc in cs.description]
        cs.close()
        conn.close()
        interview_table_serialized = [serialize_row(row) for row in interview_table_rows]
        interview_ratings_serialized = [serialize_row(row) for row in ratings_rows]
        # Pass as list of lists for JS mapping
        return render_template(
            "recruiter_home.html",
            user_name=session.get("user"),
            role=session.get("role"),
            interview_ratings=ratings_rows,
            interview_ratings_cols=ratings_cols,
            interview_table=interview_table_rows,
            interview_table_cols=interview_table_cols,
            interview_table_json=interview_table_serialized,  # <-- FIXED: pass as list of lists
            visual_feedback=visual_feedback_rows,
            visual_feedback_cols=visual_feedback_cols,
            interview_ratings_json=interview_ratings_json
        )
    except Exception as e:
        logger.error(f"Error in recruiter_home: {str(e)}", exc_info=True)
        return f"Error loading data: {e}"

@dash_bp.route('/export_interview_ratings')
def export_interview_ratings():
    logger.debug("Export interview ratings endpoint called")
    if "user" not in session or session.get("role") != "recruiter":
        logger.warning("Unauthorized export attempt")
        return redirect(url_for("login"))
    try:
        conn = get_snowflake_connection()
        if not conn:
            raise Exception("Could not connect to Snowflake")
        cs = conn.cursor()
        cs.execute("""
            SELECT roll_no, technical_rating, communication_rating, problem_solving_rating,
                   time_management_rating, total_rating, interview_ts
            FROM interview_rating
            ORDER BY interview_ts DESC
            LIMIT 200
        """)
        rows = cs.fetchall()
        cols = [desc[0] for desc in cs.description]
        cs.close()
        conn.close()
        df = pd.DataFrame(rows, columns=cols)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Interview Ratings')
        output.seek(0)
        return send_file(output,
                 download_name="interview_ratings.xlsx",
                 as_attachment=True,
                 mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        logger.error(f"Error exporting interview ratings: {str(e)}", exc_info=True)
        return f"Error exporting interview ratings: {e}", 500

@dash_bp.route('/export_student_info')
def export_student_info():
    logger.debug("Export student info endpoint called")
    if "user" not in session or session.get("role") != "recruiter":
        logger.warning("Unauthorized export attempt")
        return redirect(url_for("login"))
    try:
        conn = get_snowflake_connection()
        if not conn:
            raise Exception("Could not connect to Snowflake")
        cs = conn.cursor()
        cs.execute("""
            SELECT student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, interview_ts
            FROM student_info
            ORDER BY interview_ts DESC
            LIMIT 200
        """)
        rows = cs.fetchall()
        cols = [desc[0] for desc in cs.description]
        cs.close()
        conn.close()
        df = pd.DataFrame(rows, columns=cols)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Student Info')
        output.seek(0)
        return send_file(output,
                 download_name="student_info.xlsx",
                 as_attachment=True,
                 mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        logger.error(f"Error exporting student info: {str(e)}", exc_info=True)
        return f"Error exporting student info: {e}", 500

@dash_bp.route('/export_visual_feedback')
def export_visual_feedback():
    logger.debug("Export visual feedback endpoint called")
    if "user" not in session or session.get("role") != "recruiter":
        logger.warning("Unauthorized export attempt")
        return redirect(url_for("login"))
    try:
        conn = get_snowflake_connection()
        if not conn:
            raise Exception("Could not connect to Snowflake")
        cs = conn.cursor()
        cs.execute("""
            SELECT roll_no, professional_appearance, body_language, environment, 
                   distractions, interview_ts
            FROM visual_feedback
            ORDER BY interview_ts DESC
            LIMIT 200
        """)
        rows = cs.fetchall()
        cols = [desc[0] for desc in cs.description]
        cs.close()
        conn.close()
        df = pd.DataFrame(rows, columns=cols)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Visual Feedback')
        output.seek(0)
        return send_file(output,
                 download_name="visual_feedback.xlsx",
                 as_attachment=True,
                 mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        logger.error(f"Error exporting visual feedback: {str(e)}", exc_info=True)
        return f"Error exporting visual feedback: {e}", 500

@dash_bp.route('/view_report/<filename>')
def view_report(filename):
    # Only allow students to view their own reports
    if "user" not in session or session.get("role") != "student":
        return redirect(url_for("auth.login"))
    # Optionally, check that the filename contains the student's roll_no
    roll_no = session["user"]
    if not filename.startswith(f"interview_report_{roll_no}_") or not filename.endswith('.pdf'):
        return "Unauthorized", 403
    return send_from_directory('static/reports', filename)

@dash_bp.route('/reports', endpoint='student_reports')
def student_reports():
    if "user" not in session or session.get("role") != "student":
        return redirect(url_for("auth.login"))
    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("""
            SELECT ir.roll_no, ir.technical_rating, ir.communication_rating, ir.problem_solving_rating,
                   ir.time_management_rating, ir.total_rating, ir.interview_ts,
                   si.course, si.evaluation_date, si.difficulty_level
            FROM interview_rating ir
            JOIN student_info si ON ir.roll_no = si.roll_no AND ir.interview_ts = si.interview_ts
            WHERE ir.roll_no = %s
            ORDER BY ir.interview_ts DESC
        """, (session["user"],))
        reports = cs.fetchall()
        # KPIs: average rating (out of 10), completed interviews
        completed_interviews = len(reports)
        average_rating = round(sum(r[5] for r in reports) / completed_interviews, 1) if completed_interviews > 0 else 0
        cs.close()
        conn.close()
        return render_template("student_reports.html", reports=reports, average_rating=average_rating, completed_interviews=completed_interviews)
    except Exception as e:
        return f"Error loading reports: {e}"

@dash_bp.route('/schedule_interview', methods=['GET', 'POST'])
def schedule_interview():
    if 'user' not in session or session.get('role') != 'recruiter':
        return redirect(url_for('auth.login'))
    conn = get_snowflake_connection()
    cs = conn.cursor()
    cs.execute("""
        SELECT si.student_name, si.roll_no, si.batch_no, si.center, si.course, si.evaluation_date, si.difficulty_level, si.interview_ts, r.email_id
        FROM student_info si
        JOIN REGISTER r ON si.roll_no = r.email_id
        ORDER BY si.interview_ts DESC
        LIMIT 200
    """)
    students = cs.fetchall()
    student_cols = [desc[0].lower() for desc in cs.description]
    cs.close()
    conn.close()
    if request.method == 'POST':
        jd_file = request.files.get('jd_file')
        student_name = request.form.get('student_name')
        roll_no = request.form.get('roll_no')
        email_id = request.form.get('email_id')
        batch_no = request.form.get('batch_no')
        center = request.form.get('center')
        course = request.form.get('course')
        evaluation_date = request.form.get('evaluation_date')
        difficulty_level = request.form.get('difficulty_level')
        if not jd_file or not student_name or not roll_no or not email_id or not batch_no or not center or not course or not evaluation_date or not difficulty_level:
            flash('Please upload a JD and fill all student information fields.', 'danger')
            return render_template('schedule_interview.html')
        # filename = secure_filename(jd_file.filename)
        # jd_text = extract_text_from_file(jd_file)
        # jd_file.save(os.path.join('static/reports', filename))
        filename = secure_filename(jd_file.filename)
        jd_text = extract_text_from_file(jd_file)

        # Ensure the folder exists
        save_dir = os.path.join('static', 'reports')
        os.makedirs(save_dir, exist_ok=True)

        # Save the uploaded file
        jd_file.save(os.path.join(save_dir, filename))

        if not jd_text or not jd_text.strip():
            flash('Could not extract text from the uploaded JD file. Please upload a valid DOCX, TXT, or text-based PDF file (not a scanned image).', 'danger')
            return render_template('schedule_interview.html')
        admin_id = session['user']
        jd_id = insert_jd(jd_text, admin_id)
        # Schedule interview for this student
        interview_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # Insert into new interview table
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("""
            CREATE TABLE IF NOT EXISTS interview (
                student_name TEXT,
                roll_no TEXT,
                email_id TEXT,
                batch_no TEXT,
                center TEXT,
                course TEXT,
                evaluation_date TEXT,
                difficulty_level TEXT,
                interview_ts TIMESTAMP,
                jd_id TEXT,
                status TEXT
            );
        """)
        cs.execute("""
            INSERT INTO interview (student_name, roll_no, email_id, batch_no, center, course, evaluation_date, difficulty_level, language, interview_ts, jd_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (student_name, roll_no, email_id, batch_no, center, course, evaluation_date, difficulty_level, request.form.get('language'), interview_ts, jd_id, 'Scheduled'))
        conn.commit()
        cs.close()
        conn.close()
        # Save to Redis for interview flow
        interview_data = {
            'jd_text': jd_text,
            'jd_id': jd_id,
            'scheduled': True,
            'notified': False,
            'difficulty_level': difficulty_level,  # ENFORCE recruiter-set difficulty
            'language': request.form.get('language')
        }
        save_interview_data(email_id, interview_data)
        flash('Interview scheduled and notification sent!', 'success')
        return render_template('schedule_interview.html', show_modal=True)
    return render_template('schedule_interview.html', students=students, student_cols=student_cols)

@dash_bp.route('/student_interview_notification')
def student_interview_notification():
    if 'user' not in session or session.get('role') != 'student':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    roll_no = session['user']
    interview_data = get_interview_data(roll_no)
    if interview_data and interview_data.get('scheduled'):
        return jsonify({'status': 'scheduled', 'jd_text': interview_data.get('jd_text', '')})
    return jsonify({'status': 'none'})

# Remove or comment out the /scheduled_interview route below to avoid route conflict with interview.py
# @dash_bp.route('/scheduled_interview')
# def scheduled_interview():
#     if 'user' not in session or session.get('role') != 'student':
#         return redirect(url_for('auth.login'))
#     roll_no = session['user']
#     interview_data = get_interview_data(roll_no)
#     interview_completed = False
#     student_info = None
#     student_cols = []
#     status = 'No Interview'
#     # Always fetch student info
#     conn = get_snowflake_connection()
#     cs = conn.cursor()
#     cs.execute("""
#         SELECT student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, interview_ts
#         FROM student_info
#         WHERE roll_no = %s
#         ORDER BY interview_ts DESC
#         LIMIT 1
#     """, (roll_no,))
#     student_info = cs.fetchone()
#     student_cols = [desc[0].replace('_', ' ').title() for desc in cs.description]
#     cs.close()
#     conn.close()
#     jd_text = None
#     if interview_data and interview_data.get('scheduled'):
#         interview_completed = interview_data.get('interview_started') and interview_data.get('end_time')
#         jd_text = interview_data.get('jd_text', '')
#         if interview_completed:
#             status = 'Completed Interview'
#         else:
#             status = 'Scheduled Interview'
#     return render_template('scheduled_interview.html', jd_text=jd_text, interview_completed=interview_completed, student_info=student_info, student_cols=student_cols, status=status)

@dash_bp.route('/recruiter_extract_jd', methods=['POST'])
def recruiter_extract_jd():
    if 'user' not in session or session.get('role') != 'recruiter':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    if 'jd_file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400
    file = request.files['jd_file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No file selected'}), 400
    jd_text = extract_text_from_file(file)
    if not jd_text:
        return jsonify({'status': 'error', 'message': 'Could not extract text from file'}), 400
    return jsonify({'status': 'success', 'jd_text': jd_text})

@dash_bp.route('/performance')
def performance_reports():
    if "user" not in session or session.get("role") != "recruiter":
        return redirect(url_for("auth.login"))
    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("""
            CREATE TABLE IF NOT EXISTS student_performance_report (
                id INTEGER AUTOINCREMENT PRIMARY KEY,
                student_name TEXT,
                roll_no TEXT,
                batch_no TEXT,
                center TEXT,
                course TEXT,
                evaluation_date TEXT,
                difficulty_level TEXT,
                interview_ts TIMESTAMP,
                report TEXT
            );
        """)
        cs.execute("""
            SELECT id, student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, interview_ts, report
            FROM student_performance_report
            ORDER BY interview_ts DESC
            LIMIT 200
        """)
        reports = cs.fetchall()
        cols = [desc[0] for desc in cs.description]
        cs.close()
        conn.close()
        return render_template("performance_reports.html", reports=reports, cols=cols)
    except Exception as e:
        return f"Error loading performance reports: {e}"

@dash_bp.route('/performance/view/<int:report_id>')
def view_performance_report(report_id):
    if "user" not in session or session.get("role") != "recruiter":
        return redirect(url_for("auth.login"))
    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("SELECT report FROM student_performance_report WHERE id = %s", (report_id,))
        row = cs.fetchone()
        cs.close()
        conn.close()
        if not row:
            return "Report not found", 404
        return render_template("view_performance_report.html", report=row[0])
    except Exception as e:
        return f"Error loading report: {e}"





@dash_bp.route('/performance/download/<int:report_id>')
def download_performance_report(report_id):
    if "user" not in session or session.get("role") != "recruiter":
        return redirect(url_for("auth.login"))
    try:
        try:
            import pdfkit
            conn = get_snowflake_connection()
            cs = conn.cursor()
            cs.execute("SELECT report FROM student_performance_report WHERE id = %s", (report_id,))
            row = cs.fetchone()
            cs.close()
            conn.close()
            if not row:
                return "Report not found", 404
            # Use the same template as the view button for PDF export
            report_html = render_template("view_performance_report.html", report=row[0], pdf_export=True)
            pdf = pdfkit.from_string(report_html, False)
            return send_file(
                io.BytesIO(pdf),
                download_name=f"performance_report_{report_id}.pdf",
                as_attachment=True,
                mimetype='application/pdf'
            )
        except (ImportError, OSError):
            conn = get_snowflake_connection()
            cs = conn.cursor()
            cs.execute("SELECT report FROM student_performance_report WHERE id = %s", (report_id,))
            row = cs.fetchone()
            cs.close()
            conn.close()
            if not row:
                return "Report not found", 404
            report_html = render_template("view_performance_report.html", report=row[0], pdf_export=True)
            output = io.BytesIO(report_html.encode('utf-8'))
            return send_file(output, download_name=f"performance_report_{report_id}.html", as_attachment=True, mimetype='text/html')
    except Exception as e:
        return f"Error downloading report: {e}"

@dash_bp.route('/student_performance')
def student_performance():
    if "user" not in session or session.get("role") != "student":
        return redirect(url_for("auth.login"))
    try:
        roll_no = session["user"]
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("""
            SELECT id, student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, interview_ts, report
            FROM student_performance_report
            WHERE roll_no = %s
            ORDER BY interview_ts DESC
            LIMIT 200
        """, (roll_no,))
        reports = cs.fetchall()
        cols = [desc[0] for desc in cs.description]
        cs.close()
        conn.close()
        return render_template("student_performance.html", reports=reports, cols=cols)
    except Exception as e:
        return f"Error loading your performance reports: {e}"




@dash_bp.route('/student_performance/view/<int:report_id>')
def student_view_performance_report(report_id):
    if "user" not in session or session.get("role") != "student":
        return redirect(url_for("auth.login"))

    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("SELECT report FROM student_performance_report WHERE id = %s", (report_id,))
        row = cs.fetchone()
        cs.close()
        conn.close()

        if not row:
            return "<div class='text-danger'>Report not found.</div>", 404

        # Render the report using the same template as recruiter
        return render_template("view_performance_report.html", report=row[0])

    except Exception as e:
        return f"<div class='text-danger'>Error loading report: {e}</div>", 500

@dash_bp.route('/student_performance/download/<int:report_id>')
def student_download_performance_report(report_id):
    if "user" not in session or session.get("role") != "student":
        return redirect(url_for("auth.login"))
    try:
        try:
            import pdfkit
            conn = get_snowflake_connection()
            cs = conn.cursor()
            cs.execute("SELECT report FROM student_performance_report WHERE id = %s", (report_id,))
            row = cs.fetchone()
            cs.close()
            conn.close()
            if not row:
                return "Report not found", 404
            report_html = render_template("view_performance_report.html", report=row[0], pdf_export=True)
            pdf = pdfkit.from_string(report_html, False)
            return send_file(
                io.BytesIO(pdf),
                download_name=f"performance_report_{report_id}.pdf",
                as_attachment=True,
                mimetype='application/pdf'
            )
        except (ImportError, OSError):
            conn = get_snowflake_connection()
            cs = conn.cursor()
            cs.execute("SELECT report FROM student_performance_report WHERE id = %s", (report_id,))
            row = cs.fetchone()
            cs.close()
            conn.close()
            if not row:
                return "Report not found", 404
            report_html = render_template("view_performance_report.html", report=row[0], pdf_export=True)
            output = io.BytesIO(report_html.encode('utf-8'))
            return send_file(output, download_name=f"performance_report_{report_id}.html", as_attachment=True, mimetype='text/html')
    except Exception as e:
        return f"Error downloading report: {e}"


dashboard_bp = dash_bp