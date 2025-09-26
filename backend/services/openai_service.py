import openai
import logging
import json
import hashlib
import re
import os
from config import Config
from datetime import datetime
from collections import Counter
from backend.services.audio_service import text_to_speech
from backend.utils.file_utils import load_conversation_from_file
from backend.utils.performance_utils import timing_decorator

openai.api_key = Config.OPENAI_API_KEY
openai.api_base = Config.OPENAI_API_BASE
logger = logging.getLogger(__name__)

# Simple in-memory cache for response evaluations
_evaluation_cache = {}
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = 'interview_history'


def translate_text(text: str, target_language: str) -> str:
    try:
        lang = (target_language or 'english').strip().lower()
        if lang in {'hindi', 'hi'}:
            instruction = "Translate the following text to Standard Hindi (à¤®à¤¾à¤¨à¤• à¤¹à¤¿à¤‚à¤¦à¥€) using Devanagari script only. Avoid transliteration/Hinglish. Keep technical terms accurate. Return only the translated text."
        else:
            # default to English
            instruction = "Translate the following text to English only. Keep technical terms accurate. Return only the translated text."
        prompt = f"{instruction}\n\nText:\n{text}"
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
            timeout=15
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return text

def _get_cache_key(answer, question, difficulty_level):
    """Generate a cache key for response evaluation"""
    content = f"{answer[:100]}_{question[:100]}_{difficulty_level}"
    return hashlib.md5(content.encode()).hexdigest()

