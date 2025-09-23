import json
import logging
from config import Config
from backend.utils.json_encoder import CustomJSONEncoder
from backend.services.session_service import (
    get_interview_data as db_get_interview_data,
    save_interview_data as db_save_interview_data,
    clear_interview_data as db_clear_interview_data
)

def init_interview_data():
    return {
        "questions": [],
        "answers": [],
        "ratings": [],
        "current_question": 0,
        "interview_started": False,
        "conversation_history": [],
        "jd_text": "",
        "difficulty_level": None,
        "student_info": {
            'name': '',
            'roll_no': '',
            'batch_no': '',
            'center': '',
            'course': '',
            'eval_date': ''
        },
        "start_time": None,
        "end_time": None,
        "visual_feedback": [],
        "last_frame_time": 0,
        "last_activity_time": None,
        "current_context": "",
        "last_speech_time": None,
        "speech_detected": False,
        "current_answer": "",
        "speech_start_time": None,
        "is_processing_answer": False,
        "interview_time_used": 0,
        "visual_feedback_data": [],
        "waiting_for_answer": False,
        "report_generated": False
    }

def get_interview_data(user_id):
    """Get interview data from database"""
    try:
        data = db_get_interview_data(user_id)
        if data:
            return data
        return init_interview_data()
    except Exception as e:
        logging.error(f"Error getting interview data: {str(e)}")
        return init_interview_data()

def save_interview_data(user_id, data):
    """Save interview data to database"""
    try:
        db_save_interview_data(user_id, data)
    except Exception as e:
        logging.error(f"Error saving interview data: {str(e)}")

def clear_interview_data(user_id):
    """Clear interview data from database"""
    try:
        db_clear_interview_data(user_id)
    except Exception as e:
        logging.error(f"Error clearing interview data: {str(e)}") 