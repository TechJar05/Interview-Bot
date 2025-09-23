from flask import Flask, request, jsonify,render_template
from flask_cors import CORS
import os
import PyPDF2
import docx
import openai
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

# Configure OpenAI API
openai.api_key = os.getenv('OPENAI_API_KEY')

# Configure upload folder
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create uploads folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_file(file_path):
    file_extension = file_path.rsplit('.', 1)[1].lower()
    
    if file_extension == 'pdf':
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            text = ''
            for page in pdf_reader.pages:
                text += page.extract_text()
            return text
    
    elif file_extension in ['doc', 'docx']:
        doc = docx.Document(file_path)
        text = ''
        for paragraph in doc.paragraphs:
            text += paragraph.text + '\n'
        return text
    
    elif file_extension == 'txt':
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()

def generate_questions(jd_text):
    prompt = f"""Based on the following job description, generate 5 interview questions:
    1 introduction question, 3 technical questions, and 1 behavioral question.
    
    Job Description:
    {jd_text}
    
    Format the response as a JSON array of questions, with each question having a 'type' field ('introduction', 'technical', or 'behavioral').
    """
    
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an expert interviewer who creates relevant questions based on job descriptions."},
            {"role": "user", "content": prompt}
        ]
    )
    
    return response.choices[0].message.content

def evaluate_answer(question, answer):
    prompt = f"""Evaluate the following answer to the interview question:
    
    Question: {question}
    Answer: {answer}
    
    Provide a brief evaluation of the answer's quality and relevance.
    """
    
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an expert interviewer evaluating candidate responses."},
            {"role": "user", "content": prompt}
        ]
    )
    
    return response.choices[0].message.content

# Store interview state
interview_state = {}

@app.route('/start_interview', methods=['POST'])
def start_interview():
    if 'jd_file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['jd_file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Extract text from the uploaded file
        jd_text = extract_text_from_file(file_path)
        
        # Generate questions based on the JD
        questions_json = generate_questions(jd_text)
        
        # Store the questions in the interview state
        interview_state['questions'] = questions_json
        interview_state['current_question_index'] = 0
        
        # Return the first question
        return jsonify({
            'question': questions_json[0]['question'],
            'type': questions_json[0]['type']
        })
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/submit_answer', methods=['POST'])
def submit_answer():
    data = request.get_json()
    answer = data.get('answer')
    
    if not answer:
        return jsonify({'error': 'No answer provided'}), 400
    
    current_index = interview_state.get('current_question_index', 0)
    questions = interview_state.get('questions', [])
    
    if current_index >= len(questions):
        return jsonify({'error': 'Interview already completed'}), 400
    
    # Evaluate the answer
    evaluation = evaluate_answer(questions[current_index]['question'], answer)
    
    # Move to next question
    current_index += 1
    interview_state['current_question_index'] = current_index
    
    if current_index >= len(questions):
        return jsonify({
            'is_complete': True,
            'evaluation': evaluation
        })
    
    return jsonify({
        'is_complete': False,
        'next_question': questions[current_index]['question'],
        'type': questions[current_index]['type'],
        'evaluation': evaluation
    })

if __name__ == '__main__':
    app.run(debug=True) 