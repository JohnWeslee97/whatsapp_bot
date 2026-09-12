import re
import unicodedata
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Maximum allowed input length for WhatsApp messages (prevents DOS / buffer bloat)
MAX_MESSAGE_LENGTH = 2000

# Risk Thresholds
LOW_RISK_MAX = 2
MEDIUM_RISK_MAX = 5
HIGH_RISK_MIN = 6

# -----------------------------------------------------------------------------
# REGEX RULESETS BY CATEGORY (Boundary & Context Aware)
# -----------------------------------------------------------------------------
# Using word boundaries and action-verb associations to avoid false positives
# on benign inquiries like "what are the instructions for filing a grievance".

CATEGORY_RULES = {
    "instruction_override": {
        "weight": 3,
        "max_category_score": 6,
        "patterns": [
            r"\b(?:ignore|disregard|forget|override|bypass|drop|delete|reset)\s+(?:all\s+|any\s+|prior\s+|previous\s+|initial\s+|system\s+)?(?:instructions?|prompts?|rules?|guidelines?|commands?|directives?)\b",
            r"\b(?:new|updated|follow\s+my|obey\s+my)\s+(?:instructions?|prompt|system\s+prompt|rules?)\s*[:=]",
            r"\bstart\s+(?:a\s+)?new\s+conversation\s+and\s+forget\b",
            r"\bdo\s+not\s+follow\s+(?:your\s+)?(?:rules?|guidelines?|instructions?)\b",
        ],
    },
    "role_manipulation": {
        "weight": 2,
        "max_category_score": 4,
        "patterns": [
            r"\b(?:act|behave|operate)\s+as\s+(?:a|an)?\s*(?:unrestricted|dan|jailbreak|evil|unfiltered|god|admin|administrator|root|system|hacker)\b",
            r"\b(?:pretend|assume|imagine)\s+(?:you\s+are|you're)\s+(?:a|an)?\s*(?:unrestricted|different|evil|new|unfiltered|without\s+rules?|god\s+mode|admin)\b",
            r"\byou\s+are\s+now\s+(?:a|an)?\s*(?:unrestricted|dan|jailbreak|free|unfiltered|admin|administrator|root|different)\b",
            r"\bfrom\s+now\s+on\s*,?\s*you\s+(?:are|must|will)\b",
        ],
    },
    "system_prompt_extraction": {
        "weight": 3,
        "max_category_score": 6,
        "patterns": [
            r"\b(?:show|reveal|display|output|print|give|repeat|tell\s+me|expose|leak)\s+(?:your\s+|the\s+)?(?:hidden\s+|internal\s+|initial\s+|underlying\s+|original\s+|exact\s+)?(?:system\s+prompt|instructions?|developer\s+prompt|meta\s+prompt|system\s+message)\b",
            r"\bwhat\s+(?:are\s+your|is\s+your)\s+(?:hidden|system|internal|original|secret)\s+(?:prompt|instructions?|guidelines?)\b",
            r"\b(?:hidden\s+system\s+instructions|system\s+prompt|developer\s+instructions|secret\s+instructions)\b",
            r"\b(?:repeat|echo)\s+the\s+words\s+above\b",
            r"\bwhat\s+did\s+the\s+developer\s+tell\s+you\b",
        ],
    },
    "jailbreak": {
        "weight": 4,
        "max_category_score": 8,
        "patterns": [
            r"\b(?:jailbreak|jailbroken)\b",
            r"\b(?:developer|dev|god|unrestricted|unfiltered|debug)\s+mode\b",
            r"\b(?:bypass|disable|turn\s+off|remove)\s+(?:all\s+|your\s+|any\s+|the\s+)?(?:safety|guardrails?|content\s+filters?|moderation|ethics|guidelines?|restrictions?)\b",
            r"\b(?:unrestricted|unfiltered)\s+(?:mode|administrator|assistant|bot|ai)\b",
            r"\bdo\s+anything\s+now\b",
            r"\bDAN\s+(?:mode|prompt)?\b",
        ],
    },
    "sensitive_data_extraction": {
        "weight": 3,
        "max_category_score": 6,
        "patterns": [
            r"\b(?:database|db|postgres|mysql|sqlite|redis)\s+(?:password|user|credentials?|connection\s+string|uri)\b",
            r"\b(?:api\s*key|secret\s*key|token|auth\s*token|jwt|access\s*token|private\s*key|credentials?)\b",
            r"\b(?:internal\s+database|private\s+records?|confidential\s+(?:data|files?|info|records?))\b",
            r"\b(?:show|dump|reveal|give\s+me)\s+(?:all\s+)?(?:passwords?|secrets?|credentials?|env\s+vars?|environment\s+variables?)\b",
        ],
    },
    "tool_manipulation": {
        "weight": 3,
        "max_category_score": 6,
        "patterns": [
            r"\b(?:run|execute|perform)\s+(?:raw\s+)?(?:sql|command|bash|shell|powershell|script|eval|query)\b",
            r"\b(?:access|dump|drop|delete|truncate|select\s+\*\s+from|show\s+me\s+all)\s+(?:the\s+)?(?:database|table|users?|system\s+files?)\b",
            r"\b(?:give|grant)\s+me\s+(?:admin|superuser|root|elevated)\s+(?:access|privileges?|rights?)\b",
            r"\b(?:call|invoke|execute)\s+(?:internal\s+|hidden\s+)?tool\s*[:(]\b",
        ],
    },
}

