from contextvars import ContextVar

# Explicit job option, isolated between concurrent jobs and restored on exit.
text_only_gemini = ContextVar('text_only_gemini', default=False)

# Selective image repair can be enabled without redoing the complete image review.
repair_failed_captions = ContextVar('repair_failed_captions', default=False)
