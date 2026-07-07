"""
axel.rem configuration — reads from environment variables or .env file.
Copy .env.example to .env and fill in your values.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# PostgreSQL
DB_HOST = os.getenv("REM_DB_HOST", "localhost")
DB_PORT = int(os.getenv("REM_DB_PORT", "5432"))
DB_NAME = os.getenv("REM_DB_NAME", "rem")
DB_USER = os.getenv("REM_DB_USER", "rem")
DB_PASS = os.getenv("REM_DB_PASS", "")

# Redis
REDIS_HOST = os.getenv("REM_REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REM_REDIS_PORT", "6379"))
REDIS_PASS = os.getenv("REM_REDIS_PASS", "")

# LLM (OpenAI-compatible endpoint — Ollama, LiteLLM, OpenAI, etc.)
LLM_URL   = os.getenv("REM_LLM_URL",   "http://localhost:11434")
LLM_API_KEY = os.getenv("REM_LLM_API_KEY", "")
LLM_MODEL = os.getenv("REM_LLM_MODEL", "llama3.1")

# Dream scheduler
DREAM_HOUR = int(os.getenv("REM_DREAM_HOUR", "3"))  # UTC hour

# Agents tracked by the dream cycle (comma-separated)
AGENTS = [a.strip().upper() for a in os.getenv("REM_AGENTS", "AXEL").split(",") if a.strip()]