# -----------------------------------------------------------------------------
# OBFUSCATION DETECTORS (Lightweight Evasion Signals)
# -----------------------------------------------------------------------------

def detect_obfuscation(raw_text: str) -> bool:
    """
    Detects structural evasion anomalies:
    1. Long Base64-like suspicious strings (>40 chars of alphanumeric with padding).
    2. Character-spaced obfuscation (e.g., 'i g n o r e').
    3. Punctuation-delimited obfuscation (e.g., 'i.g.n.o.r.e').
    """
    if not raw_text:
        return False

    # 1. Base64 payload detector (long unbroken base64 sequences without spaces)
    if re.search(r"[A-Za-z0-9+/]{50,}={0,2}", raw_text):
        return True

    # 2. Character-spaced obfuscation (e.g., "i g n o r e   p r o m p t")
    spaced_words = re.findall(r"\b(?:[a-zA-Z]\s+){5,}[a-zA-Z]\b", raw_text)
    if spaced_words:
        for word in spaced_words:
            collapsed = re.sub(r"\s+", "", word).lower()
            if any(k in collapsed for k in ["ignore", "system", "prompt", "bypass", "jailbreak"]):
                return True

    # 3. Excessive repeated punctuation / delimiters (e.g. i-g-n-o-r-e or i.g.n.o.r.e)
    delimited = re.findall(r"(?:[a-zA-Z][\.\-_/\\*]){4,}[a-zA-Z]", raw_text)
    if delimited:
        for word in delimited:
            collapsed = re.sub(r"[\.\-_/\\*]", "", word).lower()
            if any(k in collapsed for k in ["ignore", "system", "prompt", "bypass", "jailbreak"]):
                return True

    return False


# -----------------------------------------------------------------------------
# NORMALIZATION
# -----------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    """
    Normalizes user message for security scanning:
    - Type-safe string coercion
    - Truncates to MAX_MESSAGE_LENGTH
    - NFKD Unicode normalization (resolves homoglyphs & special formatting)
    - Lowercases
    - Collapses excessive whitespace
    """
    if not isinstance(text, str) or not text.strip():
        return ""

    truncated = text[:MAX_MESSAGE_LENGTH]
    normalized = unicodedata.normalize("NFKD", truncated)
    encoded = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = encoded.lower()
    cleaned = re.sub(r"\s+", " ", lowered).strip()
    return cleaned


# -----------------------------------------------------------------------------
# MAIN RISK SCORING ANALYSIS
# -----------------------------------------------------------------------------
def analyze_injection(text: str) -> Dict[str, Any]:
    """
    Analyzes input text using a multi-signal weighted risk-scoring system.

    Returns:
        {
            "is_injection": bool,       # True only if risk_level is 'high' (score >= 6)
            "risk_score": int,          # Aggregated score across categories
            "risk_level": str,          # "low" (0-2), "medium" (3-5), "high" (6+)
            "reasons": List[str]        # List of matched categories
        }
    """
    if not text or not isinstance(text, str):
        return {
            "is_injection": False,
            "risk_score": 0,
            "risk_level": "low",
            "reasons": [],
        }

    normalized = normalize_text(text)
    if not normalized:
        return {
            "is_injection": False,
            "risk_score": 0,
            "risk_level": "low",
            "reasons": [],
        }

    total_score = 0
    matched_categories: List[str] = []

    # 1. Evaluate Rule Categories
    # Each distinct pattern matched inside a category adds weight, capped at max_category_score.
    # Repetitive occurrences of the EXACT SAME pattern do NOT inflate the score.
    for category_name, category_data in CATEGORY_RULES.items():
        weight = category_data["weight"]
        max_score = category_data.get("max_category_score", weight * 2)
        patterns = category_data["patterns"]

        category_score = 0
        matched_any = False

        for pattern in patterns:
            if re.search(pattern, normalized, re.IGNORECASE):
                category_score += weight
                matched_any = True
                if category_score >= max_score:
                    category_score = max_score
                    break

        if matched_any:
            total_score += category_score
            matched_categories.append(category_name)

    # 2. Evaluate Obfuscation Anomaly
    if detect_obfuscation(text):
        total_score += 2
        matched_categories.append("obfuscation_evasion")

    # 3. Determine Risk Level
    if total_score >= HIGH_RISK_MIN:
        risk_level = "high"
        is_injection = True
    elif total_score > LOW_RISK_MAX:
        risk_level = "medium"
        is_injection = False
    else:
        risk_level = "low"
        is_injection = False

    return {
        "is_injection": is_injection,
        "risk_score": total_score,
        "risk_level": risk_level,
        "reasons": matched_categories,
    }


def detect_injection(text: str) -> bool:
    """
    Convenience boolean function.
    Returns True ONLY if message is evaluated as HIGH risk (score >= 6).
    """
    analysis = analyze_injection(text)
    return analysis["is_injection"]
