"""Ensures the test suite can run without a real .env file present.
Settings requires these fields to construct at all, but the tests that
import services.llm (test_llm_parsing.py) never make a real API call —
they only need *some* value to satisfy Settings, not a working key."""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-key")
os.environ.setdefault("API_KEY", "test-secret")