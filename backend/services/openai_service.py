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
def evaluate_response(answer, question, difficulty_level, visual_feedback=None, max_retries=3):
    """
    Evaluate interview response using OpenAI API with dynamic rating based on content only
    No hard-coded fallback ratings - all ratings must be earned through actual evaluation
    """
    # Check cache first
    cache_key = _get_cache_key(answer, question, difficulty_level)
    if cache_key in _evaluation_cache:
        logger.debug("Using cached evaluation result")
        return _evaluation_cache[cache_key]
    
    # Validate inputs
    if not answer or not answer.strip():
        logger.error("Empty or null answer provided")
        return None
    
    if not question or not question.strip():
        logger.error("Empty or null question provided") 
        return None
    
    # Clean and prepare inputs
    answer = answer.strip()
    question = question.strip()
    
    # Enhanced prompt that forces contextual evaluation
    rating_prompt = f"""You are an experienced technical interviewer. Rate this interview response on a scale of 1-10 for each category based SOLELY on the content quality and appropriateness.

DIFFICULTY LEVEL: {difficulty_level.upper()}

QUESTION: "{question}"

CANDIDATE'S ANSWER: "{answer}"

EVALUATION CRITERIA:
For {difficulty_level} level expectations:

TECHNICAL (1-10):
- Accuracy of information provided
- Depth appropriate for {difficulty_level} level
- Use of relevant terminology
- Demonstration of understanding

COMMUNICATION (1-10):  
- Clarity and coherence of explanation
- Logical flow of ideas
- Appropriate language use
- Completeness of response

PROBLEM_SOLVING (1-10):
- Analytical thinking demonstrated  
- Approach to breaking down the problem
- Creative or logical solutions proposed
- Evidence of systematic thinking

TIME_MANAGEMENT (1-10):
- Relevance to the question asked
- Appropriate level of detail for the question
- Efficiency in conveying key points
- Avoiding unnecessary tangents

OVERALL (1-10):
- Composite assessment of the response
- Would this answer satisfy the interviewer?
- Overall impression of candidate's capability

RATING GUIDELINES:
1-2: Poor/Incorrect - Major issues, wrong information
3-4: Below Average - Some understanding but significant gaps
5-6: Average - Meets basic expectations, adequate response  
7-8: Good - Solid understanding, well-articulated
9-10: Excellent - Exceptional insight, comprehensive answer

CRITICAL REQUIREMENTS:
- Base ratings ONLY on the actual content provided
- Consider the {difficulty_level} difficulty level in your expectations
- Use decimal points (e.g., 6.5, 7.2) for nuanced scoring
- Return ONLY valid JSON: {{"technical": X.X, "communication": X.X, "problem_solving": X.X, "time_management": X.X, "overall": X.X}}
- No markdown formatting or explanation text"""

    for attempt in range(max_retries):
        try:
            logger.info(f"Attempting response evaluation (attempt {attempt + 1}/{max_retries})")
            
            response = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": rating_prompt}],
                temperature=0.1,  # Very low temperature for consistent evaluation
                max_tokens=200,
                timeout=20
            )
            
            response_content = response.choices[0].message.content
            
            if not response_content or response_content.strip() == "":
                logger.warning(f"Empty response from OpenAI on attempt {attempt + 1}")
                continue
            
            logger.debug(f"OpenAI response (attempt {attempt + 1}): '{response_content[:100]}...'")
            
            # Extract and validate JSON
            ratings = extract_json_from_response(response_content)
            
            if ratings is None:
                logger.warning(f"Could not extract JSON on attempt {attempt + 1}: '{response_content}'")
                continue
            
            # Validate structure
            required_keys = ['technical', 'communication', 'problem_solving', 'time_management', 'overall']
            if not all(key in ratings for key in required_keys):
                logger.warning(f"Missing keys on attempt {attempt + 1}: {list(ratings.keys())}")
                continue
            
            # Validate and clean ratings
            normalized_ratings = {}
            validation_passed = True
            
            for key, value in ratings.items():
                if key not in required_keys:
                    continue
                    
                try:
                    rating_value = float(value)
                    
                    # Strict validation - must be in valid range
                    if rating_value < 1.0 or rating_value > 10.0:
                        logger.warning(f"Rating {key} out of range on attempt {attempt + 1}: {value}")
                        validation_passed = False
                        break
                    
                    # Round to 1 decimal place
                    normalized_ratings[key] = round(rating_value, 1)
                    
                except (ValueError, TypeError):
                    logger.warning(f"Invalid rating value for {key} on attempt {attempt + 1}: {value}")
                    validation_passed = False
                    break
            
            if not validation_passed:
                continue
            
            # Final validation - check for reasonable distribution
            rating_values = list(normalized_ratings.values())
            avg_rating = sum(rating_values) / len(rating_values)
            rating_range = max(rating_values) - min(rating_values)
            
            # Log suspicious patterns but don't reject them
            if avg_rating > 9.0:
                logger.warning(f"High average rating detected: {avg_rating:.1f}")
            elif avg_rating < 2.0:
                logger.warning(f"Low average rating detected: {avg_rating:.1f}")
            
            if rating_range < 0.5:
                logger.info(f"Very consistent ratings (range: {rating_range:.1f})")
            elif rating_range > 6.0:
                logger.warning(f"Wide rating range detected: {rating_range:.1f}")
            
            # Success - cache and return
            _evaluation_cache[cache_key] = normalized_ratings
            
            # Manage cache size
            if len(_evaluation_cache) > 1000:
                oldest_keys = list(_evaluation_cache.keys())[:100]
                for key in oldest_keys:
                    del _evaluation_cache[key]
                logger.debug("Cleaned evaluation cache")
            
            logger.info(f"Successfully evaluated response: avg={avg_rating:.1f}, range={rating_range:.1f}")
            return normalized_ratings
            
        except openai.error.Timeout:
            logger.warning(f"OpenAI timeout on attempt {attempt + 1}")
            continue
        except openai.error.RateLimitError:
            logger.warning(f"Rate limit hit on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
            continue  
        except openai.error.APIError as e:
            logger.warning(f"API error on attempt {attempt + 1}: {str(e)}")
            continue
        except Exception as e:
            logger.warning(f"Unexpected error on attempt {attempt + 1}: {str(e)}")
            continue
    
    # All attempts failed - return None instead of fake ratings
    logger.error(f"Failed to evaluate response after {max_retries} attempts")
    logger.error(f"Question: {question[:100]}...")
    logger.error(f"Answer: {answer[:100]}...")
    
    # Return None to indicate evaluation failure
    # The calling code should handle this appropriately
    return None


def evaluate_response_with_fallback(answer, question, difficulty_level, visual_feedback=None):
    """
    Wrapper function that handles evaluation failure gracefully
    Returns evaluation result or None if evaluation is impossible
    """
    try:
        result = evaluate_response(answer, question, difficulty_level, visual_feedback)
        
        if result is None:
            logger.error("Response evaluation completely failed - no ratings available")
            # You could implement alternative evaluation strategies here:
            # - Try with a simpler prompt
            # - Use a different model
            # - Implement rule-based scoring
            # - Return None and handle in UI
            
        return result
        
    except Exception as e:
        logger.error(f"Critical error in response evaluation: {str(e)}", exc_info=True)
        return None


def get_evaluation_status_message(evaluation_result):
    """
    Helper function to generate user-friendly messages for evaluation status
    """
    if evaluation_result is None:
        return {
            "status": "evaluation_failed",
            "message": "Unable to evaluate response due to technical difficulties. Please try again.",
            "show_ratings": False
        }
    
    avg_rating = sum(evaluation_result.values()) / len(evaluation_result)
    
    return {
        "status": "evaluation_success", 
        "message": f"Response evaluated successfully (Average: {avg_rating:.1f}/10)",
        "show_ratings": True,
        "ratings": evaluation_result
    }
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
        
        # Calculate average rating from individual response ratings
        valid_ratings = []
        if interview_data.get('ratings'):
            for rating in interview_data['ratings']:
                if rating and isinstance(rating, dict) and rating.get('overall'):
                    val = float(rating['overall'])
                    if val <= 5:
                        val = val * 2
                    valid_ratings.append(val)
        
        logger.info(f"Collected overall ratings: {valid_ratings}")
        avg_rating = sum(valid_ratings) / len(valid_ratings) if valid_ratings else 0.0
        
        # Convert to percentage (multiply by 10 since ratings are 1-10)
        avg_percentage = avg_rating * 10
        if avg_percentage >= 80:
            status = "Very Good"
            status_class = "status-Very-Good"
        elif avg_percentage >= 65:
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
        
        # Enhanced report prompt that explicitly asks for 1-10 scale
        report_prompt = f"""
Generate a professional interview performance report focusing ONLY on strengths and areas for improvement. 
The report should be structured with clear sections and use only information from the interview.

Interview Difficulty Level: {interview_data.get('difficulty_level', 'Medium').capitalize()}
Interview Duration: {duration}

Format the report with these EXACT sections:
<h2>Key Strengths</h2>
<table class="report-table">
<tr><th>Area</th><th>Examples</th><th>Rating</th></tr>
[Create table rows for each strength with specific examples from interview - Rate each area from 1-10 and display as "X/10"]
</table>

<h2>Areas for Improvement</h2>
<table class="report-table">
<tr><th>Area</th><th>Suggestions</th></tr>
[Create table rows for each improvement area with actionable suggestions]
</table>

IMPORTANT: For all ratings in the report table, use X/10 format (not X/5). Base ratings on interview content only.

Conversation Transcript:
{conversation_history}
"""
        
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": report_prompt}],
            temperature=0.5,
            max_tokens=1200,
            timeout=60
        )
        report_content = response.choices[0].message.content
        
        # Enhanced rating prompt that explicitly enforces 1-10 scale
        rating_prompt = f"""
Based on this interview transcript, provide a JSON object with ratings (1-10) and analysis for:
1. Technical Knowledge (accuracy, depth)
2. Communication Skills (clarity, articulation)  
3. Problem Solving (logic, creativity)
4. Time Management (conciseness)
5. Overall Performance (composite)

For each category, include:
- rating (1-10) - CRITICAL: Use ONLY numbers from 1 to 10 (inclusive). NO OTHER SCALE.
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

CRITICAL REQUIREMENTS:
- All ratings must be whole numbers or decimals between 1.0 and 10.0 (inclusive)
- DO NOT use any 1-5 scale - everything must be 1-10
- If you think a rating should be 4/5, convert it to 8/10
- Rate based on the {interview_data.get('difficulty_level', 'medium')} difficulty level

Transcript:
{conversation_history}
"""
        
        rating_response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": rating_prompt}],
            temperature=0.3,
            max_tokens=800,
            timeout=30
        )
        
        try:
            category_ratings = extract_json_from_response(rating_response.choices[0].message.content)
            
            # STRICT validation - ensure ALL ratings are 1-10 scale
            for category in category_ratings:
                if 'rating' in category_ratings[category]:
                    rating_value = float(category_ratings[category]['rating'])
                    
                    # ONLY accept ratings in 1-10 range
                    if rating_value < 1.0 or rating_value > 10.0:
                        logger.error(f"Invalid rating for {category}: {rating_value}. Must be 1-10.")
                        # Use average from individual response ratings as fallback
                        rating_value = avg_rating if avg_rating > 0 else 5.0
                        logger.warning(f"Using fallback rating for {category}: {rating_value}")
                    
                    # Ensure it's properly rounded
                    category_ratings[category]['rating'] = round(rating_value, 1)
                    
        except Exception as e:
            logger.error(f"Error parsing category ratings: {str(e)}")
            # Fallback ratings based on individual response ratings
            fallback_rating = avg_rating if avg_rating > 0 else 5.0
            category_ratings = {
                "technical_knowledge": {"rating": fallback_rating, "strengths": ["Based on interview responses"], "improvement_suggestions": ["Continue practicing technical concepts"]},
                "communication_skills": {"rating": fallback_rating, "strengths": ["Participated in interview"], "improvement_suggestions": ["Work on clarity and structure"]},
                "problem_solving": {"rating": fallback_rating, "strengths": ["Attempted to solve problems"], "improvement_suggestions": ["Practice systematic problem solving"]},
                "time_management": {"rating": fallback_rating, "strengths": ["Completed interview"], "improvement_suggestions": ["Focus on concise responses"]},
                "overall_performance": {"rating": fallback_rating}
            }
        
        # Process visual feedback (complete implementation)
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
                
                # Build detailed, narrative visual feedback
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

                detailed_visual_html = f"""
                    <div class="report-section">
                    <h3>Visual Feedback Summary</h3>
                    <table class="report-table">
                        <tr>
                        <th>Aspect</th>
                        <th>Feedback</th>
                        </tr>
                        <tr><td>Appearance</td><td>{visual_detail_json.get("professional_appearance", build_detailed_text(professional_appearance))}</td></tr>
                        <tr><td>Body Language</td><td>{visual_detail_json.get("body_language", build_detailed_text(body_language))}</td></tr>
                        <tr><td>Facial Expressions</td><td>{visual_detail_json.get("facial_expressions", build_detailed_text(facial_expressions))}</td></tr>
                        <tr><td>Setting</td><td>{visual_detail_json.get("environment", build_detailed_text(environment))}</td></tr>
                    </table>
                    </div>
                    """
            except Exception as e:
                logger.error(f"Error processing visual feedback: {str(e)}")
        
        # Create summary card
        summary_card = f"""
            <div class="interview-summary-card" style="
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
                <div><small>Candidate Name</small><br><strong>{interview_data.get('student_info', {}).get('name', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Roll No</small><br><strong>{interview_data.get('student_info', {}).get('roll_no', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Batch No</small><br><strong>{interview_data.get('student_info', {}).get('batch_no', 'N/A')}</strong></div>
            </div>
            <div>
                <div><small>Center</small><br><strong>{interview_data.get('student_info', {}).get('center', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Course</small><br><strong>{interview_data.get('student_info', {}).get('course', 'N/A')}</strong></div>
                <div style="margin-top:0.5rem;"><small>Evaluation Date</small><br><strong>{interview_data.get('student_info', {}).get('eval_date', 'N/A')}</strong></div>
            </div>
            <div style="align-self:center;">
                <span class="{status_class}" style="
                    background: gold;
                    color: black;
                    padding: 0.5rem 1rem;
                    border-radius: 999px;
                    font-weight: bold;
                ">{status}</span>
            </div>
            </div>
            """
        
        full_report_html = summary_card + report_content + detailed_visual_html
        
        # Log final ratings to verify they're all 1-10
        for category, data in category_ratings.items():
            if 'rating' in data:
                logger.info(f"Final rating for {category}: {data['rating']}/10")
        
        return {
            "status": "success",
            "report_html": full_report_html,
            "category_ratings": category_ratings,
            "status_class": status_class,
            "visual_feedback": visual_feedback
        }
        
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e)
        }