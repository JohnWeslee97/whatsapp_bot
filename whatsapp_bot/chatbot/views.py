import os
import json
import time
import logging
import threading
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db import close_old_connections

from chatbot.whatsapp_service import send_whatsapp_message, mark_message_as_read
from chatbot.ai_service import generate_answer, ask_gemini
from chatbot.security.hmac_validator import validate_whatsapp_signature, get_client_ip
from chatbot.security.rate_limiter import is_rate_limited
from chatbot.security.injection_guard import analyze_injection
from chatbot.rag.retriever import retrieve_context
from chatbot.guardrails.validator import validate_answer
from chatbot.models import RequestLog
from chatbot.memory.manager import (
    get_or_create_user,
    get_or_create_conversation,
    save_message,
    get_conversation_history
)

logger = logging.getLogger(__name__)

UNSUPPORTED_MEDIA_REPLY = (
    "I can only read text messages. "
    "Please type your question and I will be happy to help!"
)

FALLBACK_ERROR_MESSAGE = (
    "Sorry, something went wrong. "
    "Please try again in a moment."
)


def _safe_print(text: str) -> None:
    """Safely prints to stdout without raising UnicodeEncodeError on Windows cp1252."""
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, Exception):
        safe_text = str(text).encode('ascii', errors='replace').decode('ascii')
        print(safe_text, flush=True)


def process_message(
    phone_number: str,
    message_text: str,
    contact_name: str = "",
    msg_id: str = None
) -> None:
    """
    Background worker thread function for slow RAG + Gemini generation pipeline.
    Executes asynchronously with full stage logging and RequestLog persistence.

    :param phone_number: Recipient / sender WhatsApp phone number
    :param message_text: Incoming user message text
    :param contact_name: Profile name from WhatsApp payload
    :param msg_id: Meta WhatsApp message ID
    """
    # Ensure fresh DB connection for this worker thread
    close_old_connections()
    start_total_time = time.time()

    _safe_print(f"\n[WEBHOOK]    Message from {phone_number}")
    _safe_print(f"[SECURITY]   HMAC valid [OK]")
    _safe_print(f"[SECURITY]   Rate limit OK [OK]")
    _safe_print(f"[SECURITY]   Injection check OK [OK]")

    try:
        # 0. Send Read Receipt (Blue Double Tick) & show "Typing..." indicator immediately
        if msg_id:
            mark_message_as_read(message_id=msg_id, show_typing=True)

        # 1. Look up or create Customer record & active Conversation
        user = get_or_create_user(phone_number=phone_number, name=contact_name)
        conversation = get_or_create_conversation(user)

        # 2. Persist valid incoming user message
        save_message(
            conversation=conversation,
            role='user',
            content=message_text,
            guardrail_passed=True,
            whatsapp_id=msg_id
        )

        # 3. Fetch multi-turn conversation memory (last 6 turns)
        chat_history = get_conversation_history(conversation, last_n=6)
        _safe_print(f"[MEMORY]     Loaded {len(chat_history)} messages")

        # 4. Hybrid RAG Pipeline Context Retrieval
        context = retrieve_context(message_text)
        _safe_print(f"[RETRIEVAL]  Context found: {bool(context)}")

        # 5. Generate AI response with Gemini
        _safe_print(f"[GEMINI]     Generating answer...")
        ai_answer = generate_answer(
            message_text=message_text,
            context=context,
            history=chat_history
        )

        # 6. Output Guardrail Validation & Retry
        val_result = validate_answer(
            question=message_text,
            context=context,
            answer=ai_answer
        )
        _safe_print(f"[GUARDRAIL]  Result: {val_result['reason']}")
        guardrail_passed = val_result.get("passed", False)

        # Retry once if initial answer fails validation
        if not guardrail_passed:
            logger.warning(
                "[GUARDRAILS] Answer validation failed (reason: %s). Retrying generation for question: '%s'",
                val_result.get("reason"),
                message_text
            )
            retry_answer = generate_answer(
                message_text=message_text,
                context=context,
                history=chat_history
            )
            retry_val = validate_answer(
                question=message_text,
                context=context,
                answer=retry_answer
            )
            if retry_val.get("passed", False):
                ai_answer = retry_answer
                guardrail_passed = True
                _safe_print(f"[GUARDRAIL]  Retry Result: {retry_val['reason']} [OK]")
            else:
                logger.error(
                    "[GUARDRAILS] Retry also failed validation (reason: %s). Falling back to safe response.",
                    retry_val.get("reason")
                )
                ai_answer = (
                    "I was not able to find a reliable answer to your question. "
                    "Please contact HR directly for assistance."
                )
                guardrail_passed = False
                _safe_print(f"[GUARDRAIL]  Retry Result: {retry_val['reason']} [FAILED] (Using fallback)")

        latency_ms = int((time.time() - start_total_time) * 1000)

        # 7. Persist outgoing assistant message
        save_message(
            conversation=conversation,
            role='assistant',
            content=ai_answer,
            latency_ms=latency_ms,
            guardrail_passed=guardrail_passed
        )

        # 8. Send response to user on WhatsApp
        send_whatsapp_message(to_number=phone_number, message_text=ai_answer)
        _safe_print(f"[WHATSAPP]   Reply sent to {phone_number}")
        _safe_print(f"[DONE]       Total latency: {latency_ms}ms\n")

        # 9. Persist RequestLog entry
        status_string = "success" if guardrail_passed else "fallback"
        RequestLog.objects.create(
            phone_number=phone_number,
            message_text=message_text,
            response_text=ai_answer,
            status=status_string,
            latency_ms=latency_ms
        )

    except Exception as e:
        latency_ms = int((time.time() - start_total_time) * 1000)
        logger.exception("Error in background message processing for %s: %s", phone_number, e)
        print(f"\n[BACKGROUND ERROR] Exception processing message for {phone_number}: {e}\n", flush=True)
        try:
            send_whatsapp_message(to_number=phone_number, message_text=FALLBACK_ERROR_MESSAGE)
        except Exception as send_err:
            logger.error("Failed to send fallback error message to %s: %s", phone_number, send_err)

        # Persist failed RequestLog
        try:
            RequestLog.objects.create(
                phone_number=phone_number,
                message_text=message_text,
                response_text=FALLBACK_ERROR_MESSAGE,
                status="failed",
                latency_ms=latency_ms
            )
        except Exception as log_err:
            logger.error("Failed to save failed RequestLog: %s", log_err)
    finally:
        close_old_connections()


