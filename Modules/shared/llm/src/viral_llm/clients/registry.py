from .anthropic_claude import AnthropicClaudeClient
from .assemblyai import AssemblyAIClient
from .base import TranscriptionClient, VisionClient
from .deepgram import DeepgramClient
from .google_gemini import GoogleGeminiClient
from .groq_whisper import GroqWhisperClient
from .openai_gpt4o import OpenAIGPT4oClient, OpenAIGPT4oMiniClient
from .openai_whisper import OpenAIWhisperClient

TRANSCRIPTION_CLIENTS: dict[str, TranscriptionClient] = {
    "assemblyai": AssemblyAIClient(),
    "deepgram": DeepgramClient(),
    "openai_whisper": OpenAIWhisperClient(),
    "groq_whisper": GroqWhisperClient(),
}

VISION_CLIENTS: dict[str, VisionClient] = {
    "anthropic_claude": AnthropicClaudeClient(),
    "openai_gpt4o": OpenAIGPT4oClient(),
    "openai_gpt4o_mini": OpenAIGPT4oMiniClient(),
    "google_gemini_pro": GoogleGeminiClient(model="gemini-2.5-pro"),
    "google_gemini_flash": GoogleGeminiClient(model="gemini-2.5-flash"),
}


def get_transcription_client(provider: str) -> TranscriptionClient:
    if provider not in TRANSCRIPTION_CLIENTS:
        raise ValueError(f"unknown transcription provider: {provider}")
    return TRANSCRIPTION_CLIENTS[provider]


def get_vision_client(provider: str) -> VisionClient:
    if provider not in VISION_CLIENTS:
        raise ValueError(f"unknown vision provider: {provider}")
    return VISION_CLIENTS[provider]