def generate_questions_from_jd(jd_text, difficulty_level, roll_no=None, language='english'):
    # Normalize difficulty values coming from UI/DB
    normalized = (difficulty_level or "").strip().lower()
    if normalized in {"easy", "beginner"}:
        normalized = "beginner"
    elif normalized in {"hard", "advanced"}:
        normalized = "advanced"
    elif normalized == "medium":
        normalized = "medium"
    else:
        # Default to medium if unknown string
        normalized = "medium"
    difficulty_level = normalized
    # Normalize language
    lang = (language or 'english').strip().lower()
    if lang in {'english+hindi', 'bilingual', 'en+hi', 'hinglish'}:
        lang = 'bilingual'
    elif lang in {'english', 'en'}:
        lang = 'english'
    elif lang in {'hindi', 'hi'}:
        lang = 'hindi'
    else:
        lang = 'english'
    logger.debug(f"Generating questions from JD for difficulty: {difficulty_level}, language: {lang}")
    if not jd_text:
        logger.error("No JD text provided for question generation.")
        return []
    previous_questions = []
    filename = os.path.join(root_dir, f"interview_conversation_{roll_no}.txt" if roll_no else "interview_conversation.txt")
    try:
        with open(filename, "r") as f:
            for line in f:
                if line.startswith("Question:"):
                    question = line.split(":", 1)[1].strip()
                    # Sanitize the question to remove any asterisks
                    question = re.sub(r'\*+([^*]*?)\*+', r'\1', question)
                    question = re.sub(r'^\s*\*+\s*', '', question)
                    question = re.sub(r'\s*\*+\s*$', '', question)
                    previous_questions.append(question)
    except FileNotFoundError:
        pass
    language_directive = ""
    if lang == 'english':
        language_directive = "Generate all questions in English."
    elif lang == 'hindi':
        language_directive = "Generate all questions in Standard Hindi (à¤®à¤¾à¤¨à¤• à¤¹à¤¿à¤‚à¤¦à¥€) using Devanagari script. Avoid Hinglish."
    else:  # bilingual
        language_directive = "For each question, provide English and Hindi (Standard Hindi in Devanagari) versions separated by ' | '."

    prompt = f"""
    Generate an interview script based on the following job description. The interview should have:
    1. One introduction question
    2. Three technical questions (appropriate for {difficulty_level} level)
    3. One behavioral question
    For {difficulty_level} level, ensure the questions are:
    - Beginner: Basic concepts, simple scenarios
    - Medium: Intermediate concepts, practical applications
    - Advanced: Complex problems, in-depth analysis
    {language_directive}
    Avoid repeating these previous questions: {previous_questions[-5:] if previous_questions else "None"}
    Job Description:
    {jd_text}
    Format the output as plain text questions without any markdown formatting, asterisks, or special characters:
    Question 1: [introduction question]
    Question 2: [technical question 1]
    Question 3: [technical question 2]
    Question 4: [technical question 3]
    Question 5: [behavioral question]
    
    IMPORTANT: Do not use any markdown formatting, asterisks (*), bold formatting (**), or special characters. Use only plain text.
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1500,
            timeout=45
        )
        if 'choices' not in response or not response['choices']:
            logger.error("No valid choices found in OpenAI response.")
            return []
        script = response.choices[0].message.content or ""
        questions = []
        
        # Clean the script to remove any markdown formatting
        script = re.sub(r'\*+([^*]*?)\*+', r'\1', script)  # Remove markdown bold/italic
        
        # Primary: lines labeled as Question N:
        for line in script.split("\n"):
            ls = line.strip()
            if ls.lower().startswith("question"):
                parts = ls.split(":", 1)
                if len(parts) > 1 and parts[1].strip():
                    question_text = parts[1].strip()
                    # Remove any remaining markdown formatting
                    question_text = re.sub(r'\*+([^*]*?)\*+', r'\1', question_text)
                    questions.append(question_text)
        
        # Fallback 1: bullet or numbered lines
        if not questions:
            for line in script.split("\n"):
                ls = line.strip(" -\t")
                if re.match(r"^(\d+\.|[-*â€¢])\s+", line.strip()):
                    # remove list marker
                    cleaned = re.sub(r"^(\d+\.|[-*â€¢])\s+", "", line.strip()).strip()
                    if cleaned:
                        # Remove any markdown formatting
                        cleaned = re.sub(r'\*+([^*]*?)\*+', r'\1', cleaned)
                        questions.append(cleaned)
        
        # Fallback 2: sentences split
        if not questions:
            sentences = [s.strip() for s in script.split("\n") if s.strip()]
            questions = sentences[:5]
        
        # Final sanitize and clean
        cleaned_questions = []
        for q in questions:
            if q and q.strip():
                # Remove any remaining markdown formatting
                q = re.sub(r'\*+([^*]*?)\*+', r'\1', q.strip())
                # Normalize nbsp
                q = q.replace('\u00A0', ' ')
                # Remove any markers/bullets at the beginning or end
                q = re.sub(r'^[\s\*\-\u2022\u2023\u25E6\u2043\u2219]+', '', q)
                q = re.sub(r'[\s\*\-\u2022\u2023\u25E6\u2043\u2219]+$', '', q)
                # Remove stray "**" in the middle surrounded by spaces
                q = re.sub(r'[\s\u00A0]+\*+[\s\u00A0]+', ' ', q)
                # Ensure question ends with proper punctuation
                q = q.rstrip('?.!').strip() + '?' if q and not q.strip().endswith('?') else q.strip()
                if q:
                    cleaned_questions.append(q)
        
        return cleaned_questions[:5]
    except Exception as e:
        logger.error(f"Error generating questions: {str(e)}", exc_info=True)
        if difficulty_level == "beginner":
            return [
                "Tell us about yourself and your background.",
                "What programming languages are you familiar with?",
                "Explain a basic programming concept you've learned recently.",
                "Have you worked on any small coding projects?",
                "Describe a time when you had to learn something new quickly."
            ]
        elif difficulty_level == "advanced":
            return [
                "Walk us through your professional experience and key achievements.",
                "Explain a complex technical challenge you've solved recently.",
                "How would you design a scalable system for high traffic?",
                "Describe your approach to debugging complex issues.",
                "Tell us about a time you had to lead a technical team through a difficult project."
            ]
        else:
            return [
                "Tell us about your technical background and experience.",
                "Explain a technical concept you're comfortable with in detail.",
                "Describe a project where you implemented a technical solution.",
                "How do you approach learning new technologies?",
                "Describe a time you had to work in a team to solve a technical problem."
            ]

def generate_encouragement_prompt(conversation_history):
    try:
        prompt = f"""
        The candidate has paused during their response. Generate a brief, encouraging prompt to:
        - Help them continue their thought
        - Be supportive and professional
        - Be concise (one short sentence)
        Current conversation context:
        {conversation_history[-2:] if len(conversation_history) > 2 else conversation_history}
        Return ONLY the prompt, nothing else.
        """
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=300
        )
        encouragement = response.choices[0].message.content.strip()
        return encouragement
    except Exception as e:
        logger.error(f"Error generating encouragement prompt: {str(e)}", exc_info=True)
        return "Please continue with your thought."

def extract_json_from_response(response_content):
    """
    Robust JSON extraction that handles various response formats from OpenAI
    """
    if not response_content or response_content.strip() == "":
        return None
    
    response_content = response_content.strip()
    
    # Method 1: Try direct JSON parsing
    try:
        return json.loads(response_content)
    except json.JSONDecodeError:
        pass
    
    # Method 2: Extract from markdown code blocks
    json_patterns = [
        r'```json\s*([\s\S]*?)\s*```',  # ```json...```
        r'```\s*([\s\S]*?)\s*```',      # ```...```
        r'`([^`]+)`'                     # Single backticks
    ]
    
    for pattern in json_patterns:
        match = re.search(pattern, response_content)
        if match:
            try:
                json_content = match.group(1).strip()
                return json.loads(json_content)
            except json.JSONDecodeError:
                continue
    
    # Method 3: Find JSON object in text
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_content)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass
    
    # Method 4: Extract individual values using regex
    try:
        ratings = {}
        patterns = {
            'technical': r'"technical":\s*(\d+(?:\.\d+)?)',
            'communication': r'"communication":\s*(\d+(?:\.\d+)?)',
            'problem_solving': r'"problem_solving":\s*(\d+(?:\.\d+)?)',
            'time_management': r'"time_management":\s*(\d+(?:\.\d+)?)',
            'overall': r'"overall":\s*(\d+(?:\.\d+)?)'
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, response_content)
            if match:
                ratings[key] = float(match.group(1))
        
        if len(ratings) == 5:  # All ratings found
            return ratings
            
    except Exception:
        pass
    
    return None

def validate_and_normalize_ratings(ratings):
    """
    Validate and ensure ratings are on 1-10 scale
    """
    if not isinstance(ratings, dict):
        return None
    
    required_keys = ['technical', 'communication', 'problem_solving', 'time_management', 'overall']
    if not all(key in ratings for key in required_keys):
        return None
    
    normalized_ratings = {}
    for key, value in ratings.items():
        try:
            rating_value = float(value)
            
            # Validate and ensure ratings are on 1-10 scale
            if rating_value < 1 or rating_value > 10:
                if 0 <= rating_value <= 1:
                    # Assume it's on a 0-1 scale, scale to 1-10
                    rating_value = (rating_value * 9) + 1
                elif 1 <= rating_value <= 5:
                    # Assume it's on a 1-5 scale, scale to 1-10
                    rating_value = (rating_value - 1) * (9/4) + 1
                else:
                    # Clamp to valid range
                    rating_value = max(1, min(10, rating_value))
                logger.warning(f"Rating {key} was {value}, adjusted to {rating_value}")
            
            normalized_ratings[key] = float(rating_value)
            
        except (ValueError, TypeError):
            logger.error(f"Invalid rating value for {key}: {value}")
            return None
    
    return normalized_ratings

@timing_decorator("Response Evaluation")
def evaluate_response(answer, question, difficulty_level, visual_feedback=None):
    """
    Evaluate interview response using OpenAI API with robust error handling
    
    Args:
        answer (str): The candidate's response
        question (str): The interview question
        difficulty_level (str): Interview difficulty level
        visual_feedback (dict, optional): Visual feedback data
    
    Returns:
        dict: Rating scores for different categories (1-10 scale)
    """
    # Check cache first
    cache_key = _get_cache_key(answer, question, difficulty_level)
    if cache_key in _evaluation_cache:
        logger.debug("Using cached evaluation result")
        return _evaluation_cache[cache_key]
    
    # Handle very short responses
    if len(answer.strip()) < 20:
        result = {
            "technical": 4.0,
            "communication": 4.0,
            "problem_solving": 4.0,
            "time_management": 4.0,
            "overall": 4.0
        }
        _evaluation_cache[cache_key] = result
        return result
    
    # Prepare the evaluation prompt
    rating_prompt = f"""Rate this {difficulty_level} level interview response on a scale of 1-10 for each category:

Question: "{question[:200]}{'...' if len(question) > 200 else ''}"
Answer: "{answer[:500]}{'...' if len(answer) > 500 else ''}"

Evaluate based on:
- Technical: Accuracy, depth of knowledge, correctness
- Communication: Clarity, articulation, structure
- Problem Solving: Logic, creativity, approach
- Time Management: Conciseness, relevance
- Overall: Composite performance

IMPORTANT: 
- Use ONLY numbers from 1 to 10 (inclusive). Do not use any other scale.
- Return ONLY plain JSON without any markdown formatting or code blocks.
- Format exactly as: {{"technical": X, "communication": X, "problem_solving": X, "time_management": X, "overall": X}}
- Where X is a number between 1 and 10.
- DO NOT wrap in ```json``` blocks or any other formatting."""
    
    try:
        # Use faster model for evaluation
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": rating_prompt}],
            temperature=0.3,
            max_tokens=150,
            timeout=15
        )
        
        # Get response content
        response_content = response.choices[0].message.content
        
        if not response_content or response_content.strip() == "":
            logger.error("Empty response from OpenAI API")
            raise ValueError("Empty response from OpenAI")
        
        logger.debug(f"Raw OpenAI response: '{response_content}'")
        
        # Extract JSON using robust method
        ratings = extract_json_from_response(response_content)
        
        if ratings is None:
            logger.error(f"Could not extract JSON from response: '{response_content}'")
            raise ValueError("Failed to extract ratings from response")
        
        # Validate and normalize ratings
        normalized_ratings = validate_and_normalize_ratings(ratings)
        
        if normalized_ratings is None:
            logger.error(f"Invalid ratings structure: {ratings}")
            raise ValueError("Invalid ratings structure")
        
        result = normalized_ratings
        
        # Cache the result
        _evaluation_cache[cache_key] = result
        
        # Limit cache size to prevent memory issues
        if len(_evaluation_cache) > 1000:
            # Remove oldest entries (first 100)
            oldest_keys = list(_evaluation_cache.keys())[:100]
            for key in oldest_keys:
                del _evaluation_cache[key]
            logger.debug(f"Cleaned evaluation cache, removed {len(oldest_keys)} entries")
        
        logger.debug(f"Successfully evaluated response with ratings: {result}")
        return result
        
    except openai.error.Timeout:
        logger.error("OpenAI API timeout during response evaluation")
    except openai.error.RateLimitError:
        logger.error("OpenAI API rate limit exceeded during response evaluation")
    except openai.error.APIError as e:
        logger.error(f"OpenAI API error during response evaluation: {str(e)}")
    except openai.error.InvalidRequestError as e:
        logger.error(f"Invalid OpenAI API request during response evaluation: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error evaluating response: {str(e)}", exc_info=True)
    
    # Fallback ratings based on difficulty level
    if difficulty_level.lower() == "beginner":
        fallback_ratings = {
            "technical": 5.5,
            "communication": 6.0,
            "problem_solving": 5.5,
            "time_management": 6.0,
            "overall": 5.8
        }
    elif difficulty_level.lower() == "advanced":
        fallback_ratings = {
            "technical": 6.5,
            "communication": 6.0,
            "problem_solving": 6.5,
            "time_management": 6.0,
            "overall": 6.3
        }
    else:  # medium or default
        fallback_ratings = {
            "technical": 6.0,
            "communication": 6.0,
            "problem_solving": 6.0,
            "time_management": 6.0,
            "overall": 6.0
        }
    
    logger.warning(f"Using fallback ratings for {difficulty_level} level: {fallback_ratings}")
    
    # Cache the fallback result
    _evaluation_cache[cache_key] = fallback_ratings
    return fallback_ratings
def generate_interview_report(interview_data):
    try:
        duration = "N/A"
        if interview_data.get('start_time') and interview_data.get('end_time'):
            try:
                if isinstance(interview_data['start_time'], str):
                    interview_data['start_time'] = datetime.fromisoformat(interview_data['start_time'])
                if isinstance(interview_data['end_time'], str):
                    interview_data['end_time'] = datetime.fromisoformat(interview_data['end_time'])
                total_secs = (interview_data['end_time'] - interview_data['start_time']).total_seconds()
                m, s = divmod(int(total_secs), 60)
                duration = f"{m}m {s}s"
            except Exception as e:
                logger.error(f"Error calculating duration: {str(e)}")
                duration = "N/A"
        avg_rating = 0.0
        if interview_data.get('ratings'):
            try:
                avg_rating = sum(r['overall'] for r in interview_data['ratings']) / len(interview_data['ratings'])
            except Exception as e:
                logger.error(f"Error calculating average rating: {str(e)}")
                avg_rating = 0.0
        avg_percentage = avg_rating * 10
        if avg_percentage >= 75:
            status = "Very Good"
            status_class = "status-Very-Good"
        elif avg_percentage >= 60:
            status = "Good"
            status_class = "status-Good"
        elif avg_percentage >= 50:
            status = "Average"
            status_class = "status-Average"
        else:
            status = "Poor"
            status_class = "status-Poor"
        conversation_history = []
        try:
            conversation_history = "\n".join(
                f"{item['speaker']}: {item['text']}" 
                for item in interview_data.get('conversation_history', [])
                if isinstance(item, dict) and 'speaker' in item and 'text' in item
            )
        except Exception as e:
            logger.error(f"Error preparing conversation history: {str(e)}")
            conversation_history = "Could not load conversation history"
        report_prompt = f"""