@csrf_exempt
def webhook(request):
    """
    Handles Meta's Webhook verification (GET) and incoming message notifications (POST).
    Validates HMAC, rate limits, and injection guards synchronously before dispatching
    heavy RAG & AI generation to a background daemon thread.
    """
    # ---------------------------------------------------------
    # 1. GET: Verification Handshake with Meta
    # ---------------------------------------------------------
    if request.method == 'GET':
        mode = request.GET.get('hub.mode') or request.GET.get('hub_mode')
        token = request.GET.get('hub.verify_token') or request.GET.get('hub_verify_token')
        challenge = request.GET.get('hub.challenge') or request.GET.get('hub_challenge')

        # Read secret VERIFY_TOKEN from Django settings or environment
        verify_token = getattr(settings, 'VERIFY_TOKEN', os.getenv('VERIFY_TOKEN', ''))

        if mode == 'subscribe' and token == verify_token:
            print(f"\n[SUCCESS] Meta Webhook verified successfully! (challenge: {challenge})\n", flush=True)
            return HttpResponse(challenge, status=200)
        else:
            print(f"\n[ERROR] Webhook verification failed. Token received: '{token}', Expected: '{verify_token}'\n", flush=True)
            return HttpResponse('Forbidden', status=403)

    # ---------------------------------------------------------
    # 2. POST: Incoming Webhook Event from Meta
    # ---------------------------------------------------------
    elif request.method == 'POST':
        # Step 1: Validate Meta HMAC-SHA256 Signature (Synchronous Fast Check)
        if not validate_whatsapp_signature(request):
            client_ip = get_client_ip(request)
            error_msg = f"[SECURITY] Invalid signature attempt from {client_ip}"
            print(f"\n{error_msg}\n", flush=True)
            logger.warning(error_msg)

            # Record blocked HMAC attempt
            RequestLog.objects.create(
                phone_number=f"IP:{client_ip}",
                message_text="[Invalid HMAC Signature Request]",
                response_text="Forbidden: Invalid signature",
                status="blocked_hmac",
                latency_ms=0
            )
            return HttpResponse("Forbidden: Invalid signature", status=403)

        # Step 2: Extract all payload data before spawning threads
        try:
            raw_body = request.body.decode('utf-8')
            data = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError as e:
            print(f"\n[ERROR] Malformed JSON payload received: {e}", flush=True)
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            print(f"\n[ERROR] Could not decode request body: {e}", flush=True)
            return JsonResponse({"error": "Failed to read request body"}, status=400)

        extracted_info = []
        incoming_messages = []

        try:
            entries = data.get('entry', []) if isinstance(data, dict) else []
            for entry in entries:
                changes = entry.get('changes', []) if isinstance(entry, dict) else []
                for change in changes:
                    value = change.get('value', {}) if isinstance(change, dict) else {}

                    # Extract contacts map (wa_id -> profile name)
                    contacts = value.get('contacts', []) if isinstance(value, dict) else []
                    contacts_map = {}
                    if isinstance(contacts, list):
                        for contact in contacts:
                            if isinstance(contact, dict):
                                wa_id = contact.get('wa_id')
                                name = contact.get('profile', {}).get('name', 'Unknown')
                                if wa_id:
                                    contacts_map[wa_id] = name

                    # Extract incoming messages
                    messages = value.get('messages', []) if isinstance(value, dict) else []
                    if isinstance(messages, list):
                        for msg in messages:
                            if isinstance(msg, dict):
                                sender = msg.get('from', 'Unknown')
                                contact_name = contacts_map.get(sender)
                                if not contact_name and contacts and isinstance(contacts[0], dict):
                                    contact_name = contacts[0].get('profile', {}).get('name', 'Unknown')
                                if not contact_name:
                                    contact_name = 'Unknown'

                                msg_type = msg.get('type')  # None if missing
                                msg_id = msg.get('id', 'Unknown')
                                timestamp = msg.get('timestamp', 'Unknown')

                                msg_text = None
                                if msg_type == 'text':
                                    msg_text = msg.get('text', {}).get('body', '')

                                incoming_messages.append({
                                    'sender': sender,
                                    'name': contact_name,
                                    'type': msg_type,
                                    'text': msg_text,
                                    'msg_id': msg_id,
                                    'raw_msg': msg
                                })

                                extracted_info.append({
                                    'sender': sender,
                                    'name': contact_name,
                                    'type': msg_type or 'missing_type',
                                    'message': msg_text,
                                    'id': msg_id,
                                    'timestamp': timestamp,
                                })

                    # Extract message status updates (e.g. sent, delivered, read)
                    statuses = value.get('statuses', []) if isinstance(value, dict) else []
                    if isinstance(statuses, list):
                        for status in statuses:
                            if isinstance(status, dict):
                                recipient = status.get('recipient_id', 'Unknown')
                                current_status = status.get('status', 'Unknown')
                                timestamp = status.get('timestamp', 'Unknown')
                                extracted_info.append({
                                    'status_update': True,
                                    'recipient': recipient,
                                    'status': current_status,
                                    'timestamp': timestamp,
                                })
        except Exception as e:
            print(f"\n[WARNING] Error extracting payload fields: {e}", flush=True)

        # Print formatted event logging to terminal
        print("\n" + "=" * 10 + " WHATSAPP WEBHOOK " + "=" * 10 + "\n", flush=True)
        if extracted_info:
            for item in extracted_info:
                if item.get('status_update'):
                    print(f"Status Update: Message to {item['recipient']} is {item['status']}", flush=True)
                    print(f"Timestamp: {item['timestamp']}", flush=True)
                else:
                    print(f"Sender: {item['sender']}", flush=True)
                    print(f"Name: {item['name']}", flush=True)
                    print(f"Message Type: {item['type']}", flush=True)
                    if item['type'] == 'text':
                        print(f"Message: {item['message']}", flush=True)
                    print(f"Message ID: {item['id']}", flush=True)
                    print(f"Timestamp: {item['timestamp']}", flush=True)
                print(flush=True)
        else:
            print("Event: Webhook event received (no message or status objects)\n", flush=True)

        print("-" * 10 + " COMPLETE PAYLOAD " + "-" * 10 + "\n", flush=True)
        try:
            print(json.dumps(data, indent=4), flush=True)
        except Exception:
            print(data, flush=True)
        print("\n" + "=" * 38 + "\n", flush=True)

        # Step 3: Fast Synchronous Filtering & Security Checks
        for item in incoming_messages:
            sender = item['sender']
            sender_name = item.get('name', '')
            msg_type = item.get('type')
            msg_id = item.get('msg_id')

            if not sender or sender == 'Unknown':
                continue

            # A. Malformed request with missing type -> ignore silently
            if not msg_type:
                logger.warning("Missing message type from sender %s. Ignoring.", sender)
                continue

            # B. Emoji reactions -> ignore silently
            if msg_type == 'reaction':
                logger.debug("Received emoji reaction from %s. Ignoring silently.", sender)
                continue

            # C. Unsupported media types -> notify user & log
            if msg_type != 'text':
                logger.info("Unsupported message type: %s from %s", msg_type, sender)
                print(f"\n[UNSUPPORTED MEDIA] Unsupported message type: '{msg_type}' from {sender}. Sending notice.\n", flush=True)
                send_whatsapp_message(to_number=sender, message_text=UNSUPPORTED_MEDIA_REPLY)

                RequestLog.objects.create(
                    phone_number=sender,
                    message_text=f"[{msg_type} message]",
                    response_text=UNSUPPORTED_MEDIA_REPLY,
                    status="blocked_type",
                    latency_ms=0
                )
                continue

            user_question = item.get('text', '')
            if not user_question:
                continue

            # D. Fast Rate Limiting Check (10 msgs per 60s)
            if is_rate_limited(sender):
                rate_limit_msg = (
                    "You are sending too many messages. "
                    "Please wait a moment before trying again."
                )
                print(f"[RATE LIMITED] Sender {sender} blocked. Sending warning.\n", flush=True)
                send_whatsapp_message(to_number=sender, message_text=rate_limit_msg)

                RequestLog.objects.create(
                    phone_number=sender,
                    message_text=user_question,
                    response_text=rate_limit_msg,
                    status="blocked_rate",
                    latency_ms=0
                )
                continue

            # E. Fast Prompt Injection Risk Guard
            injection_result = analyze_injection(user_question)
            if injection_result["is_injection"]:
                logger.warning(
                    "Prompt injection blocked | phone=%s score=%s reasons=%s",
                    sender,
                    injection_result["risk_score"],
                    injection_result["reasons"]
                )
                print(
                    f"\n[SECURITY] Prompt injection detected from {sender}! "
                    f"Score: {injection_result['risk_score']} | "
                    f"Reasons: {injection_result['reasons']}\n",
                    flush=True
                )

                # Persist blocked user message synchronously for security logs
                user = get_or_create_user(phone_number=sender, name=sender_name)
                conversation = get_or_create_conversation(user)
                save_message(
                    conversation=conversation,
                    role='user',
                    content=user_question,
                    guardrail_passed=False,
                    whatsapp_id=msg_id
                )

                safe_rejection_msg = "I am not able to process that message. Please ask me a normal question."
                send_whatsapp_message(to_number=sender, message_text=safe_rejection_msg)

                RequestLog.objects.create(
                    phone_number=sender,
                    message_text=user_question,
                    response_text=safe_rejection_msg,
                    status="blocked_injection",
                    latency_ms=0
                )
                continue

            # Step 4: Dispatch heavy RAG + Gemini pipeline to background thread
            thread = threading.Thread(
                target=process_message,
                args=(sender, user_question, sender_name, msg_id)
            )
            thread.daemon = True
            thread.start()

        # Step 5: Immediately return HTTP 200 OK (<50ms) to satisfy Meta's 5s SLA
        return JsonResponse({"status": "EVENT_RECEIVED"}, status=200)

    return HttpResponse("Method Not Allowed", status=405)
