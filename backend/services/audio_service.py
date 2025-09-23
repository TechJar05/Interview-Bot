import base64
import numpy as np
import webrtcvad
import logging
import wave
import io
import re
import os
from elevenlabs import ElevenLabs
from config import Config

logger = logging.getLogger(__name__)
ElevenLabsAPI = Config.ELEVENLABS_TTS
print("ElevenLabs API Key:", ElevenLabsAPI)

VAD_SAMPLING_RATE = 16000
VAD_FRAME_DURATION = 30  # ms
VAD_MODE = 2

vad = webrtcvad.Vad()
vad.set_mode(VAD_MODE)
# Initialize ElevenLabs client
client = ElevenLabs(api_key=ElevenLabsAPI)


def _sanitize_tts_text(text: str) -> str:
    if not text:
        return ""
    cleaned = str(text).strip().replace('\u00A0', ' ')
    cleaned = re.sub(r'^[\s\*\-\u2022\u2023\u25E6\u2043\u2219]+', '', cleaned)
    cleaned = re.sub(r'\*+([^*]*?)\*+', r'\1', cleaned)
    cleaned = re.sub(r'[\s\u00A0]+\*+[\s\u00A0]+', ' ', cleaned)
    cleaned = re.sub(r'[\s\*\-\u2022\u2023\u25E6\u2043\u2219]+$', '', cleaned)
    cleaned = re.sub(r'[\s\u00A0]+', ' ', cleaned).strip()
    return cleaned



def text_to_speech(
    text,
    lang_code="en",
    voice_id="21m00Tcm4TlvDq8ikWAM",
    model_id="eleven_multilingual_v2",
    output_format="mp3_44100_128"
):
    """
    Convert text to speech using ElevenLabs API.
    Returns base64-encoded WAV audio (16kHz mono).
    """
    try:
        text = _sanitize_tts_text(text)

        # Generator → join chunks into bytes
        audio_generator = client.text_to_speech.convert(
            voice_id="CZdRaSQ51p0onta4eec8",   # replace with your chosen voice_id #EXAVITQu4vr4xnSDxMaL - bella #21m00Tcm4TlvDq8ikWAM - Rachel voice #MF3mGyEYCl7XYWbV9V6O - Ellie
            model_id="eleven_multilingual_v2",  # supports Hindi
            text=text
        )

        print('audio generated')
        audio_bytes = b"".join(audio_generator)
        return base64.b64encode(audio_bytes).decode("utf-8")

    except Exception as e:
        logger.error(f"Error in ElevenLabs text-to-speech: {str(e)}", exc_info=True)
        return None


def process_audio_with_vad(audio_bytes):
    """
    Run VAD on WAV raw bytes.
    """
    try:
        # Read WAV and extract PCM frames
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            pcm_data = wf.readframes(wf.getnframes())
            audio = np.frombuffer(pcm_data, dtype=np.int16)

        frame_size = int(VAD_SAMPLING_RATE * VAD_FRAME_DURATION / 1000)
        frames = [audio[i:i + frame_size] for i in range(0, len(audio), frame_size)]
        speech_frames = 0

        for frame in frames:
            if len(frame) < frame_size:
                frame = np.pad(frame, (0, frame_size - len(frame)), "constant")
            frame_bytes = frame.tobytes()
            if vad.is_speech(frame_bytes, VAD_SAMPLING_RATE):
                speech_frames += 1

        speech_ratio = speech_frames / len(frames) if frames else 0
        has_speech = speech_ratio > 0.5
        return has_speech, speech_ratio

    except Exception as e:
        logger.error(f"Error in VAD processing: {e}", exc_info=True)
        return False, 0


def process_audio_from_base64(audio_data_base64):
    """
    Decode base64 WAV data and run VAD.
    """
    try:
        # Strip possible "data:audio/wav;base64," prefix
        if "," in audio_data_base64:
            audio_data_base64 = audio_data_base64.split(",", 1)[1]

        audio_bytes = base64.b64decode(audio_data_base64)
        return process_audio_with_vad(audio_bytes)

    except Exception as e:
        logger.error(f"Error processing audio from base64: {str(e)}", exc_info=True)
        return False, 0
