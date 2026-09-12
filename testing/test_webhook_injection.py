"""
Webhook HMAC Security & Injection Test Suite
---------------------------------------------
Tests three scenarios against the Django WhatsApp Webhook endpoint:
1. Missing Signature (Unsigned Request) -> Expected: 403 Forbidden
2. Invalid Signature (Tampered Request) -> Expected: 403 Forbidden
3. Valid HMAC-SHA256 Signature          -> Expected: 200 OK

Usage:
    python testing/test_webhook_injection.py
"""

import json
import time
import hmac
import hashlib
import urllib.request
import urllib.error

# Target local webhook endpoint
WEBHOOK_URL = "http://127.0.0.1:8000/webhook/"

# Secret used to compute valid HMAC (must match META_APP_SECRET in .env)
TEST_APP_SECRET = "test_meta_app_secret_12345"


def build_fake_meta_payload(sender_number: str, message_text: str, sender_name: str = "Security Tester"):
    """
    Constructs a valid Meta WhatsApp Cloud API incoming message payload.
    """
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550254321",
                                "phone_number_id": "123456789012345"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": sender_name
                                    },
                                    "wa_id": sender_number
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender_number,
                                    "id": f"wamid.TEST_{int(time.time())}",
                                    "timestamp": str(int(time.time())),
                                    "text": {
                                        "body": message_text
                                    },
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }


def compute_hmac_signature(body_bytes: bytes, secret: str) -> str:
    """
    Computes Meta-compliant HMAC-SHA256 signature string: 'sha256=<hex_digest>'
    """
    signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=body_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()
    return f"sha256={signature}"


def send_test_request(test_name: str, payload_dict: dict, headers: dict):
    print("\n" + "=" * 65)
    print(f"TEST: {test_name}")
    print("=" * 65)

    json_bytes = json.dumps(payload_dict).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json_bytes,
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status_code = response.getcode()
            response_body = response.read().decode("utf-8")
            print(f"Status Code : {status_code}")
            print(f"Response    : {response_body}")
            return status_code

    except urllib.error.HTTPError as e:
        status_code = e.code
        error_body = e.read().decode("utf-8")
        print(f"Status Code : {status_code} ({e.reason})")
        print(f"Response    : {error_body}")
        return status_code

    except urllib.error.URLError as e:
        print(f"[-] Connection failed: {e.reason}")
        print("[-] Ensure Django is running: `python manage.py runserver`")
        return None


def run_all_tests():
    payload = build_fake_meta_payload(
        sender_number="919876543210",
        message_text="Hello, this is a security validation test."
    )
    raw_bytes = json.dumps(payload).encode("utf-8")

    # 1. Test Unsigned Request
    headers_unsigned = {
        "Content-Type": "application/json"
    }
    status_1 = send_test_request(
        "1. Unsigned Request (Missing X-Hub-Signature-256)",
        payload,
        headers_unsigned
    )
    if status_1 == 403:
        print("[PASS] Unsigned request correctly blocked with 403 Forbidden!")
    else:
        print(f"[FAIL] Expected 403, got {status_1}")

    # 2. Test Invalid/Tampered Signature
    headers_invalid = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000"
    }
    status_2 = send_test_request(
        "2. Invalid / Tampered Signature",
        payload,
        headers_invalid
    )
    if status_2 == 403:
        print("[PASS] Tampered signature request correctly blocked with 403 Forbidden!")
    else:
        print(f"[FAIL] Expected 403, got {status_2}")

    # 3. Test Valid HMAC-SHA256 Signature
    valid_signature = compute_hmac_signature(raw_bytes, TEST_APP_SECRET)
    headers_valid = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": valid_signature
    }
    status_3 = send_test_request(
        "3. Valid HMAC-SHA256 Signature",
        payload,
        headers_valid
    )
    if status_3 == 200:
        print("[PASS] Validly signed request accepted with 200 OK!")
    else:
        print(f"[FAIL] Expected 200, got {status_3}")


if __name__ == "__main__":
    run_all_tests()
