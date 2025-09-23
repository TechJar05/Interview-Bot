import logging
import PyPDF2
import docx
import re
from io import BytesIO
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = 'interview_history'
logger = logging.getLogger(__name__)

def extract_text_from_file(file):
    try:
        if file.filename.lower().endswith('.pdf'):
            pdf_reader = PyPDF2.PdfReader(file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text()
            return text
        elif file.filename.lower().endswith(('.doc', '.docx')):
            doc = docx.Document(BytesIO(file.read()))
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        elif file.filename.lower().endswith('.txt'):
            text = file.read().decode('utf-8')
            return text
        else:
            return None
    except Exception as e:
        logger.error(f"Error extracting text from file: {str(e)}")
        return None

def save_conversation_to_file(conversation_data, roll_no=None, interview_ts=None):
    try:
        os.makedirs(root_dir, exist_ok=True)
        # Build filename scoped to specific interview when possible
        if roll_no and interview_ts:
            # Sanitize timestamp for filesystem use
            ts_key = str(interview_ts).replace(":", "").replace(" ", "_").replace("-", "").replace(".", "_")
            filename = os.path.join(root_dir,f"interview_conversation_{roll_no}_{ts_key}.txt")
        else:
            # Backward-compatible fallback (per user)
            filename = os.path.join(root_dir,f"interview_conversation_{roll_no}.txt" if roll_no else "interview_conversation.txt")
        with open(filename, "a") as f:
            for item in conversation_data:
                if 'speaker' in item:
                    # Final safety check - sanitize text before saving
                    text = item['text']
                    # Remove any asterisks at the beginning
                    text = re.sub(r'^\s*\*+\s*', '', text)
                    # Remove any asterisks at the end
                    text = re.sub(r'\s*\*+\s*$', '', text)
                    # Remove markdown asterisks
                    text = re.sub(r'\*+([^*]*?)\*+', r'\1', text)
                    f.write(f"{item['speaker']}: {text}\n")
                elif 'question' in item:
                    # Final safety check - sanitize question before saving
                    question = item['question']
                    # Remove any asterisks at the beginning
                    question = re.sub(r'^\s*\*+\s*', '', question)
                    # Remove any asterisks at the end
                    question = re.sub(r'\s*\*+\s*$', '', question)
                    # Remove markdown asterisks
                    question = re.sub(r'\*+([^*]*?)\*+', r'\1', question)
                    f.write(f"Question: {question}\n")
    except Exception as e:
        logger.error(f"Error saving conversation to file: {str(e)}", exc_info=True)

def load_conversation_from_file(roll_no=None, interview_ts=None):
    try:
        # Build filename scoped to specific interview when possible
        if roll_no and interview_ts:
            ts_key = str(interview_ts).replace(":", "").replace(" ", "_").replace("-", "").replace(".", "_")
            filename = os.path.join(root_dir,f"interview_conversation_{roll_no}_{ts_key}.txt")
            if not os.path.exists(filename):
                # Fallback to legacy per-user file if specific interview file not found
                legacy = os.path.join(root_dir,f"interview_conversation_{roll_no}.txt")
                filename = legacy
        else:
            filename = os.path.join(root_dir,f"interview_conversation_{roll_no}.txt" if roll_no else "interview_conversation.txt")
        if not os.path.exists(filename):
            return []
        with open(filename, "r") as f:
            lines = f.readlines()
        conversation = []
        for line in lines:
            if line.startswith("bot:") or line.startswith("user:"):
                speaker, text = line.split(":", 1)
                # Sanitize the text to remove any asterisks
                text = text.strip()
                # Remove all markdown asterisks from the text
                text = re.sub(r'\*+([^*]*?)\*+', r'\1', text)
                # Remove any asterisks at the beginning or end
                text = re.sub(r'^\s*\*+\s*', '', text)
                text = re.sub(r'\s*\*+\s*$', '', text)
                conversation.append({"speaker": speaker.strip(), "text": text})
            elif line.startswith("Question:"):
                question = line.split(":", 1)[1].strip()
                # Sanitize the question to remove any asterisks
                question = re.sub(r'\*+([^*]*?)\*+', r'\1', question)
                question = re.sub(r'^\s*\*+\s*', '', question)
                question = re.sub(r'\s*\*+\s*$', '', question)
                conversation.append({"question": question})
        return conversation
    except Exception as e:
        logger.error(f"Error loading conversation from file: {str(e)}", exc_info=True)
        return [] 