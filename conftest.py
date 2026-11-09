import os

# The tools are built at import time and check for these. Tests make no API calls.
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("SERPER_API_KEY", "test")
