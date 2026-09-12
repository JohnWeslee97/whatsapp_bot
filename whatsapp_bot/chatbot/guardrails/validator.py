import os
import logging
import requests
from typing import Dict, Any, Optional
from django.conf import settings

logger = logging.getLogger(__name__)


def validate_answer(
    question: str,
    context: str,
    answer: Optional[str]
) -> Dict[str, Any]:
    """
    Validates a generated AI answer before sending to the user.
    Applies empty checks, minimum word length constraints, and LLM relevance evaluation.

    :param question: The original user question
    :param context: The RAG context retrieved for the question
    :param answer: The candidate AI answer
    :return: dict with keys 'passed' (bool) and 'reason' (str)
    """
    # Check 1: Empty check
    if answer is None or not str(answer).strip():
        logger.warning("[GUARDRAIL] Answer failed empty check.")
        return {"passed": False, "reason": "empty"}

    cleaned_answer = str(answer).strip()

    # Check 2: Length check (minimum 5 words)
    words = cleaned_answer.split()
    if len(words) < 5:
        logger.warning("[GUARDRAIL] Answer failed length check (%d words): '%s'", len(words), cleaned_answer)
        return {"passed": False, "reason": "too_short"}

    # Check 2b: Skip LLM validation for known system / fallback / error responses
    known_system_prefixes = (
        "I don't have information on that topic",
        "I apologize, but I encountered an error",
        "I apologize, but the request timed out",
        "Sorry, AI service is not configured",
        "Sorry, something went wrong",
        "I was not able to find a reliable answer",
        "I can only read text messages",
        "You are sending too many messages",
        "I am not able to process that message"
    )
    if any(cleaned_answer.startswith(prefix) for prefix in known_system_prefixes):
        return {"passed": True, "reason": "system_message"}

    # Check 3: Relevance check using Gemini
    api_key = getattr(settings, 'GEMINI_API_KEY', os.getenv('GEMINI_API_KEY', ''))
    if not api_key:
        logger.warning("[GUARDRAIL] GEMINI_API_KEY missing for relevance check. Defaulting to ok.")
        return {"passed": True, "reason": "ok"}

    model_name = getattr(settings, 'GEMINI_MODEL', os.getenv('GEMINI_MODEL', 'gemini-2.0-flash'))
    relevance_prompt = (
        "Answer with only YES or NO.\n"
        "Does this answer actually respond to the question?\n"
        f"Question: {question.strip()}\n"
        f"Answer: {cleaned_answer}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [{"text": relevance_prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 10
        }
    }
    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    eval_text = parts[0].get("text", "").strip().upper()
                    
                    # If response is "NO" or does not contain "YES"
                    if "YES" not in eval_text or "NO" in eval_text.split():
                        logger.warning(
                            "[GUARDRAIL] Answer failed relevance check (LLM output: '%s') for question: '%s'",
                            eval_text,
                            question
                        )
                        return {"passed": False, "reason": "irrelevant"}

            # If LLM returned yes
            return {"passed": True, "reason": "ok"}
        else:
            logger.error("[GUARDRAIL] Relevance check API returned status %s: %s", response.status_code, response.text)
            # If the validator API call itself fails, treat gracefully
            return {"passed": True, "reason": "ok"}

    except Exception as e:
        logger.error("[GUARDRAIL] Relevance check error: %s", e)
        return {"passed": True, "reason": "ok"}
