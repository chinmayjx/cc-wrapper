"""Tests run with fixed settings, never this machine's kindle/.env: set before the ink package is imported."""

import os

os.environ["KINDLE_ENV_FILE"] = os.devnull
os.environ.update({
    "KINDLE_AUTH": "access",
    "KINDLE_ACCESS_TEAM": "https://team.example.com",
    "KINDLE_ACCESS_AUD": "AUD",
    "KINDLE_ACCESS_EMAIL": "me@example.com",
    "KINDLE_ORIGIN": "https://kindle.example.com",
    "KINDLE_ARTIFACT_LINK": "https://artifacts.example.com",
    "KINDLE_ARTIFACT_HUB_URL": "https://artifact-hub.example.com",
    "KINDLE_SESSIONS_URL": "",
    "KINDLE_STT_ENGINE": "elevenlabs",
    "KINDLE_STT_ENGINE_FILE": "",
    "KINDLE_WHISPER_URL": "http://whisper.example.com:8793",
    "ELEVENLABS_API_KEY": "",
    "DEEPGRAM_API_KEY": "",
})
