from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for, send_file, flash
from backend.services.redis_service import get_interview_data, save_interview_data, clear_interview_data, init_interview_data
from backend.services.monitoring_service import interview_monitor, system_monitor
from backend.services.snowflake_service import get_snowflake_connection
from backend.services.openai_service import (
    generate_questions_from_jd,
    generate_encouragement_prompt,
    evaluate_response,
    generate_interview_report,
    translate_text
)
from backend.services.audio_service import text_to_speech, process_audio_from_base64
from backend.services.visual_service import process_frame_for_gpt4v, analyze_visual_response
from backend.utils.file_utils import extract_text_from_file, save_conversation_to_file, load_conversation_from_file
from config import Config
import re
import logging
from datetime import datetime, timezone, timedelta
from collections import Counter
import os
from werkzeug.utils import secure_filename
import requests
import json
from werkzeug.utils import secure_filename
from deepgram.utils import verboselogs
from deepgram import DeepgramClient,PrerecordedOptions,FileSource

logger = logging.getLogger(__name__)
interview_bp = Blueprint('interview', __name__)
_interview_has_language = None
DEEPGRAM_API = Config.DEEPGRAM_STT
print("Deepgram API Key:", DEEPGRAM_API)

deepgram = DeepgramClient(DEEPGRAM_API)

def _sanitize_question_text(text):
    """Remove leading markdown like **Question 1** / Question 1: and stray asterisks."""
    if not text:
        return text
    
    # Convert to string and strip whitespace
    cleaned = str(text).strip()
    
    # AGGRESSIVE asterisk removal - handle all possible cases
    
    # Normalize non-breaking spaces
    cleaned = cleaned.replace('\u00A0', ' ')
    # 1. Remove any asterisks/bullets/hyphens at the very beginning (like "** ", "- ", "• ")
    cleaned = re.sub(r'^[\s\*\-\u2022\u2023\u25E6\u2043\u2219]+', '', cleaned)
    
    # 2. Remove any asterisks at the very end
    cleaned = re.sub(r'[\s\u00A0]*\*+[\s\u00A0]*$', '', cleaned)
    
    # 3. Remove all markdown asterisks from the entire text
    # This handles cases like "**Question 1:** Tell us about yourself" or "*What is your experience?*"
    cleaned = re.sub(r'\*+([^*]*?)\*+', r'\1', cleaned)
    
    # 4. Remove any remaining standalone asterisks (like "** " in the middle)
    cleaned = re.sub(r'[\s\u00A0]+\*+[\s\u00A0]+', ' ', cleaned)
    
    # 5. Drop a leading line that is just a Question header (possibly bolded)
    lines = cleaned.splitlines()
    while lines and re.match(r"^\s*\**\s*question\s*\d+\s*\**\s*:?\s*$", lines[0], re.IGNORECASE):
        lines.pop(0)
    cleaned = "\n".join(lines).strip()
    
    # 6. Remove inline prefix like **Question 1:** or Question 1:
    cleaned = re.sub(r"^\s*\**\s*question\s*\d+\s*\**\s*:?\s*", "", cleaned, flags=re.IGNORECASE)
    
    # 7. Remove any remaining question prefixes with numbers
    cleaned = re.sub(r"^\s*\**\s*question\s*\d+\s*\**\s*:?\s*", "", cleaned, flags=re.IGNORECASE)
    
    # 8. Remove surrounding asterisks if entire text is wrapped
    cleaned = re.sub(r"^\s*\*{1,3}\s*(.*?)\s*\*{1,3}\s*$", r"\1", cleaned)
    
    # 9. Final cleanup - remove any remaining asterisks/bullets at the beginning or end
    cleaned = re.sub(r'^[\s\*\-\u2022\u2023\u25E6\u2043\u2219]+', '', cleaned)
    cleaned = re.sub(r'[\s\*\-\u2022\u2023\u25E6\u2043\u2219]+$', '', cleaned)
    
    # 10. Clean up any double spaces that might have been created
    cleaned = re.sub(r'[\s\u00A0]+', ' ', cleaned)
    
    return cleaned.strip()

