import os
import json
import time
import requests
import logging
from typing import List, Dict, Optional, Tuple
from django.conf import settings

logger = logging.getLogger(__name__)

# Fallback refusal message when context is missing or question is out of scope
NO_INFO_FALLBACK_MESSAGE = (
    "I don't have information on that topic in our knowledge base. "
    "Please contact HR directly."
)

SYSTEM_INSTRUCTION = (
    "You are a company knowledge assistant.\n"
    "Answer ONLY using the provided context below.\n"
    "If the answer is not found in the context, say exactly: "
    "I don't have information on that topic in our knowledge base. "
    "Please contact HR directly.\n"
    "Never make up information.\n"
    "Be concise and clear.\n"
    "Format for WhatsApp — short paragraphs, no markdown."
)


def generate_answer(
    message_text: str,
    context: str = "",
    history: Optional[List[Dict[str, str]]] = None
) -> str:
    """
    Generates an answer using Google Gemini REST API constrained strictly by RAG context
    and multi-turn conversation history.

    :param message_text: Current user question / message
    :param context: Retrieved context string from ChromaDB / RAG pipeline
    :param history: List of previous conversation turns [{"role": "user"|"assistant", "content": "..."}]
    :return: AI response string formatted for WhatsApp
    """
    # 1. Strict Guard: If context is empty, skip Gemini API call completely to save costs and avoid hallucination
    if not context or not context.strip():
        print("[AI SERVICE] Context is empty. Skipping Gemini API call and returning standard fallback.", flush=True)
        return NO_INFO_FALLBACK_MESSAGE

    api_key = getattr(settings, 'GEMINI_API_KEY', os.getenv('GEMINI_API_KEY', ''))
    if not api_key:
        logger.error("GEMINI_API_KEY is missing in settings / environment variables.")
        print("[ERROR] GEMINI_API_KEY is missing in settings/.env", flush=True)
        return "Sorry, AI service is not configured yet (missing GEMINI_API_KEY)."

    model_name = getattr(settings, 'GEMINI_MODEL', os.getenv('GEMINI_MODEL', 'gemini-2.0-flash'))
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    # 2. Build structured prompt components
    prompt_sections = []

    # Section A: Context (from ChromaDB)
    prompt_sections.append(f"COMPANY KNOWLEDGE:\n{context.strip()}")

    # Section B: Conversation History (if present)
    if history:
        history_lines = ["PREVIOUS CONVERSATION:"]
        for turn in history:
            role = turn.get("role", "user")
            label = "User" if role == "user" else "Assistant"
            content = turn.get("content", "").strip()
            if content:
                history_lines.append(f"{label}: {content}")
        
        if len(history_lines) > 1:
            prompt_sections.append("\n".join(history_lines))

    # Section C: Current Question
    prompt_sections.append(f"QUESTION: {message_text.strip()}")

    # Combine into final user content payload
    user_content_text = "\n\n".join(prompt_sections)

    payload = {
        "contents": [
            {
                "parts": [{"text": user_content_text}]
            }
        ],
        "systemInstruction": {
            "parts": [
                {
                    "text": SYSTEM_INSTRUCTION
                }
            ]
        },
        "generationConfig": {
            "temperature": 0.1,  # Low temperature for strict factual grounding
            "maxOutputTokens": 600
        }
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        print(f"\n[GEMINI AI] Calling API for: '{message_text}' (History turns: {len(history or [])})", flush=True)
        response = requests.post(url, json=payload, headers=headers, timeout=20)

        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    ai_reply = parts[0].get("text", "").strip()

                    try:
                        print(f"[GEMINI AI ANSWER]: {ai_reply}\n", flush=True)
                    except UnicodeEncodeError:
                        safe_text = ai_reply.encode('ascii', 'replace').decode('ascii')
                        print(f"[GEMINI AI ANSWER]: {safe_text}\n", flush=True)

                    return ai_reply

            return "Sorry, I could not generate a response for that."
        else:
            logger.error("Gemini API error status %s: %s", response.status_code, response.text)
            print(f"[GEMINI API ERROR] Status {response.status_code}: {response.text}\n", flush=True)
            return "I apologize, but I encountered an error while processing your request. Please try again in a moment."

    except requests.exceptions.Timeout:
        logger.error("Gemini API request timed out after 20 seconds.")
        print("[GEMINI TIMEOUT ERROR]: Request timed out.\n", flush=True)
        return "I apologize, but the request timed out. Please try asking again."

    except requests.exceptions.RequestException as e:
        logger.error("Gemini API network error: %s", e)
        print(f"[GEMINI NETWORK ERROR]: {e}\n", flush=True)
        return "I apologize, but I am having trouble connecting to AI services right now."


def ask_gemini(
    prompt: str,
    context: str = "",
    chat_history: Optional[List[Dict[str, str]]] = None
) -> Tuple[str, int]:
    """
    Backwards compatibility wrapper that returns (reply, latency_ms).
    """
    start_time = time.time()
    reply = generate_answer(
        message_text=prompt,
        context=context,
        history=chat_history or []
    )
    latency_ms = int((time.time() - start_time) * 1000)
    return reply, latency_ms
