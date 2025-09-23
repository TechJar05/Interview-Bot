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

def _get_visual_cache_key(frame_base64, candidate_context=""):
    """Generate a cache key for visual analysis including candidate context"""
    content = f"{frame_base64[:100]}_{candidate_context}"
    return hashlib.md5(content.encode()).hexdigest()

def process_frame_for_gpt4v(frame):
    try:
        height, width = frame.shape[:2]
        if height > MAX_FRAME_SIZE or width > MAX_FRAME_SIZE:
            scale = MAX_FRAME_SIZE / max(height, width)
            frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])  # Slightly higher quality
        base64_str = base64.b64encode(buffer).decode('utf-8')
        return base64_str
    except Exception as e:
        logger.error(f"Error processing frame for GPT-4V: {str(e)}")
        return ""

def _analyze_frame_locally(frame_base64: str, candidate_context="", candidate_name="Unknown") -> dict:
    """Enhanced local frame analysis with candidate-specific observations"""
    try:
        img_bytes = base64.b64decode(frame_base64)
        np_arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Failed to decode image")
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = img.shape[:2]
        
        # Enhanced brightness analysis
        mean_brightness = float(np.mean(gray))
        brightness_std = float(np.std(gray))
        
        # Enhanced edge detection for background complexity
        edges = cv2.Canny(gray, 100, 200)
        edge_density = float(np.count_nonzero(edges)) / float(edges.size)
        
        # Face region analysis (upper third of frame)
        face_region = gray[:h//3, :]
        face_brightness = float(np.mean(face_region))
        
        # Background analysis (excluding center region)
        mask = np.ones_like(gray, dtype=bool)
        center_h, center_w = h//3, w//3
        mask[h//2-center_h//2:h//2+center_h//2, w//2-center_w//2:w//2+center_w//2] = False
        bg_region = gray[mask]
        bg_complexity = float(np.std(bg_region)) if len(bg_region) > 0 else 0
        
        # Color analysis for more specific descriptions
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        dominant_hue = float(np.median(hsv[:,:,0]))
        saturation = float(np.mean(hsv[:,:,1]))
        
        # Generate candidate-specific descriptions based on measurements
        current_time = datetime.now()
        time_context = f"at {current_time.strftime('%H:%M')}"
        
        # Professional appearance with specific details and candidate context
        if face_brightness >= 140 and brightness_std < 40:
            if saturation > 80:  # Colorful clothing
                appearance_desc = f"{candidate_name} wearing vibrant attire with excellent facial lighting {time_context}"
            else:
                appearance_desc = f"{candidate_name} in neutral-toned clothing, well-lit professional appearance {time_context}"
        elif face_brightness >= 120:
            appearance_desc = f"{candidate_name} displaying professional attire with adequate lighting conditions {time_context}"
        elif face_brightness >= 100:
            appearance_desc = f"{candidate_name} maintaining neat presentation despite moderate lighting {time_context}"
        else:
            appearance_desc = f"{candidate_name}'s appearance assessment limited by dim lighting conditions {time_context}"
        
        # Body language with specific posture indicators and candidate name
        center_region = gray[h//3:2*h//3, w//3:2*w//3]
        center_edges = cv2.Canny(center_region, 50, 150)
        posture_metric = float(np.count_nonzero(center_edges)) / float(center_edges.size)
        
        if posture_metric <= 0.08:
            posture_desc = f"{candidate_name} maintaining steady, upright seating position throughout {time_context}"
        elif posture_metric <= 0.15:
            posture_desc = f"{candidate_name} exhibiting stable posture with occasional minor adjustments {time_context}"
        elif posture_metric <= 0.25:
            posture_desc = f"{candidate_name} showing active engagement through moderate body movements {time_context}"
        else:
            posture_desc = f"{candidate_name} demonstrating dynamic posture with frequent positional changes {time_context}"
        
        # Environment with candidate-specific context
        if bg_complexity <= 20 and mean_brightness >= 130:
            env_desc = f"{candidate_name} positioned in clean, well-illuminated professional setting {time_context}"
        elif bg_complexity <= 35:
            env_desc = f"{candidate_name} in organized environment with consistent ambient lighting {time_context}"
        elif bg_complexity <= 50:
            env_desc = f"{candidate_name} situated in moderately detailed background with acceptable lighting {time_context}"
        else:
            env_desc = f"{candidate_name} in complex environmental setup with varied lighting elements {time_context}"
        
        # Distractions with specific indicators
        if edge_density <= 0.1 and bg_complexity <= 25:
            distraction_desc = f"Minimal background distractions observed in {candidate_name}'s frame {time_context}"
        elif edge_density <= 0.18:
            distraction_desc = f"Some background elements present behind {candidate_name} but non-disruptive {time_context}"
        else:
            distraction_desc = f"Multiple visual elements creating complexity in {candidate_name}'s background {time_context}"
        
        # Facial expression analysis with candidate specificity
        face_edges = cv2.Canny(face_region, 30, 100)
        expression_metric = float(np.count_nonzero(face_edges)) / float(face_edges.size)
        
        if expression_metric <= 0.12:
            expression_desc = f"{candidate_name} displaying calm, composed facial expression {time_context}"
        elif expression_metric <= 0.20:
            expression_desc = f"{candidate_name} showing engaged expression with moderate facial activity {time_context}"
        else:
            expression_desc = f"{candidate_name} exhibiting animated facial expressions during interaction {time_context}"
        
        return {
            "professional_appearance": appearance_desc,
            "body_language": posture_desc,
            "environment": env_desc,
            "distractions": distraction_desc,
            "facial_expressions": expression_desc
        }
        
    except Exception as e:
        logger.error(f"Error in local frame analysis: {str(e)}")
        timestamp = datetime.now().strftime('%H:%M')
        candidate_name = candidate_name or "Candidate"
        return {
            "professional_appearance": f"{candidate_name}'s appearance analysis unavailable due to processing issue at {timestamp}",
            "body_language": f"{candidate_name}'s posture assessment limited by analysis error at {timestamp}",
            "environment": f"{candidate_name}'s environment details unclear due to technical error at {timestamp}",
            "distractions": f"Distraction evaluation for {candidate_name} inconclusive at {timestamp}",
            "facial_expressions": f"{candidate_name}'s expression analysis unavailable at {timestamp}"
        }

@timing_decorator("Visual Analysis")
def analyze_visual_response(frame_base64, conversation_context, candidate_info=None):
    """Enhanced visual analysis with candidate-specific context and uniqueness"""
    
    # Extract candidate context for unique analysis
    candidate_context = ""
    candidate_name = "Unknown Candidate"
    candidate_id = "unknown"
    
    if candidate_info:
        candidate_name = candidate_info.get('name', 'Unknown Candidate')
        candidate_id = candidate_info.get('roll_no', candidate_info.get('email', 'unknown'))
        candidate_context = f"{candidate_name}_{candidate_id}"
    
    # Generate cache key with candidate context
    cache_key = _get_visual_cache_key(frame_base64, candidate_context)
    
    # Check cache first
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
        _visual_cache[cache_key] = result
        return result
    
    try:
        # Check previous observations to avoid repetition
        previous_observations = _candidate_history.get(candidate_context, [])
        avoided_phrases = []
        
        if previous_observations:
            for prev in previous_observations[-2:]:  # Last 2 observations
                for category, observation in prev.items():
                    if len(observation) > 20:
                        # Extract key phrases to avoid
                        words = observation.split()
                        key_phrases = [word for word in words[2:6] if len(word) > 4]  # Skip first 2 words, take meaningful ones
                        avoided_phrases.extend(key_phrases)
        
        # Create enhanced prompt with candidate specificity and uniqueness requirements
        current_time = datetime.now().strftime("%H:%M on %B %d")
        
        enhanced_prompt = f"""
        Analyze this interview video frame for candidate {candidate_name} (ID: {candidate_id}) captured at {current_time}.

        CRITICAL REQUIREMENTS FOR UNIQUENESS:
        - Provide SPECIFIC, DETAILED observations unique to THIS candidate and moment
        - Use concrete visual details: colors, textures, positioning, lighting characteristics
        - Include specific measurements or spatial relationships when possible
        - Focus on distinguishing characteristics that make this candidate different
        - Each observation must be 20-30 words with concrete, observable details
        
        AVOID these generic terms: professional, neat, stable, clean, good, appears, seems, normal, typical
        """
        
        # Add phrase avoidance if we have previous observations
        if avoided_phrases:
            unique_phrases = list(set(avoided_phrases))[:8]  # Remove duplicates, limit to 8
            enhanced_prompt += f"\nAVOID these previously used phrases: {', '.join(unique_phrases)}"
        
        enhanced_prompt += f"""
        
        Analyze these specific aspects:
        
        1. PROFESSIONAL APPEARANCE: Exact clothing details (colors, styles, textures), grooming specifics, accessories, unique visual characteristics
        2. BODY LANGUAGE: Precise posture description (shoulder position, hand placement, head angle, torso alignment), movement patterns
        3. FACIAL EXPRESSIONS: Specific facial characteristics (eye direction, eyebrow position, mouth expression, overall engagement level)
        4. ENVIRONMENT: Detailed background elements (objects, colors, lighting direction and quality, room characteristics, camera setup)
        5. DISTRACTIONS: Specific environmental elements, movements, technical issues, visual complexity factors
        
        Return JSON with exactly these keys:
        {{"professional_appearance": "specific clothing and grooming details with colors and textures", 
          "body_language": "exact posture and positioning with spatial descriptions",
          "facial_expressions": "specific facial characteristics and expression details",
          "environment": "detailed background description with lighting and objects", 
          "distractions": "specific environmental elements and technical observations"}}
        
        Make each description unique to {candidate_name} and include concrete visual evidence.
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-4o",  # Use stronger model for better analysis
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": enhanced_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{frame_base64}",
                                "detail": "high"  # Request high detail analysis
                            }
                        }
                    ]
                }
            ],
            temperature=0.8,  # Higher temperature for more variety
            max_tokens=400,   # Increased for more detailed responses
            timeout=25
        )
        
        logger.debug(f"Raw OpenAI visual feedback response for {candidate_name}: {response.choices[0].message.content}")
        
        try:
            feedback = json.loads(response.choices[0].message.content)
            
            # Validate and enhance feedback quality
            for key, value in feedback.items():
                # Ensure minimum quality and length
                if len(value) < 20 or any(generic in value.lower() for generic in 
                                        ['professional', 'neat', 'stable', 'clean', 'good', 'appears', 'seems']):
                    # Use local analysis as backup with candidate context
                    local_analysis = _analyze_frame_locally(frame_base64, candidate_context, candidate_name)
                    feedback[key] = local_analysis.get(key, f"Specific {key.replace('_', ' ')} observation needed for {candidate_name}")
            
            # Store in candidate history for future uniqueness
            if candidate_context:
                if candidate_context not in _candidate_history:
                    _candidate_history[candidate_context] = []
                _candidate_history[candidate_context].append(feedback)
                
                # Keep only last 3 observations per candidate
                if len(_candidate_history[candidate_context]) > 3:
                    _candidate_history[candidate_context] = _candidate_history[candidate_context][-3:]
            
            # Cache the result
            _visual_cache[cache_key] = feedback
            
            # Limit cache size to prevent memory issues
            if len(_visual_cache) > 300:
                oldest_keys = list(_visual_cache.keys())[:50]
                for key in oldest_keys:
                    del _visual_cache[key]
            
            logger.info(f"Generated unique visual feedback for {candidate_name}: {len(str(feedback))} chars")
            return feedback
            
        except json.JSONDecodeError:
            logger.warning(f"OpenAI response not valid JSON for {candidate_name}, using local analysis")
            result = _analyze_frame_locally(frame_base64, candidate_context, candidate_name)
            _visual_cache[cache_key] = result
            return result
            
    except Exception as e:
        logger.error(f"Error in enhanced visual analysis for {candidate_name}: {str(e)}", exc_info=True)
        result = _analyze_frame_locally(frame_base64, candidate_context, candidate_name)
        _visual_cache[cache_key] = result
        return result

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
    
    # Aggregate observations by category
    summary = {}
    for category in ['professional_appearance', 'body_language', 'environment', 'distractions', 'facial_expressions']:
        category_obs = []
        for obs in observations:
            if category in obs and obs[category]:
                category_obs.append(obs[category])
        
        if category_obs:
            # Find most comprehensive observation (longest with candidate name)
            best_obs = max(category_obs, key=lambda x: len(x) if candidate_name in x else len(x) * 0.5)
            summary[category] = best_obs
        else:
            summary[category] = f"No specific {category.replace('_', ' ')} observations recorded for {candidate_name}"
    
    return summary

def get_candidate_observation_count(candidate_info):
    """Get the number of visual observations for a candidate"""
    if not candidate_info:
        return 0
        
    candidate_name = candidate_info.get('name', 'Unknown')
    candidate_id = candidate_info.get('roll_no', candidate_info.get('email', 'unknown'))
    candidate_context = f"{candidate_name}_{candidate_id}"
    
    return len(_candidate_history.get(candidate_context, []))

def clear_candidate_history(candidate_info=None):
    """Clear visual history for a specific candidate or all candidates"""
    if candidate_info:
        candidate_name = candidate_info.get('name', 'Unknown')
        candidate_id = candidate_info.get('roll_no', candidate_info.get('email', 'unknown'))
        candidate_context = f"{candidate_name}_{candidate_id}"
        _candidate_history.pop(candidate_context, None)
        logger.info(f"Cleared visual history for {candidate_name}")
    else:
        _candidate_history.clear()
        logger.info("Cleared visual history for all candidates")

def get_all_candidate_contexts():
    """Get list of all candidate contexts with observations"""
    return list(_candidate_history.keys())

def cleanup_old_observations(max_age_hours=24):
    """Clean up old visual observations to manage memory"""
    # This is a placeholder for future implementation if needed
    # Could track timestamps and remove old entries
    current_count = sum(len(obs) for obs in _candidate_history.values())
    logger.info(f"Current visual observation count: {current_count} across {len(_candidate_history)} candidates")
    
    # If we have too many observations, keep only the most recent per candidate
    if current_count > 1000:
        for context in _candidate_history:
            if len(_candidate_history[context]) > 2:
                _candidate_history[context] = _candidate_history[context][-2:]
        logger.info("Cleaned up old visual observations due to memory limits")