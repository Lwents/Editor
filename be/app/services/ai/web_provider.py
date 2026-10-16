"""Select the configured browser without sharing logins or conversations."""
from app.core import settings


def is_web_engine():
    return settings.translation_engine.lower() in {'gemini_web', 'deepseek_web'}


def get_browser(engine=None):
    if (engine or settings.translation_engine).lower() == 'deepseek_web':
        from .deepseek_web import browser
    else:
        from .gemini_web import browser
    return browser


class ConfiguredBrowser:
    def __getattr__(self, name):
        return getattr(get_browser(), name)


browser = ConfiguredBrowser()