def interview_table_has_language():
    global _interview_has_language
    if _interview_has_language is not None:
        return _interview_has_language
    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'INTERVIEW'
              AND table_schema = CURRENT_SCHEMA()
              AND UPPER(column_name) = 'LANGUAGE'
            LIMIT 1
        """)
        _interview_has_language = cs.fetchone() is not None
        cs.close()
        conn.close()
    except Exception:
        _interview_has_language = False
    return _interview_has_language


# Interview duration in seconds (15 minutes)
INTERVIEW_DURATION = 900
# Pause threshold in seconds (15 seconds)
PAUSE_THRESHOLD = 10

def get_jd_text(jd_id):
    conn = get_snowflake_connection()
    cs = conn.cursor()
    cs.execute("SELECT jd_text FROM job_descriptions WHERE jd_id = %s", (jd_id,))
    row = cs.fetchone()
    cs.close()
    conn.close()
    return row[0] if row else None

def insert_jd(jd_text, admin_id):
    conn = get_snowflake_connection()
    cs = conn.cursor()
    # Ensure table exists
    cs.execute(
        """
        CREATE TABLE IF NOT EXISTS job_descriptions (
            jd_id INTEGER AUTOINCREMENT PRIMARY KEY,
            jd_text TEXT,
            admin_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Insert the JD and try to fetch jd_id directly
    jd_id = None
    try:
        cs.execute(
            "INSERT INTO job_descriptions (jd_text, admin_id) VALUES (%s, %s) RETURNING jd_id",
            (jd_text, admin_id)
        )
        row = cs.fetchone()
        if row:
            jd_id = row[0]
    except Exception:
        # Fallback path if RETURNING is not supported
        cs.execute(
            "INSERT INTO job_descriptions (jd_text, admin_id) VALUES (%s, %s)",
            (jd_text, admin_id)
        )
        try:
            cs.execute(
                "SELECT jd_id FROM job_descriptions WHERE jd_text = %s AND admin_id = %s ORDER BY jd_id DESC LIMIT 1",
                (jd_text, admin_id)
            )
            row = cs.fetchone()
            jd_id = row[0] if row else None
            if jd_id is None:
                cs.execute("SELECT MAX(jd_id) FROM job_descriptions WHERE admin_id = %s", (admin_id,))
                row2 = cs.fetchone()
                jd_id = row2[0] if row2 and row2[0] is not None else None
        except Exception:
            try:
                cs.execute("SELECT MAX(jd_id) FROM job_descriptions WHERE admin_id = %s", (admin_id,))
                row3 = cs.fetchone()
                jd_id = row3[0] if row3 and row3[0] is not None else None
            except Exception:
                jd_id = None
    conn.commit()
    cs.close()
    conn.close()
    print(f"Inserted JD for admin {admin_id}, got jd_id: {jd_id}")
    return jd_id

@interview_bp.route("/interview")
def interview_bot():
    logger.debug("Interview bot route accessed")
    if "user" not in session:
        logger.warning("Unauthorized access to interview bot")
        return redirect(url_for("login"))
    email_id = session.get("user")
    interview_data = get_interview_data(email_id)
    if not interview_data:
        interview_data = init_interview_data()
        save_interview_data(email_id, interview_data)
    logger.debug("Initialized interview session data")
    return render_template("interview_bot.html",language=interview_data.get('language', 'english'))

@interview_bp.route('/transcribe', methods=['POST'])
def transcribe():
    """
    Accepts an audio file via multipart/form-data under 'audio' and returns a transcript.
    """
    print("Transcription endpoint called")

    try:
        if 'audio' not in request.files:
            return jsonify({'error': 'no audio file'}), 400

        audio_file = request.files['audio']
        language = request.form.get('language', 'english')
        if language.lower() == 'hindi':
            lang = 'hi'
        elif language.lower() == 'english':
            lang = 'en'
        else:
            lang = 'multi'
        bufferdata = audio_file.read()
        payload: FileSource = {
            "buffer": bufferdata,
            "mimetype": audio_file.mimetype,
        }
        options = PrerecordedOptions(model="nova-2", smart_format=True,language=lang)
        response = deepgram.listen.rest.v("1").transcribe_file(payload, options)
        print(response)

        transcript = (
            response['results']['channels'][0]['alternatives'][0]['transcript']
        )
        return jsonify({'transcript': transcript})

    except Exception as e:
        print(f"Error in transcription: {e}")
        return jsonify({'error': str(e)}), 500

@interview_bp.route('/start_interview', methods=['POST'])
def start_interview():
    logger.debug("Start interview endpoint called")
    system_monitor.increment_request()
    
    if "user" not in session:
        logger.warning("Unauthenticated start interview attempt")
        system_monitor.increment_error()
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    
    email_id = session.get("user")
    interview_data = get_interview_data(email_id) or init_interview_data()
    
    # Log the current interview data for debugging
    logger.debug(f"Current interview data: {interview_data}")
    
    # Ensure jd_id or jd_text exists; if missing, try DB; else error
    if not interview_data.get('jd_id') and not (interview_data.get('jd_text') and interview_data['jd_text'].strip()):
        try:
            conn = get_snowflake_connection()
            cs = conn.cursor()
            if interview_table_has_language():
                cs.execute(
                    """
                        SELECT jd_id, interview_ts, difficulty_level, language
                        FROM interview
                        WHERE email_id = %s
                        ORDER BY interview_ts DESC
                        LIMIT 1
                    """,
                    (email_id,)
                )
            else:
                cs.execute(
                    """
                        SELECT jd_id, interview_ts, difficulty_level
                        FROM interview
                        WHERE email_id = %s
                        ORDER BY interview_ts DESC
                        LIMIT 1
                    """,
                    (email_id,)
                )
            row = cs.fetchone()
            cs.close()
            conn.close()
            if row and row[0]:
                interview_data['jd_id'] = row[0]
                interview_data['interview_ts'] = row[1] if len(row) > 1 else None
                if len(row) > 2 and row[2]:
                    interview_data['difficulty_level'] = row[2]
                if interview_table_has_language() and len(row) > 3 and row[3]:
                    interview_data['language'] = row[3]
                save_interview_data(email_id, interview_data)
            else:
                # Final DB fallback: take latest JD in system
                try:
                    conn2 = get_snowflake_connection()
                    cs2 = conn2.cursor()
                    cs2.execute("SELECT MAX(jd_id) FROM job_descriptions")
                    r = cs2.fetchone()
                    cs2.close()
                    conn2.close()
                    if r and r[0]:
                        interview_data['jd_id'] = r[0]
                        save_interview_data(email_id, interview_data)
                except Exception:
                    pass
                # As a final fallback, try to reuse jd_text already cached in session (if any)
                if not (interview_data.get('jd_text') and interview_data['jd_text'].strip()) and not interview_data.get('jd_id'):
                    logger.error('No JD found in DB or session for this user')
                    return jsonify({"status": "error", "message": "No Job Description (JD) found for this interview. Please contact your recruiter."}), 400
        except Exception as e:
            logger.error(f"Error fetching JD from interview table: {e}")
            if not (interview_data.get('jd_text') and interview_data['jd_text'].strip()):
                return jsonify({"status": "error", "message": "No Job Description (JD) found for this interview. Please contact your recruiter."}), 400
    
    # If we have a jd_id, fetch jd_text from DB; otherwise, rely on cached jd_text
    if interview_data.get('jd_id'):
        interview_data['jd_text'] = get_jd_text(interview_data['jd_id'])
        if not interview_data['jd_text']:
            # As a fallback, try latest jd_id for the admin (recruiter) that scheduled
            try:
                conn = get_snowflake_connection()
                cs = conn.cursor()
                cs.execute("SELECT MAX(jd_id) FROM job_descriptions")
                row = cs.fetchone()
                cs.close()
                conn.close()
                if row and row[0]:
                    interview_data['jd_id'] = row[0]
                    interview_data['jd_text'] = get_jd_text(interview_data['jd_id'])
                    save_interview_data(email_id, interview_data)
            except Exception:
                pass
    # Validate jd_text presence finally
    if not (interview_data.get('jd_text') and interview_data['jd_text'].strip()):
        logger.error('No JD text available to start interview')
        return jsonify({"status": "error", "message": "No Job Description (JD) found for this interview. Please contact your recruiter."}), 400
    
    if not interview_data.get('jd_text'):
        logger.error(f'No JD text found for jd_id: {interview_data.get("jd_id")}')
        return jsonify({"status": "error", "message": "No Job Description (JD) found for this interview. Please contact your recruiter."}), 400
    
    jd_name = interview_data['jd_text'][:30] + ('...' if len(interview_data['jd_text']) > 30 else '')
    
    interview_data['start_time'] = datetime.now(timezone.utc)
    interview_data['last_activity_time'] = datetime.now(timezone.utc)
    
    # Track interview start in monitoring system
    interview_monitor.start_interview(email_id, session.get('session_id', 'unknown'), interview_data)
    # Always reset state when starting interview to avoid carryover from previous sessions
    interview_data['current_question'] = 0
    interview_data['answers'] = []
    interview_data['ratings'] = []
    interview_data['conversation_history'] = []
    interview_data['current_answer'] = ""
    interview_data['visual_feedback'] = []
    interview_data['visual_feedback_data'] = []
    interview_data['interview_time_used'] = 0
    interview_data['end_time'] = None
    interview_data['report_generated'] = False
    # ENFORCE recruiter-set difficulty level
    # If difficulty_level is not set, fetch from scheduled interview record
    if not interview_data.get('difficulty_level'):
        try:
            conn = get_snowflake_connection()
            cs = conn.cursor()
            # If interview_ts is missing, get the latest scheduled interview
            if not interview_data.get('interview_ts'):
                cs.execute("""
                    SELECT difficulty_level, interview_ts FROM interview
                    WHERE email_id = %s
                    ORDER BY interview_ts DESC LIMIT 1
                """, (email_id,))
                row = cs.fetchone()
                if row:
                    interview_data['difficulty_level'] = row[0]
                    interview_data['interview_ts'] = row[1]
            else:
                cs.execute("""
                    SELECT difficulty_level FROM interview
                    WHERE email_id = %s
                      AND interview_ts = COALESCE(
                        TRY_TO_TIMESTAMP_TZ(%s, 'YYYY-MM-DD"T"HH24:MI:SS.FF TZHTZM')::TIMESTAMP_NTZ,
                        TRY_TO_TIMESTAMP(%s, 'YYYY-MM-DD HH24:MI:SS')
                      )
                """, (email_id, interview_data.get('interview_ts'), interview_data.get('interview_ts')))
                row = cs.fetchone()
                if row and row[0]:
                    interview_data['difficulty_level'] = row[0]
            cs.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error fetching difficulty_level: {e}")
    # If still not set, raise error (do NOT default to medium)
    if not interview_data.get('difficulty_level'):
        logger.error('No difficulty level set for this interview. Recruiter must specify difficulty when scheduling.')
        return jsonify({"status": "error", "message": "No difficulty level set for this interview. Please contact your recruiter."}), 400
    logger.debug(f"Starting interview with difficulty level: {interview_data['difficulty_level']}")
    try:
        questions = generate_questions_from_jd(
            interview_data['jd_text'],
            interview_data['difficulty_level'],
            interview_data.get('student_info', {}).get('roll_no', None),
            interview_data.get('language', 'english')
        )
        # Ensure we have exactly 5 distinct, non-empty questions
        fallback_pool = [
            "Tell us about yourself.",
            "What programming languages do you know?",
            "Explain a basic project you've worked on.",
            "Describe a challenge you faced and how you resolved it.",
            "Where do you see yourself improving technically?"
        ]
        sanitized = []
        seen = set()
        for q in questions or []:
            qn = _sanitize_question_text((q or "").strip())
            if not qn:
                continue
            if qn in seen:
                continue
            sanitized.append(qn)
            seen.add(qn)
            if len(sanitized) == 5:
                break
        # Pad with fallback questions until we have 5
        for fq in fallback_pool:
            if len(sanitized) >= 5:
                break
            if fq not in seen:
                sanitized.append(fq)
                seen.add(fq)
        if not sanitized:
            logger.error("No questions generated, using fallback questions.")
            sanitized = fallback_pool
        interview_data['questions'] = sanitized[:5]
        interview_data['interview_started'] = True
        save_interview_data(email_id, interview_data)
        logger.info(f"Interview started with {len(questions)} questions")
        return jsonify({
            "status": "started",
            "total_questions": len(interview_data['questions']),
            "welcome_message": f"Welcome to the interview based on JD: {jd_name}. Let's begin with the first question.",
            "jd_name": jd_name,
            "difficulty_level": interview_data.get('difficulty_level')
        })
    except Exception as e:
        logger.error(f"Error in start_interview: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@interview_bp.route('/upload_jd', methods=['POST'])
def upload_jd():
    logger.debug("JD upload endpoint called")
    if "user" not in session:
        logger.warning("Unauthenticated JD upload attempt")
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    if 'jd_file' not in request.files:
        logger.warning("No file in JD upload request")
        return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files['jd_file']
    if file.filename == '':
        logger.warning("Empty filename in JD upload")
        return jsonify({"status": "error", "message": "No file selected"}), 400
    logger.debug(f"Processing JD file: {file.filename}")
    jd_text = extract_text_from_file(file)
    if not jd_text or not jd_text.strip():
        flash('Could not extract text from the uploaded JD file. Please upload a valid DOCX, TXT, or text-based PDF file (not a scanned image).', 'danger')
        return render_template('schedule_interview.html')
    logger.info(f"Successfully extracted JD text (length: {len(jd_text)} characters)")
    admin_id = session['user']
    jd_id = insert_jd(jd_text, admin_id)
    print(f"Inserted JD: {jd_text[:30]}... by admin {admin_id}, got jd_id: {jd_id}")
    return jsonify({
        "status": "success",
        "jd_text": jd_text,
        "jd_id": jd_id
    })

@interview_bp.route('/get_question', methods=['GET'])
def get_question():
    logger.debug("Get question endpoint called")
    try:
        if "user" not in session:
            logger.warning("Unauthenticated get question attempt")
            return jsonify({"status": "error", "message": "Not authenticated"}), 401
        email_id = session.get("user")
        interview_data = get_interview_data(email_id)
        if not interview_data or not interview_data.get('interview_started', False):
            logger.warning("Attempt to get question before interview started")
            return jsonify({"status": "not_started"})
        # If we're currently waiting for the user's answer, re-serve the same question
        if interview_data.get('waiting_for_answer', False):
            questions = interview_data.get('questions', [])
            idx = min(interview_data.get('current_question', 0), max(0, len(questions) - 1))
            if not questions:
                return jsonify({"status": "error", "message": "No questions available"}), 400
            current_q = questions[idx]
            # Final safety check to ensure no asterisks remain
            current_q = _sanitize_question_text(current_q)
            sel_lang = (interview_data.get('language') or 'english').lower()
            tts_lang = 'hi' if sel_lang in {'hindi', 'english+hindi', 'bilingual', 'hinglish', 'en+hi'} else 'en'
            audio_data = text_to_speech(current_q, tts_lang)
            return jsonify({
                "status": "success",
                "question": current_q,
                "audio": audio_data,
                "question_number": idx + 1,
                "total_questions": len(questions),
                "difficulty_level": interview_data.get('difficulty_level', 'medium')
            })
        # --- Deduplication logic start ---
        asked_questions = set()
        for entry in interview_data.get('conversation_history', []):
            if entry.get('speaker') == 'bot' and entry.get('text'):
                asked_questions.add(entry['text'].strip())
        questions = interview_data['questions']
        idx = interview_data['current_question']
        total_questions = len(questions)
        # Find the next unique question
        next_unique_idx = idx
        while next_unique_idx < total_questions and questions[next_unique_idx].strip() in asked_questions:
            next_unique_idx += 1
        if next_unique_idx >= total_questions:
            logger.info("All unique questions have been asked")
            return jsonify({"status": "completed"})
        # Update current_question pointer if we skipped duplicates
        interview_data['current_question'] = next_unique_idx
        current_q = questions[next_unique_idx]
        # Final safety check to ensure no asterisks remain
        current_q = _sanitize_question_text(current_q)
        interview_data['conversation_history'].append({"speaker": "bot", "text": current_q})
        interview_data['current_answer'] = ""
        interview_data['waiting_for_answer'] = True
        roll_no = None
        if 'student_info' in interview_data and interview_data['student_info']:
            roll_no = interview_data['student_info'].get('roll_no')
        # Save per-interview conversation record (ensure question is clean before saving)
        clean_question_for_save = _sanitize_question_text(current_q)
        save_conversation_to_file([{ "speaker": "bot", "text": clean_question_for_save }], roll_no, interview_data.get('interview_ts'))
        interview_data['last_activity_time'] = datetime.now(timezone.utc)
        save_interview_data(email_id, interview_data)
        logger.debug(f"Question {interview_data['current_question']}: {current_q[:50]}...")
        # Determine TTS language
        sel_lang = (interview_data.get('language') or 'english').lower()
        tts_lang = 'en'
        if sel_lang == 'hindi':
            tts_lang = 'hi'
        elif sel_lang in {'english+hindi', 'bilingual', 'hinglish', 'en+hi'}:
            # Prefer Hindi audio for bilingual to ensure Hindi-speaking candidates can follow
            tts_lang = 'hi'
        audio_data = text_to_speech(current_q, tts_lang)
        return jsonify({
            "status": "success",
            "question": current_q,
            "audio": audio_data,
            "question_number": interview_data['current_question'] + 1,
            "total_questions": len(interview_data['questions']),
            "difficulty_level": interview_data.get('difficulty_level', 'medium')
        })
        # --- Deduplication logic end ---
    except Exception as e:
        logger.error(f"Error in get_question: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@interview_bp.route('/process_answer', methods=['POST'])
def process_answer():
    logger.debug("Process answer endpoint called")
    system_monitor.increment_request()
    if "user" not in session:
        logger.warning("Unauthenticated process answer attempt")
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    email_id = session.get("user")
    interview_data = get_interview_data(email_id)
    if not interview_data:
        interview_data = init_interview_data()
        interview_data['interview_started'] = True
        interview_data['current_question'] = 0
        interview_data['questions'] = []
        interview_data['conversation_history'] = []
        interview_data['answers'] = []
        interview_data['current_answer'] = ""
        interview_data['waiting_for_answer'] = False
        interview_data['interview_time_used'] = 0
    try:
        if not interview_data.get('interview_started', False):
            logger.warning("Attempt to process answer before interview started")
            return jsonify({"status": "error", "message": "Interview not started"}), 400
        data = request.get_json()
        answer = data.get('answer', '').strip()
        frame_data = data.get('frame', None)
        audio_data = data.get('audio', None)
        is_final = data.get('is_final', False)
        speaking_time = data.get('speaking_time', 0)
        logger.debug(f"Processing answer (is_final: {is_final}, speaking_time: {speaking_time}s)")
        logger.debug(f"Answer text length: {len(answer)} characters")
        interview_data['interview_time_used'] += speaking_time
        start_time = interview_data.get('start_time')
        if start_time:
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time)
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            if elapsed >= INTERVIEW_DURATION:
                logger.info("Interview duration limit reached (elapsed time)")
                interview_data['end_time'] = datetime.now(timezone.utc)
                save_interview_data(email_id, interview_data)
                return jsonify({
                    "status": "interview_complete",
                    "message": "Interview duration limit reached"
                })
        if not is_final:
            interview_data['current_answer'] = answer
            save_interview_data(email_id, interview_data)
            return jsonify({
                "status": "answer_accumulated",
                "remaining_time": max(0, INTERVIEW_DURATION - interview_data['interview_time_used'])
            })
        if not answer and interview_data['current_answer']:
            answer = interview_data['current_answer']
        if not answer:
            logger.warning("Empty answer received")
            return jsonify({"status": "error", "message": "Empty answer"}), 400
        if audio_data:
            try:
                logger.debug("Processing audio data with VAD")
                has_speech, speech_ratio = process_audio_from_base64(audio_data)
                interview_data['speech_detected'] = has_speech
                interview_data['last_speech_time'] = datetime.now(timezone.utc) if has_speech else None
                logger.debug(f"Speech detection - has_speech: {has_speech}, ratio: {speech_ratio:.2f}")
            except Exception as e:
                logger.error(f"Error processing audio with VAD: {str(e)}", exc_info=True)

        current_question_index = interview_data.get('current_question', 0)
        questions = interview_data.get('questions', [])
        if current_question_index < len(questions):
            current_question = questions[current_question_index]
        else:
            current_question = "Follow-up question"

        # Translate answer for transcript if needed
        lang_pref = (interview_data.get('language') or 'english').lower()
        transcript_answer = answer
        if lang_pref in {'hindi', 'english+hindi', 'bilingual', 'hinglish', 'en+hi'}:
            try:
                transcript_answer = translate_text(answer, 'hindi')
            except Exception:
                transcript_answer = answer

        interview_data['answers'].append(transcript_answer)
        interview_data['conversation_history'].append({"speaker": "user", "text": transcript_answer})
        interview_data['current_answer'] = ""
        interview_data['waiting_for_answer'] = False
        interview_data['current_question'] += 1

        roll_no = interview_data.get('student_info', {}).get('roll_no')

        # Save per-interview conversation record
        save_conversation_to_file([{ "speaker": "user", "text": transcript_answer }], roll_no, interview_data.get('interview_ts'))

        interview_data['last_activity_time'] = datetime.now(timezone.utc)
        
        # Update interview activity in monitoring system
        interview_monitor.update_interview_activity(email_id, interview_data['current_question'])

        # Process visual feedback if needed - ENHANCED WITH CANDIDATE CONTEXT
        visual_feedback = None
        current_time = datetime.now().timestamp()
        if Config.ENABLE_VISUAL_ANALYSIS and frame_data and (current_time - interview_data.get('last_frame_time', 0)) > 3:
            try:
                logger.debug("Processing frame data with candidate context")
                import base64
                import numpy as np
                import cv2
                frame_bytes = base64.b64decode(frame_data.split(',')[1])
                frame_array = np.frombuffer(frame_bytes, dtype=np.uint8)
                frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
                if frame is not None:
                    frame_base64 = process_frame_for_gpt4v(frame)
                    
                    # Pass candidate information for context - ENHANCED
                    candidate_info = interview_data.get('student_info', {})
                    if not candidate_info and email_id:
                        # Create basic candidate info from email if student_info not available
                        candidate_info = {
                            'name': email_id.split('@')[0].replace('.', ' ').title(),
                            'roll_no': email_id,
                            'email': email_id
                        }
                    
                    visual_feedback = analyze_visual_response(
                        frame_base64,
                        interview_data['conversation_history'][-3:],
                        candidate_info  # Pass candidate context for unique analysis
                    )
                    
                    if visual_feedback:
                        interview_data['visual_feedback'].append(visual_feedback)
                        interview_data['visual_feedback_data'].append({
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "question_number": current_question_index + 1,
                            "question": current_question,
                            "feedback": visual_feedback,
                            "candidate_info": candidate_info  # Store candidate context
                        })
                        interview_data['last_frame_time'] = current_time
                    logger.debug(f"Visual feedback with candidate context: {visual_feedback}")
            except Exception as e:
                logger.error(f"Error processing frame with candidate context: {str(e)}", exc_info=True)

        # Evaluate the answer
        rating = evaluate_response(
            answer,
            current_question,
            interview_data.get('difficulty_level', 'medium'),
            visual_feedback
        )
        interview_data['ratings'].append(rating)
        logger.debug(f"Response rating: {rating}")

        # Save updated interview data
        save_interview_data(email_id, interview_data)
        
        # Update interview status to 'Completed' if all questions are answered
        if interview_data['current_question'] >= len(interview_data['questions']):
            interview_data['end_time'] = datetime.now(timezone.utc)
            save_interview_data(email_id, interview_data)
            
            # Track interview completion in monitoring system
            interview_monitor.end_interview(email_id)
            
            try:
                conn = get_snowflake_connection()
                cs = conn.cursor()
                # Update status to 'Completed' using both email_id and interview_ts
                cs.execute("""
                    UPDATE interview 
                    SET status = 'Completed'
                    WHERE email_id = %s AND interview_ts = TRY_TO_TIMESTAMP(%s, 'YYYY-MM-DD HH24:MI:SS')
                """, (email_id, interview_data.get('interview_ts')))
                conn.commit()
                cs.close()
                conn.close()
                logger.info(f"Updated interview status to Completed for {email_id}")
            except Exception as e:
                logger.error(f"Error updating interview status to Completed: {e}")

        return jsonify({
            "status": "answer_processed",
            "current_question": interview_data['current_question'],
            "total_questions": len(interview_data['questions']),
            "interview_complete": interview_data['current_question'] >= len(interview_data['questions']),
            "remaining_time": max(0, INTERVIEW_DURATION - interview_data['interview_time_used'])
        })
    except Exception as e:
        logger.error(f"Error in process_answer: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@interview_bp.route('/check_speech', methods=['POST'])
def check_speech():
    logger.debug("Check speech endpoint called")
    if "user" not in session:
        logger.warning("Unauthenticated check speech attempt")
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    email_id = session.get("user")
    interview_data = get_interview_data(email_id)
    if not interview_data['interview_started']:
        logger.warning("Attempt to check speech before interview started")
        return jsonify({"status": "not_started"})
    data = request.get_json()
    audio_data = data.get('audio', None)
    if not audio_data:
        logger.warning("No audio data in check speech request")
        return jsonify({"status": "error", "message": "No audio data"}), 400
    try:
        logger.debug("Checking speech in audio data")
        has_speech, speech_ratio = process_audio_from_base64(audio_data)
        interview_data['speech_detected'] = has_speech
        interview_data['last_speech_time'] = datetime.now(timezone.utc) if has_speech else None
        save_interview_data(email_id, interview_data)
        speech_ended = False
        silence_duration = 0
        if interview_data['last_speech_time']:
            silence_duration = (datetime.now(timezone.utc) - interview_data['last_speech_time']).total_seconds()
            speech_ended = silence_duration > 40
        logger.debug(f"Speech detection - has_speech: {has_speech}, ratio: {speech_ratio:.2f}, silence_duration: {silence_duration:.1f}s, speech_ended: {speech_ended}")
        return jsonify({
            "status": "success",
            "speech_detected": has_speech,
            "speech_ratio": speech_ratio,
            "speech_ended": speech_ended,
            "silence_duration": silence_duration if has_speech else 0
        })
    except Exception as e:
        logger.error(f"Error checking speech: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@interview_bp.route('/check_pause', methods=['GET'])
def check_pause():
    logger.debug("Check pause endpoint called")
    try:
        if "user" not in session:
            logger.warning("Unauthenticated check pause attempt")
            return jsonify({"status": "error", "message": "Not authenticated"}), 401
        email_id = session.get("user")
        interview_data = get_interview_data(email_id)
        if not interview_data.get('interview_started', False):
            logger.warning("Attempt to check pause before interview started")
            return jsonify({"status": "not_started"})
        if not interview_data.get('waiting_for_answer', False):
            logger.debug("Not waiting for answer, skip pause check")
            return jsonify({"status": "active"})
        # Remove pause detection and always return active
        return jsonify({"status": "active"})
    except Exception as e:
        logger.error(f"Error in check_pause: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@interview_bp.route('/generate_report', methods=['GET'])
def generate_report():
    logger.debug("Generate report endpoint called")
    if "user" not in session:
        logger.warning("Unauthenticated generate report attempt")
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    email_id = session.get("user")
    interview_data = get_interview_data(email_id)
    if not interview_data['interview_started']:
        logger.warning("Attempt to generate report before interview started")
        return jsonify({"status": "error", "message": "Interview not started"}), 400
    if interview_data.get('report_generated', False):
        roll_no = interview_data.get('student_info', {}).get('roll_no')
        if roll_no:
            interview_ts = interview_data.get('interview_ts')
            conversation_history = load_conversation_from_file(roll_no, interview_ts)
            # Sanitize any remaining asterisks in the conversation history
            for entry in conversation_history:
                if 'text' in entry:
                    entry['text'] = _sanitize_question_text(entry['text'])
                if 'question' in entry:
                    entry['question'] = _sanitize_question_text(entry['question'])
            interview_data['conversation_history'] = conversation_history
        report = generate_interview_report(interview_data)
        return jsonify({
            "status": "success",
            "report": report['report_html'],
            "ratings": report['category_ratings'],
            # "voice_feedback": report['voice_feedback'],
            # "voice_audio": report['voice_audio'],
            "status_class": report['status_class'],
            "visual_feedback": report.get('visual_feedback', {})
        })
    if not interview_data['end_time']:
        interview_data['end_time'] = datetime.now(timezone.utc)
        save_interview_data(email_id, interview_data)

    # Fetch student/interview info from interview table
    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        if interview_table_has_language():
            cs.execute("""
                SELECT student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, language
                FROM interview
                WHERE email_id = %s AND interview_ts = TRY_TO_TIMESTAMP_TZ(%s, 'YYYY-MM-DD"T"HH24:MI:SS.FF TZHTZM')::TIMESTAMP_NTZ
                ORDER BY interview_ts DESC LIMIT 1
            """, (email_id, interview_data.get('interview_ts')))
        else:
            cs.execute("""
                SELECT student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level
                FROM interview
                WHERE email_id = %s AND interview_ts = TRY_TO_TIMESTAMP_TZ(%s, 'YYYY-MM-DD"T"HH24:MI:SS.FF TZHTZM')::TIMESTAMP_NTZ
                ORDER BY interview_ts DESC LIMIT 1
            """, (email_id, interview_data.get('interview_ts')))
        student_row = cs.fetchone()
        cs.close()
        conn.close()
        if student_row:
            interview_data['student_info'] = {
                'name': student_row[0],
                'roll_no': student_row[1],
                'batch_no': student_row[2],
                'center': student_row[3],
                'course': student_row[4],
                'eval_date': student_row[5]
            }
            interview_data['difficulty_level'] = student_row[6]
            if interview_table_has_language():
                interview_data['language'] = student_row[7]
    except Exception as e:
        logger.error(f"Error fetching interview info for report: {e}")

    # Ensure conversation history reflects only the current interview
    try:
        roll_no = interview_data.get('student_info', {}).get('roll_no')
        if roll_no:
            interview_ts = interview_data.get('interview_ts')
            conversation_history = load_conversation_from_file(roll_no, interview_ts)
            # Sanitize any remaining asterisks in the conversation history
            for entry in conversation_history:
                if 'text' in entry:
                    entry['text'] = _sanitize_question_text(entry['text'])
                if 'question' in entry:
                    entry['question'] = _sanitize_question_text(entry['question'])
            interview_data['conversation_history'] = conversation_history
    except Exception:
        pass
    report = generate_interview_report(interview_data)
    if report['status'] == 'error':
        logger.error(f"Error generating report: {report['message']}")
        return jsonify(report), 500
    interview_data['report_generated'] = True
    save_interview_data(email_id, interview_data)
    # Ensure interview status is marked Completed after report generation
    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        cs.execute("""
            UPDATE interview 
            SET status = 'Completed'
            WHERE email_id = %s AND interview_ts = COALESCE(
                TRY_TO_TIMESTAMP_TZ(%s, 'YYYY-MM-DD""T""HH24:MI:SS.FF TZHTZM')::TIMESTAMP_NTZ,
                TRY_TO_TIMESTAMP(%s, 'YYYY-MM-DD HH24:MI:SS')
            )
        """, (email_id, interview_data.get('interview_ts'), interview_data.get('interview_ts')))
        conn.commit()
        cs.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error updating interview status to Completed after report: {e}")

    # ENHANCED VISUAL FEEDBACK DATABASE INSERTION
    try:
        conn = get_snowflake_connection()
        if not conn:
            raise Exception("Could not connect to Snowflake")
        cs = conn.cursor()
        roll_no = email_id  # Use email_id as roll_no
        
        interview_ts = interview_data['end_time']
        
        # Create required tables
        cs.execute("""
            CREATE TABLE IF NOT EXISTS interview_rating(
              roll_no TEXT,
              technical_rating FLOAT,
              communication_rating FLOAT,
              problem_solving_rating FLOAT,
              time_management_rating FLOAT,
              total_rating FLOAT,
              interview_ts TIMESTAMP
            );
        """)
        cs.execute("""
            CREATE TABLE IF NOT EXISTS visual_feedback (
              roll_no TEXT,
              professional_appearance TEXT,
              body_language TEXT,
              environment TEXT,
              distractions TEXT,
              interview_ts TIMESTAMP,
              feedback_timestamp TIMESTAMP
            );
        """)
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

        # Insert interview rating
        cs.execute("""
            INSERT INTO interview_rating
              (roll_no, technical_rating, communication_rating,
               problem_solving_rating, time_management_rating,
               total_rating, interview_ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """, (
            roll_no,
            report['category_ratings']['technical_knowledge']['rating'],
            report['category_ratings']['communication_skills']['rating'],
            report['category_ratings']['problem_solving']['rating'],
            report['category_ratings']['time_management']['rating'],
            report['category_ratings']['overall_performance']['rating'],
            interview_ts
         ))

        # Insert into student_performance_report if not already present
        cs.execute("SELECT COUNT(*) FROM student_performance_report WHERE roll_no = %s AND interview_ts = TRY_TO_TIMESTAMP(%s, 'YYYY-MM-DD HH24:MI:SS')", (roll_no, interview_ts))
        already_exists = cs.fetchone()[0]
        if not already_exists:
            cs.execute("""
                INSERT INTO student_performance_report (
                    student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, interview_ts, report
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                interview_data.get('student_info', {}).get('name', ''),
                roll_no,
                interview_data.get('student_info', {}).get('batch_no', ''),
                interview_data.get('student_info', {}).get('center', ''),
                interview_data.get('student_info', {}).get('course', ''),
                interview_data.get('student_info', {}).get('eval_date', ''),
                interview_data.get('difficulty_level', ''),
                interview_ts,
                report['report_html']
            ))

        # ENHANCED VISUAL FEEDBACK INSERTION WITH CANDIDATE-SPECIFIC PROCESSING
        if interview_data.get('visual_feedback_data') and len(interview_data['visual_feedback_data']) > 0:
            try:
                logger.debug(f"Inserting visual feedback into Snowflake - {len(interview_data['visual_feedback_data'])} entries")
                
                # Initialize containers for feedback collection
                professional_appearance = []
                body_language = []
                environment = []
                distractions = []
                
                # Extract candidate-specific information for uniqueness
                candidate_info = interview_data.get('student_info', {})
                if not candidate_info and email_id:
                    # Create basic candidate info from email if student_info not available
                    candidate_info = {
                        'name': email_id.split('@')[0].replace('.', ' ').title(),
                        'roll_no': email_id,
                        'email': email_id
                    }
                
                candidate_id = candidate_info.get('roll_no', email_id)
                candidate_name = candidate_info.get('name', 'Unknown')
                interview_timestamp = interview_data.get('interview_ts', datetime.now().isoformat())
                
                # Process each feedback entry with candidate context
                for i, feedback_entry in enumerate(interview_data['visual_feedback_data']):
                    logger.debug(f"Processing feedback entry {i+1}: {feedback_entry}")
                    
                    if isinstance(feedback_entry, dict) and 'feedback' in feedback_entry:
                        visual_data = feedback_entry['feedback']
                        question_num = feedback_entry.get('question_number', i+1)
                        timestamp = feedback_entry.get('timestamp', 'unknown')
                        
                        if isinstance(visual_data, dict):
                            # Extract individual feedback components with context
                            pa = visual_data.get('professional_appearance', '').strip()
                            bl = visual_data.get('body_language', '').strip()
                            env = visual_data.get('environment', '').strip()
                            dist = visual_data.get('distractions', '').strip()
                            
                            # Only add meaningful, unique feedback
                            if pa and len(pa) > 15 and not any(generic in pa.lower() for generic in 
                                                              ['no feedback', 'not available', 'not fully clear', 
                                                               'no visual feedback', 'appears professional', 'seems neat']):
                                # Add context to make it unique
                                contextual_pa = f"Q{question_num}: {pa}"
                                professional_appearance.append(contextual_pa)
                            
                            if bl and len(bl) > 15 and not any(generic in bl.lower() for generic in 
                                                               ['no feedback', 'not available', 'not fully clear', 'no visual feedback']):
                                contextual_bl = f"Q{question_num}: {bl}"
                                body_language.append(contextual_bl)
                                
                            if env and len(env) > 15 and not any(generic in env.lower() for generic in 
                                                                 ['no feedback', 'not available', 'not fully clear', 'no visual feedback']):
                                contextual_env = f"Q{question_num}: {env}"
                                environment.append(contextual_env)
                                
                            if dist and len(dist) > 10 and not any(generic in dist.lower() for generic in 
                                                                   ['no feedback', 'not available', 'not fully clear', 'no visual feedback']):
                                contextual_dist = f"Q{question_num}: {dist}"
                                distractions.append(contextual_dist)
                
                def create_candidate_specific_feedback(feedback_list, category_name, candidate_info):
                    """Create candidate-specific feedback that avoids generic responses"""
                    if not feedback_list:
                        return f"No specific {category_name.lower()} observations for {candidate_info.get('name', 'candidate')} during this interview"
                    
                    # Remove duplicate observations
                    unique_feedback = []
                    seen = set()
                    for item in feedback_list:
                        # Extract the actual feedback (after "QX: ")
                        clean_feedback = item.split(': ', 1)[1] if ': ' in item else item
                        if clean_feedback.lower() not in seen and len(clean_feedback) > 10:
                            unique_feedback.append(item)
                            seen.add(clean_feedback.lower())
                    
                    if not unique_feedback:
                        return f"No distinct {category_name.lower()} patterns observed for this candidate"
                    
                    if len(unique_feedback) == 1:
                        # Single observation - make it candidate-specific
                        observation = unique_feedback[0].split(': ', 1)[1] if ': ' in unique_feedback[0] else unique_feedback[0]
                        return f"Candidate {candidate_info.get('name', candidate_id)} consistently showed: {observation.lower()}"
                    
                    elif len(unique_feedback) <= 3:
                        # Few observations - create progression narrative
                        observations = []
                        for feedback in unique_feedback:
                            obs = feedback.split(': ', 1)[1] if ': ' in feedback else feedback
                            observations.append(obs.lower())
                        
                        return f"Throughout the interview, {candidate_info.get('name', candidate_id)} demonstrated: {observations[0]}. Additionally observed: {observations[1] if len(observations) > 1 else 'consistent behavior'}"
                    
                    else:
                        # Multiple observations - analyze patterns
                        question_patterns = {}
                        for feedback in unique_feedback:
                            if ': ' in feedback:
                                q_part, obs_part = feedback.split(': ', 1)
                                if obs_part.lower() not in question_patterns:
                                    question_patterns[obs_part.lower()] = []
                                question_patterns[obs_part.lower()].append(q_part)
                        
                        # Find most consistent pattern
                        most_frequent = max(question_patterns.items(), key=lambda x: len(x[1]))
                        pattern_text, questions = most_frequent
                        
                        if len(questions) >= len(unique_feedback) // 2:
                            return f"Primary characteristic for {candidate_info.get('name', candidate_id)}: {pattern_text} (observed across {len(questions)} interview segments)"
                        else:
                            # Show diversity
                            top_patterns = list(question_patterns.keys())[:2]
                            return f"Variable {category_name.lower()} for {candidate_info.get('name', candidate_id)} including: {top_patterns[0]}; also noted: {top_patterns[1] if len(top_patterns) > 1 else 'other characteristics'}"
                
                # Generate candidate-specific feedback for each category
                final_visual_feedback = {
                    "professional_appearance": create_candidate_specific_feedback(
                        professional_appearance, "Professional Appearance", candidate_info
                    ),
                    "body_language": create_candidate_specific_feedback(
                        body_language, "Body Language", candidate_info
                    ),
                    "environment": create_candidate_specific_feedback(
                        environment, "Environment", candidate_info
                    ),
                    "distractions": create_candidate_specific_feedback(
                        distractions, "Distractions", candidate_info
                    )
                }
                
                # Add interview-specific context
                interview_context = f" (Interview on {interview_timestamp[:10]} at {interview_timestamp[11:19]})"
                for key in final_visual_feedback:
                    if "No specific" not in final_visual_feedback[key] and "No distinct" not in final_visual_feedback[key]:
                        final_visual_feedback[key] += interview_context
                
                logger.info(f"Final processed visual feedback for {candidate_id}: {final_visual_feedback}")
                
                # Insert into database with proper error handling and truncation
                try:
                    cs.execute("""
                        INSERT INTO visual_feedback
                          (roll_no, professional_appearance, body_language,
                           environment, distractions, interview_ts)
                        VALUES (%s, %s, %s, %s, %s, %s);
                    """, (
                        candidate_id,
                        final_visual_feedback['professional_appearance'][:800],  # Increased limit with truncation
                        final_visual_feedback['body_language'][:800],
                        final_visual_feedback['environment'][:800],
                        final_visual_feedback['distractions'][:800],
                        interview_ts
                    ))
                    
                    logger.info(f"Successfully saved unique visual feedback for candidate {candidate_id} to Snowflake")
                    
                except Exception as db_error:
                    logger.error(f"Database insertion error for visual feedback: {str(db_error)}")
                    # Try simplified version with basic uniqueness
                    try:
                        simplified_feedback = {
                            "professional_appearance": f"{candidate_name} - Professional appearance observed during {len(professional_appearance)} segments" if professional_appearance else "No appearance feedback",
                            "body_language": f"{candidate_name} - Body language patterns noted across {len(body_language)} observations" if body_language else "No body language feedback", 
                            "environment": f"{candidate_name} - Interview environment assessed in {len(environment)} instances" if environment else "No environment feedback",
                            "distractions": f"{candidate_name} - Distraction analysis from {len(distractions)} checkpoints" if distractions else "No distraction feedback"
                        }
                        
                        cs.execute("""
                            INSERT INTO visual_feedback
                              (roll_no, professional_appearance, body_language,
                               environment, distractions, interview_ts)
                            VALUES (%s, %s, %s, %s, %s, %s);
                        """, (
                            candidate_id,
                            simplified_feedback['professional_appearance'][:500],
                            simplified_feedback['body_language'][:500],
                            simplified_feedback['environment'][:500],
                            simplified_feedback['distractions'][:500],
                            interview_ts
                        ))
                        logger.info(f"Saved simplified visual feedback for candidate {candidate_id}")
                    except Exception as fallback_error:
                        logger.error(f"Even simplified visual feedback insertion failed: {str(fallback_error)}")
                        
            except Exception as e:
                logger.error(f"Error processing visual feedback for database: {str(e)}", exc_info=True)
        else:
            logger.info("No visual feedback data to insert into database")

        conn.commit()
        logger.info("Successfully saved all interview data to Snowflake")
        
    except Exception as e:
        logger.error(f"Snowflake insert failed: {e}")
    finally:
        if 'cs' in locals(): cs.close()
        if 'conn' in locals(): conn.close()

    return jsonify({
        "status": "success",
        "report": report['report_html'],
        "ratings": report['category_ratings'],
        # "voice_feedback": report['voice_feedback'],
        # "voice_audio": report['voice_audio'],
        "status_class": report['status_class'],
        "visual_feedback": report.get('visual_feedback', {})
    })

@interview_bp.route('/reset_interview', methods=['POST'])
def reset_interview():
    logger.debug("Reset interview endpoint called")
    if "user" not in session:
        logger.warning("Unauthenticated reset interview attempt")
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    email_id = session.get("user")
    clear_interview_data(email_id)
    session['interview_data'] = init_interview_data()
    logger.info("Interview session reset")
    return jsonify({"status": "success", "message": "Interview reset successfully"})

@interview_bp.route('/interview_status')
def interview_status():
    if "user" not in session:
        return jsonify({"status": "not_authenticated"}), 401
    email_id = session.get("user")
    interview_data = get_interview_data(email_id)
    return jsonify({
        "is_processing": interview_data.get('is_processing_answer', False),
        "waiting_for_answer": interview_data.get('waiting_for_answer', False),
        "current_question": interview_data.get('current_question', 0),
        "total_questions": len(interview_data.get('questions', []))
    }) 

@interview_bp.route('/scheduled_interview')
def scheduled_interview():
    if 'user' not in session or session.get('role') != 'student':
        return redirect(url_for('login'))
    email_id = session['user']
    # Fetch all scheduled interviews for this student from interview table by email_id
    conn = get_snowflake_connection()
    cs = conn.cursor()
    if interview_table_has_language():
        cs.execute("""
            SELECT student_name, roll_no, email_id, batch_no, center, course, evaluation_date, difficulty_level, language, interview_ts, jd_id, status
            FROM interview
            WHERE email_id = %s OR roll_no = %s
            ORDER BY interview_ts DESC
        """, (email_id, email_id))
    else:
        cs.execute("""
            SELECT student_name, roll_no, email_id, batch_no, center, course, evaluation_date, difficulty_level, interview_ts, jd_id, status
            FROM interview
            WHERE email_id = %s OR roll_no = %s
            ORDER BY interview_ts DESC
        """, (email_id, email_id))
    all_interviews = cs.fetchall()
    student_cols = [desc[0].replace('_', ' ').title() for desc in cs.description]
    cs.close()
    conn.close()
    # Add JD Name column
    student_cols.append('JD Name')
    # For each interview, get JD name
    interviews = []
    # Try Redis fallback for jd_id if missing
    redis_fallback = get_interview_data(email_id)
    redis_ts = None
    redis_jd_id = None
    if redis_fallback:
        redis_ts = redis_fallback.get('interview_ts')
        redis_jd_id = redis_fallback.get('jd_id')
    has_lang = interview_table_has_language()
    # Set column indexes depending on presence of LANGUAGE column
    idx_ts = 9 if has_lang else 8
    idx_jd = 10 if has_lang else 9
    idx_status = 11 if has_lang else 10
    for interview in all_interviews:
        jd_id = interview[idx_jd]
        status = interview[idx_status]
        # Get JD name (first 30 chars of JD text or 'N/A')
        jd_name = 'N/A'
        # Redis fallback when DB jd_id is missing but we have it in session for the same interview_ts
        if (not jd_id or str(jd_id).lower() == 'none') and redis_ts and redis_jd_id and interview[idx_ts] == redis_ts:
            jd_id = redis_jd_id
        if jd_id:
            jd_text = get_jd_text(jd_id)
            if jd_text:
                jd_name = jd_text[:30] + ('...' if len(jd_text) > 30 else '')
        # Add interview_ts for use in Start Interview link
        interviews.append({
            'info': interview,
            'status': status,
            'jd_name': jd_name,
            'jd_id': jd_id,
            'interview_ts': interview[idx_ts]
        })
    return render_template('scheduled_interview.html', interviews=interviews, student_cols=student_cols)

@interview_bp.route('/start_scheduled_interview/<jd_id>/<interview_ts>', methods=['POST'])
def start_scheduled_interview(jd_id, interview_ts):
    if 'user' not in session or session.get('role') != 'student':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    email_id = session['user']
    try:
        conn = get_snowflake_connection()
        cs = conn.cursor()
        if interview_table_has_language():
            cs.execute("""
                SELECT student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level, language
                FROM interview
                WHERE (email_id = %s OR roll_no = %s)
                  AND interview_ts = COALESCE(
                    TRY_TO_TIMESTAMP_TZ(%s, 'YYYY-MM-DD"T"HH24:MI:SS.FF TZHTZM')::TIMESTAMP_NTZ,
                    TRY_TO_TIMESTAMP(%s, 'YYYY-MM-DD HH24:MI:SS')
                  )
            """, (email_id, email_id, interview_ts, interview_ts))
        else:
            cs.execute("""
                SELECT student_name, roll_no, batch_no, center, course, evaluation_date, difficulty_level
                FROM interview
                WHERE (email_id = %s OR roll_no = %s)
                  AND interview_ts = COALESCE(
                    TRY_TO_TIMESTAMP_TZ(%s, 'YYYY-MM-DD"T"HH24:MI:SS.FF TZHTZM')::TIMESTAMP_NTZ,
                    TRY_TO_TIMESTAMP(%s, 'YYYY-MM-DD HH24:MI:SS')
                  )
            """, (email_id, email_id, interview_ts, interview_ts))
        student_data = cs.fetchone()
        cs.close()
        conn.close()
        if not student_data or len(student_data) < (8 if interview_table_has_language() else 7):
            logger.error(f"Interview not found or incomplete data for {email_id} at {interview_ts}: {student_data}")
            return "Interview not found or incomplete data", 404
        # If jd_id is 'None' from template fallback, try to recover from Redis or DB
        if not jd_id or str(jd_id).lower() == 'none':
            session_data = get_interview_data(email_id)
            recovered_jd_id = None
            if session_data and session_data.get('interview_ts') == interview_ts:
                recovered_jd_id = session_data.get('jd_id')
            if not recovered_jd_id:
                try:
                    conn2 = get_snowflake_connection()
                    cs2 = conn2.cursor()
                    cs2.execute("SELECT jd_id FROM interview WHERE (email_id = %s OR roll_no = %s) AND interview_ts = COALESCE(TRY_TO_TIMESTAMP_TZ(%s, 'YYYY-MM-DD""T""HH24:MI:SS.FF TZHTZM')::TIMESTAMP_NTZ, TRY_TO_TIMESTAMP(%s, 'YYYY-MM-DD HH24:MI:SS')) ORDER BY interview_ts DESC LIMIT 1", (email_id, email_id, interview_ts, interview_ts))
                    r = cs2.fetchone()
                    cs2.close()
                    conn2.close()
                    if r and r[0]:
                        recovered_jd_id = r[0]
                except Exception:
                    recovered_jd_id = None
            jd_id = recovered_jd_id
        jd_text = get_jd_text(jd_id) if jd_id else None
        interview_data = init_interview_data()
        interview_data['jd_id'] = jd_id
        interview_data['jd_text'] = jd_text
        interview_data['scheduled'] = True
        interview_data['interview_ts'] = interview_ts
        interview_data['difficulty_level'] = student_data[6]
        if interview_table_has_language():
            interview_data['language'] = student_data[7]
        interview_data['student_info'] = {
            'name': student_data[0],
            'roll_no': student_data[1],
            'batch_no': student_data[2],
            'center': student_data[3],
            'course': student_data[4],
            'eval_date': student_data[5]
        }
        save_interview_data(email_id, interview_data)
        return redirect(url_for('interview.interview_bot'))
    except Exception as e:
        logger.error(f"Error starting scheduled interview: {e}", exc_info=True)
        return "Error starting interview", 500 

@interview_bp.route('/schedule_interview', methods=['GET', 'POST'])
def schedule_interview():
    if 'user' not in session or session.get('role') != 'recruiter':
        return redirect(url_for('auth.login'))
    conn = get_snowflake_connection()
    cs = conn.cursor()
    # Use only the interview table for fetching student/interview info
    cs.execute("""
        SELECT student_name, roll_no, email_id, batch_no, center, course, evaluation_date, interview_ts
        FROM interview
        ORDER BY interview_ts DESC
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
        filename = secure_filename(jd_file.filename)
        jd_text = extract_text_from_file(jd_file)
        jd_file.save(os.path.join('static/reports', filename))
        if not jd_text or not jd_text.strip():
            flash('Could not extract text from the uploaded JD file. Please upload a valid DOCX, TXT, or text-based PDF file (not a scanned image).', 'danger')
            return render_template('schedule_interview.html')
        admin_id = session['user']
        jd_id = insert_jd(jd_text, admin_id)
        interview_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                language TEXT,
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
        # Save to Redis for interview flow, including difficulty_level
        interview_data = {
            'jd_text': jd_text,
            'jd_id': jd_id,
            'scheduled': True,
            'notified': False,
            'difficulty_level': difficulty_level,  # ENFORCE recruiter-set difficulty
            'language': request.form.get('language'),
            'interview_ts': interview_ts
        }
        save_interview_data(email_id, interview_data)
        flash('Interview scheduled and notification sent!', 'success')
        return render_template('schedule_interview.html', show_modal=True)
    return render_template('schedule_interview.html', students=students, student_cols=student_cols)