import base64
import cv2
import numpy as np
import logging
import openai
import json
import hashlib
from datetime import datetime
from backend.utils.performance_utils import timing_decorator

logger = logging.getLogger(__name__)

MAX_FRAME_SIZE = 500

# Cache for visual analysis results with candidate context
_visual_cache = {}

# Track previous observations per candidate to avoid repetition
_candidate_history = {}

def _get_visual_cache_key(frame_base64, candidate_context="", timestamp=""):
    """Generate a more unique cache key for visual analysis"""
    # Use more of the base64 string and include timestamp for uniqueness
    content = f"{frame_base64[:200]}_{candidate_context}_{timestamp}"
    return hashlib.md5(content.encode()).hexdigest()

def process_frame_for_gpt4v(frame):
    try:
        height, width = frame.shape[:2]
        if height > MAX_FRAME_SIZE or width > MAX_FRAME_SIZE:
            scale = MAX_FRAME_SIZE / max(height, width)
            frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        base64_str = base64.b64encode(buffer).decode('utf-8')
        return base64_str
    except Exception as e:
        logger.error(f"Error processing frame for GPT-4V: {str(e)}")
        return ""

def extract_json_from_response(response_content):
    """Extract JSON from various response formats"""
    if not response_content or response_content.strip() == "":
        return None
    
    response_content = response_content.strip()
    
    # Method 1: Try direct JSON parsing
    try:
        return json.loads(response_content)
    except json.JSONDecodeError:
        pass
    
    # Method 2: Extract from markdown code blocks
    import re
    json_patterns = [
        r'```json\s*([\s\S]*?)\s*```',
        r'```\s*([\s\S]*?)\s*```',
        r'`([^`]+)`'
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
    
    return None

@timing_decorator("Visual Analysis")
def analyze_visual_response(frame_base64, conversation_context, candidate_info=None):
    """Enhanced visual analysis with better uniqueness and error handling"""
    
    # Extract candidate context for unique analysis
    candidate_context = ""
    candidate_name = "Unknown Candidate"
    candidate_id = "unknown"
    
    if candidate_info:
        candidate_name = candidate_info.get('name', 'Unknown Candidate')
        candidate_id = candidate_info.get('roll_no', candidate_info.get('email', 'unknown'))
        candidate_context = f"{candidate_name}_{candidate_id}"
    
    # Generate more unique cache key with timestamp
    current_timestamp = datetime.now().strftime("%H%M%S")
    cache_key = _get_visual_cache_key(frame_base64, candidate_context, current_timestamp)
    
    # Check cache first (but with shorter expiry for uniqueness)
    if cache_key in _visual_cache:
        logger.debug(f"Using cached visual analysis result for {candidate_name}")
        return _visual_cache[cache_key]
    
    if not frame_base64:
        logger.error(f"No frame data provided for {candidate_name} visual analysis")
        result = {
            "professional_appearance": f"No visual data available for {candidate_name} analysis",
            "body_language": f"No visual data available for {candidate_name} posture assessment", 
            "environment": f"No visual data available for {candidate_name} environment analysis",
            "distractions": f"No visual data available for {candidate_name} distraction assessment",
            "facial_expressions": f"No visual data available for {candidate_name} expression analysis"
        }
        return result  # Don't cache empty results
    
    try:
        # Get previous observations to ensure uniqueness
        previous_observations = _candidate_history.get(candidate_context, [])
        avoided_phrases = []
        
        if previous_observations:
            for prev in previous_observations[-2:]:
                for category, observation in prev.items():
                    if len(observation) > 20:
                        words = observation.lower().split()
                        key_phrases = [word for word in words if len(word) > 5 and 
                                     word not in ['professional', 'displaying', 'maintaining', 'showing']][:5]
                        avoided_phrases.extend(key_phrases)
        
        # Create enhanced prompt with better instructions
        current_time = datetime.now().strftime("%H:%M")
        
        # Fix the set slicing issue
        unique_avoided_phrases = list(set(avoided_phrases))[:10] if avoided_phrases else []
        
        enhanced_prompt = f"""
        You are analyzing a video frame from an interview with {candidate_name} (ID: {candidate_id}) at {current_time}.
        
        CRITICAL INSTRUCTIONS:
        1. Provide SPECIFIC, CONCRETE observations unique to THIS moment
        2. Use detailed visual descriptions: exact colors, patterns, textures, positioning
        3. Mention specific objects, clothing details, facial features, room elements
        4. Each description must be 25-40 words with specific visual evidence
        5. Be factual and avoid generic terms like "professional", "neat", "good"
        
        AVOID these recently used phrases: {', '.join(unique_avoided_phrases) if unique_avoided_phrases else 'none'}
        
        Analyze these aspects with SPECIFIC details:
        
        1. PROFESSIONAL APPEARANCE: Exact clothing (colors, patterns, style), accessories, grooming details
        2. BODY LANGUAGE: Precise posture (shoulder angle, hand position, head tilt, sitting/standing)  
        3. FACIAL EXPRESSIONS: Specific facial details (eye contact, smile, eyebrow position, overall expression)
        4. ENVIRONMENT: Background objects, wall colors, lighting source/direction, room setup, visible items
        5. DISTRACTIONS: Movement, objects, sounds, technical issues, background activity
        
        Return ONLY valid JSON with these exact keys:
        {{"professional_appearance": "specific clothing and appearance details with colors/patterns",
          "body_language": "exact posture description with spatial positioning",
          "facial_expressions": "detailed facial characteristics and expression specifics", 
          "environment": "specific background elements, colors, objects, and lighting details",
          "distractions": "concrete environmental factors and technical observations"}}
        
        Focus on what makes THIS candidate unique in THIS moment.
        """
        
        logger.info(f"Sending visual analysis request to OpenAI for {candidate_name}")
        
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": enhanced_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{frame_base64}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            temperature=0.9,  # Higher temperature for more variety
            max_tokens=500,
            timeout=30
        )
        
        response_content = response.choices[0].message.content
        logger.info(f"OpenAI visual response for {candidate_name}: {response_content[:200]}...")
        
        # Extract JSON from response
        feedback = extract_json_from_response(response_content)
        
        if not feedback:
            logger.error(f"Failed to extract JSON from OpenAI response for {candidate_name}")
            raise ValueError("Invalid JSON response from OpenAI")
        
        # Validate response quality
        required_keys = ['professional_appearance', 'body_language', 'facial_expressions', 'environment', 'distractions']
        if not all(key in feedback for key in required_keys):
            logger.error(f"Missing required keys in response for {candidate_name}: {list(feedback.keys())}")
            raise ValueError("Incomplete response from OpenAI")
        
        # Check for generic responses and minimum length
        for key, value in feedback.items():
            if len(value) < 20:
                logger.warning(f"Response too short for {key}: '{value}'")
                raise ValueError(f"Response too generic for {key}")
            
            # Check for overly generic terms
            generic_terms = ['professional', 'neat', 'stable', 'clean', 'good', 'normal', 'typical']
            if sum(1 for term in generic_terms if term in value.lower()) > 2:
                logger.warning(f"Response too generic for {key}: '{value}'")
                raise ValueError(f"Response too generic for {key}")
        
        # Store in candidate history for future uniqueness
        if candidate_context:
            if candidate_context not in _candidate_history:
                _candidate_history[candidate_context] = []
            _candidate_history[candidate_context].append(feedback)
            
            # Keep only last 5 observations per candidate
            if len(_candidate_history[candidate_context]) > 5:
                _candidate_history[candidate_context] = _candidate_history[candidate_context][-5:]
        
        # Cache the result (with limited cache size)
        _visual_cache[cache_key] = feedback
        
        # Limit cache size to prevent memory issues
        if len(_visual_cache) > 100:  # Reduced cache size for more uniqueness
            oldest_keys = list(_visual_cache.keys())[:20]
            for key in oldest_keys:
                del _visual_cache[key]
        
        logger.info(f"Successfully generated unique visual feedback for {candidate_name}")
        return feedback
        
    except Exception as e:
        logger.error(f"Error in visual analysis for {candidate_name}: {str(e)}", exc_info=True)
        
        # Return more specific error-based response instead of generic local analysis
        current_time = datetime.now().strftime("%H:%M")
        error_result = {
            "professional_appearance": f"Visual analysis temporarily unavailable for {candidate_name} at {current_time} - technical processing issue",
            "body_language": f"Posture assessment for {candidate_name} limited at {current_time} - analysis system temporarily unavailable", 
            "environment": f"Background analysis for {candidate_name} incomplete at {current_time} - processing difficulties encountered",
            "distractions": f"Distraction evaluation for {candidate_name} inconclusive at {current_time} - technical analysis limitations",
            "facial_expressions": f"Expression analysis for {candidate_name} unavailable at {current_time} - visual processing temporarily impaired"
        }
        
        # Don't cache error results to allow retry
        return error_result

