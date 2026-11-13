"""Groq clients: 120b for generation, 20b for routing and grading."""
from __future__ import annotations

from langchain_groq import ChatGroq

from app.config import require_groq, settings

require_groq()

generator = ChatGroq(model=settings.GROQ_MODEL_GENERATION, api_key=settings.GROQ_API_KEY, temperature=0,
                     max_retries=2, timeout=60)
grader = ChatGroq(model=settings.GROQ_MODEL_ROUTING, api_key=settings.GROQ_API_KEY, temperature=0,
                  max_retries=2, timeout=30)
