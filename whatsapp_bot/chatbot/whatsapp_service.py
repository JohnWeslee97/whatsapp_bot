import os
import requests
import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

def mark_message_as_read(message_id: str, show_typing: bool = True) -> dict:
    """
    Marks an incoming WhatsApp message as read (blue double ticks)
    and optionally shows the 'typing...' indicator while generating response.

    :param message_id: The WhatsApp message ID (wamid) from incoming webhook
    :param show_typing: Whether to display 'typing...' animation in chat
    :return: Response dictionary from Meta API
    """
    if not message_id or message_id == 'Unknown':
        return {}

    token = getattr(settings, 'WHATSAPP_TOKEN', os.getenv('WHATSAPP_TOKEN', ''))
    phone_number_id = getattr(settings, 'PHONE_NUMBER_ID', os.getenv('PHONE_NUMBER_ID', ''))

    if not token or not phone_number_id:
        return {}

    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    if show_typing:
        payload["typing_indicator"] = {
            "type": "text"
        }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        if response.status_code == 200:
            print(f"[WHATSAPP]   Marked message {message_id[:25]}... as read + typing indicator [OK]", flush=True)
        return response.json() if response.content else {}
    except Exception as e:
        logger.warning("Failed to send read status/typing indicator to Meta: %s", e)
        return {}


def send_whatsapp_message(to_number: str, message_text: str) -> dict:
    """
    Sends a text message to a WhatsApp user via Meta Cloud API.

    :param to_number: Recipient's phone number with country code (e.g. '917305831401')
    :param message_text: The text string to send to the recipient
    :return: Response dictionary from Meta API
    """
    token = getattr(settings, 'WHATSAPP_TOKEN', os.getenv('WHATSAPP_TOKEN', ''))
    phone_number_id = getattr(settings, 'PHONE_NUMBER_ID', os.getenv('PHONE_NUMBER_ID', ''))

    if not token or not phone_number_id:
        print("[ERROR] WHATSAPP_TOKEN or PHONE_NUMBER_ID is missing in settings/.env", flush=True)
        return {"error": "Missing credentials"}

    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message_text
        }
    }

    try:
        try:
            print(f"\n[SENDING WHATSAPP MESSAGE] To: {to_number} | Message: '{message_text}'", flush=True)
        except UnicodeEncodeError:
            safe_text = message_text.encode('ascii', 'replace').decode('ascii')
            print(f"\n[SENDING WHATSAPP MESSAGE] To: {to_number} | Message: '{safe_text}'", flush=True)

        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        response_data = response.json()
        
        if response.status_code == 200:
            msg_id = response_data.get('messages', [{}])[0].get('id', 'Unknown')
            print(f"[MESSAGE SENT SUCCESSFULLY] Message ID: {msg_id}\n", flush=True)
        else:
            print(f"[META API ERROR] Status: {response.status_code} | Response: {response_data}\n", flush=True)
            
        return response_data

    except requests.exceptions.RequestException as e:
        print(f"[NETWORK ERROR] Failed to send message to WhatsApp: {e}", flush=True)
        return {"error": str(e)}