def get_candidate_visual_summary(candidate_info):
    """Get comprehensive visual summary for a candidate across all observations"""
    if not candidate_info:
        return None
        
    candidate_name = candidate_info.get('name', 'Unknown')
    candidate_id = candidate_info.get('roll_no', candidate_info.get('email', 'unknown'))
    candidate_context = f"{candidate_name}_{candidate_id}"
    
    if candidate_context not in _candidate_history:
        return None
    
    observations = _candidate_history[candidate_context]
    if not observations:
        return None
    
    # Get the most recent and detailed observations
    summary = {}
    for category in ['professional_appearance', 'body_language', 'environment', 'distractions', 'facial_expressions']:
        category_obs = []
        for obs in observations:
            if category in obs and obs[category] and len(obs[category]) > 20:
                category_obs.append(obs[category])
        
        if category_obs:
            # Get most recent detailed observation
            summary[category] = category_obs[-1]  # Most recent
        else:
            summary[category] = f"Insufficient {category.replace('_', ' ')} observations recorded for {candidate_name}"
    
    return summary

def clear_candidate_cache(candidate_info=None):
    """Clear cache for specific candidate or all candidates"""
    if candidate_info:
        candidate_name = candidate_info.get('name', 'Unknown')
        candidate_id = candidate_info.get('roll_no', candidate_info.get('email', 'unknown'))
        candidate_context = f"{candidate_name}_{candidate_id}"
        
        # Clear history
        _candidate_history.pop(candidate_context, None)
        
        # Clear related cache entries
        keys_to_remove = [k for k in _visual_cache.keys() if candidate_context in k]
        for key in keys_to_remove:
            del _visual_cache[key]
            
        logger.info(f"Cleared visual cache for {candidate_name}")
    else:
        _candidate_history.clear()
        _visual_cache.clear()
        logger.info("Cleared all visual caches")