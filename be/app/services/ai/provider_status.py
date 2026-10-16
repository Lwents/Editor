"""User-facing names for the provider selected in AI settings."""
from app.core import settings


def provider_name():
    return {'deepseek_web': 'DeepSeek', 'gemini_web': 'Gemini', 'gemini': settings.ai_model}.get(settings.translation_engine.lower(), 'AI')


def error_prefix():
    return {'deepseek_web': 'DEEPSEEK', 'gemini_web': 'GEMINI'}.get(settings.translation_engine.lower(), 'AI')