Generate a professional interview performance report focusing ONLY on strengths and areas for improvement. 
The report should be structured with clear sections and use only information from the interview.
Interview Difficulty Level: {interview_data['difficulty_level'].capitalize()}
Interview Duration: {duration}
Format the report with these EXACT sections:
<h2>Key Strengths</h2>
<table class=\"report-table\">
<tr><th>Area</th><th>Examples</th><th>Rating</th></tr>
[Create table rows for each strength with specific examples from interview]
</table>
<h2>Areas for Improvement</h2>
<table class=\"report-table\">
<tr><th>Area</th><th>Suggestions</th></tr>
[Create table rows for each improvement area with actionable suggestions]
</table>
Conversation Transcript:
{conversation_history}
"""
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": report_prompt}],
            temperature=0.5,
            max_tokens=1200,  # Reduced from 1500
            timeout=60  # Add timeout
        )
        report_content = response.choices[0].message.content
        rating_prompt = f"""
Based on this interview transcript, provide a JSON object with ratings (1-10) and analysis for:
1. Technical Knowledge (accuracy, depth)
2. Communication Skills (clarity, articulation)
3. Problem Solving (logic, creativity)
4. Time Management (conciseness)
5. Overall Performance (composite)
For each category, include:
- rating (1-10) - IMPORTANT: Use ONLY numbers from 1 to 10 (inclusive)
- strengths (bullet points)
- improvement_suggestions (bullet points)
Format:
{{
  "technical_knowledge": {{"rating": number, "strengths": [], "improvement_suggestions": []}},
  "communication_skills": {{"rating": number, "strengths": [], "improvement_suggestions": []}},
  "problem_solving": {{"rating": number, "strengths": [], "improvement_suggestions": []}},
  "time_management": {{"rating": number, "strengths": [], "improvement_suggestions": []}},
  "overall_performance": {{"rating": number}}
}}
IMPORTANT: All ratings must be numbers between 1 and 10 (inclusive). Do not use any other scale.
Transcript:
{conversation_history}
"""
        rating_response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": rating_prompt}],
            temperature=0.3,
            max_tokens=800,  # Add max tokens
            timeout=30  # Add timeout
        )
        try:
            category_ratings = json.loads(rating_response.choices[0].message.content)
            for category in category_ratings:
                if 'rating' in category_ratings[category]:
                    rating_value = float(category_ratings[category]['rating'])
                    # Validate and ensure ratings are on 1-10 scale
                    if rating_value < 1 or rating_value > 10:
                        if 1 <= rating_value <= 5:
                            # Assume it's on a 1-5 scale, scale to 1-10
                            rating_value = rating_value * 2
                        else:
                            # Clamp to valid range
                            rating_value = max(1, min(10, rating_value))
                        logger.warning(f"Report rating {category} was {category_ratings[category]['rating']}, adjusted to {rating_value}")
                    category_ratings[category]['rating'] = rating_value
        except:
            category_ratings = {
                "technical_knowledge": {"rating": float(avg_rating), "strengths": [], "improvement_suggestions": []},
                "communication_skills": {"rating": float(avg_rating), "strengths": [], "improvement_suggestions": []},
                "problem_solving": {"rating": float(avg_rating), "strengths": [], "improvement_suggestions": []},
                "time_management": {"rating": float(avg_rating), "strengths": [], "improvement_suggestions": []},
                "overall_performance": {"rating": float(avg_rating)}
            }
        visual_feedback = {
            "professional_appearance": "No visual feedback collected",
            "body_language": "No visual feedback collected",
            "environment": "No visual feedback collected",
            "distractions": "No visual feedback collected",
            "summary": "No visual feedback was collected during this interview"
        }
        detailed_visual_html = ""
        if interview_data.get('visual_feedback_data'):
            try:
                professional_appearance = []
                body_language = []
                facial_expressions = []
                environment = []
                distractions = []
                for feedback in interview_data['visual_feedback_data']:
                    if isinstance(feedback, dict) and 'feedback' in feedback:
                        visual_data = feedback['feedback']
                        if isinstance(visual_data, dict):
                            pa = visual_data.get('professional_appearance', 'No feedback')
                            bl = visual_data.get('body_language', 'No feedback')
                            fe = visual_data.get('facial_expressions', visual_data.get('facial_expression', 'No feedback'))
                            env = visual_data.get('environment', 'No feedback')
                            dist = visual_data.get('distractions', 'No feedback')
                            professional_appearance.append(pa)
                            body_language.append(bl)
                            facial_expressions.append(fe)
                            environment.append(env)
                            distractions.append(dist)
                def most_common_feedback(feedback_list):
                    if not feedback_list:
                        return "No feedback available"
                    filtered = [f for f in feedback_list if f and f.lower() != 'no feedback' and 'not fully clear' not in f.lower()]
                    if not filtered:
                        return "No feedback available"
                    counts = Counter(filtered)
                    return counts.most_common(1)[0][0]
                def build_detailed_text(feedback_list):
                    if not feedback_list:
                        return "No feedback available."
                    filtered = [f for f in feedback_list if f and f.lower() != 'no feedback' and 'not fully clear' not in f.lower()]
                    if not filtered:
                        return "No feedback available."
                    counts = Counter(filtered)
                    total = sum(counts.values())
                    top_items = counts.most_common(3)
                    parts = []
                    # Primary observation with frequency
                    primary, primary_count = top_items[0]
                    parts.append(f"'{primary}' ({primary_count} of {total}).")
                    # Secondary observations
                    if len(top_items) > 1:
                        others = [f"'{item}'" for item, _ in top_items[1:]]
                        parts.append("Also noted: " + ", ".join(others) + ".")
                    # Diversity note
                    if len(counts) > 3:
                        parts.append(f"Additional unique observations: {len(counts) - 3}.")
                    return " ".join(parts)
                visual_feedback = {
                    "professional_appearance": most_common_feedback(professional_appearance),
                    "body_language": most_common_feedback(body_language),
                    "facial_expressions": most_common_feedback(facial_expressions),
                    "environment": most_common_feedback(environment),
                    "distractions": most_common_feedback(distractions),
                    "summary": "Visual feedback collected during this interview"
                }
                # Build detailed, narrative visual feedback in Aspect/Feedback 2-column table
                # Try using the LLM to craft concise, professional paragraphs per aspect using only this interview's observations
                visual_detail_json = None
                try:
                    pa_list = "\n- ".join(professional_appearance or ["No feedback"])
                    bl_list = "\n- ".join(body_language or ["No feedback"])
                    fe_list = "\n- ".join(facial_expressions or ["No feedback"])
                    env_list = "\n- ".join(environment or ["No feedback"])
                    dist_list = "\n- ".join(distractions or ["No feedback"])
                    narrative_prompt = f"""
                                Create four short, professional paragraphs (2-4 sentences each) that summarize interview visual observations.
                                Use ONLY these observations collected during THIS interview for each category.
                                Return strict JSON with keys: professional_appearance, body_language, facial_expressions, environment, distractions.

                                Professional Appearance observations:\n- {pa_list}

                                Body Language observations:\n- {bl_list}

                                Facial Expressions observations:\n- {fe_list}

                                Environment observations:\n- {env_list}

                                Distractions observations:\n- {dist_list}

                                Constraints:
                                - Be factual and neutral.
                                - Do not invent details.
                                - If observations are sparse, write a brief, honest sentence.
                                - Return ONLY JSON.
                                """
                    response = openai.ChatCompletion.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": narrative_prompt}],
                        temperature=0.4,
                        max_tokens=500,
                        timeout=20
                    )
                    visual_detail_json = json.loads(response.choices[0].message.content)
                except Exception:
                    # Fallback to deterministic summaries if JSON generation fails
                    visual_detail_json = {
                        "professional_appearance": build_detailed_text(professional_appearance),
                        "body_language": build_detailed_text(body_language),
                        "facial_expressions": build_detailed_text(facial_expressions),
                        "environment": build_detailed_text(environment),
                        "distractions": build_detailed_text(distractions)
                    }

                detailed_visual_html = """
                    <div class=\"report-section\">
                    <h3>Visual Feedback Summary</h3>
                    <table class=\"report-table\">
                        <tr>
                        <th>Aspect</th>
                        <th>Feedback</th>
                        </tr>
                        <tr><td>Appearance</td><td>{pa_paragraph}</td></tr>
                        <tr><td>Body Language</td><td>{bl_paragraph}</td></tr>
                        <tr><td>Facial Expressions</td><td>{fe_paragraph}</td></tr>
                        <tr><td>Setting</td><td>{env_paragraph}</td></tr>
                    </table>
                    </div>
                    """.format(
                    pa_paragraph=visual_detail_json.get("professional_appearance", build_detailed_text(professional_appearance)),
                    bl_paragraph=visual_detail_json.get("body_language", build_detailed_text(body_language)),
                    fe_paragraph=visual_detail_json.get("facial_expressions", build_detailed_text(facial_expressions)),
                    env_paragraph=visual_detail_json.get("environment", build_detailed_text(environment))
                )
            except Exception as e:
                logger.error(f"Error processing visual feedback: {str(e)}")
        summary_card = f"""
            <div class=\"interview-summary-card\" style="
                display: flex;
                justify-content: space-between;
                background: linear-gradient(135deg,#6e8efb,#a777e3);
                padding: 1rem;
                border-radius: 8px;
                color: white;
                margin-bottom: 1rem;
                font-family: sans-serif;
            ">
            <div>
                <div><small>Candidate Name</small><br><strong>{interview_data['student_info'].get('name', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Roll No</small><br><strong>{interview_data['student_info'].get('roll_no', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Batch No</small><br><strong>{interview_data['student_info'].get('batch_no', 'N/A')}</strong></div>
            </div>
            <div>
                <div><small>Center</small><br><strong>{interview_data['student_info'].get('center', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Course</small><br><strong>{interview_data['student_info'].get('course', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Evaluation Date</small><br><strong>{interview_data['student_info'].get('eval_date', 'N/A')}</strong></div>
            </div>
            <div style="align-self:center;">
                <span class=\"{status_class}\" style="
                    background: gold;
                    color: black;
                    padding: 0.5rem 1rem;
                    border-radius: 999px;
                    font-weight: bold;
                ">{status}</span>
            </div>
            </div>
            """
        # Append detailed visual section (strictly based on this interview) to the LLM report
        full_report_html = summary_card + report_content + detailed_visual_html
        return {
            "status": "success",
            "report_html": full_report_html,
            "category_ratings": category_ratings,
            # "voice_feedback": voice_feedback,
            # "voice_audio": voice_audio,
            "status_class": status_class,
            "visual_feedback": visual_feedback
        }
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }