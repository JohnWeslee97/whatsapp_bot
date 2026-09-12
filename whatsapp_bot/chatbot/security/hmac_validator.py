import hmac
import hashlib
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

def get_client_ip(request) -> str:
    """
    Extracts client IP address from request headers, accounting for reverse proxies.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # If behind a reverse proxy/load balancer, take the first client IP
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'Unknown IP')
    return ip


def validate_whatsapp_signature(request) -> bool:
    """
    Validates the X-Hub-Signature-256 header sent by Meta WhatsApp Cloud API.
    
    :param request: Django HttpRequest object
    :return: True if signature matches, False otherwise
    """
    # 1. Fetch secret key from settings or environment
    app_secret = getattr(settings, 'META_APP_SECRET', os.getenv('META_APP_SECRET', ''))
    
    if not app_secret:
        logger.error("[SECURITY] META_APP_SECRET is not configured in settings or .env!")
        return False

    # 2. Retrieve X-Hub-Signature-256 header from request
    signature_header = (
        request.headers.get('X-Hub-Signature-256') 
        or request.META.get('HTTP_X_HUB_SIGNATURE_256')
    )

    if not signature_header:
        logger.warning("[SECURITY] Missing 'X-Hub-Signature-256' header in incoming request.")
        return False

    # 3. Header format is "sha256=<hash>"
    if not signature_header.startswith('sha256='):
        logger.warning(f"[SECURITY] Invalid signature format: '{signature_header}'")
        return False

    received_hash = signature_header.split('sha256=', 1)[1].strip()

    # 4. Get raw body bytes (must be exact original bytes received from wire)
    raw_body = request.body

    # 5. Compute HMAC-SHA256 hash using META_APP_SECRET
    expected_hash = hmac.new(
        key=app_secret.encode('utf-8'),
        msg=raw_body,
        digestmod=hashlib.sha256
    ).hexdigest()

    # 6. Constant-time comparison to prevent timing side-channel attacks
    is_valid = hmac.compare_digest(expected_hash, received_hash)

    if not is_valid:
        logger.warning(
            f"[SECURITY] HMAC mismatch! Expected: {expected_hash[:10]}..., Received: {received_hash[:10]}..."
        )

    return is_valid
