from contextvars import ContextVar

# Explicit job option, isolated between concurrent jobs and restored on exit.
text_only_gemini = ContextVar('text_only_gemini', default=False)
