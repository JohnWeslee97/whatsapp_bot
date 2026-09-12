import logging
from datetime import timedelta
from typing import List, Dict, Optional, Any
from django.utils import timezone
from chatbot.models import Customer, Conversation, Message

logger = logging.getLogger(__name__)

# Active session window: 24 hours
SESSION_EXPIRATION_HOURS = 24


def get_or_create_user(phone_number: str, name: str = "") -> Customer:
    """
    Looks up a Customer by phone number or creates a new one.
    Updates last_active to current timestamp and increments message_count.

    :param phone_number: Unique phone number string
    :param name: Profile name (optional)
    :return: Customer model instance
    """
    user, created = Customer.objects.get_or_create(
        phone_number=phone_number,
        defaults={
            'name': name,
            'last_active': timezone.now(),
            'message_count': 1
        }
    )

    if not created:
        user.last_active = timezone.now()
        user.message_count += 1
        if name and not user.name:
            user.name = name
        user.save(update_fields=['last_active', 'message_count', 'name'])

    return user


def get_or_create_conversation(user: Customer) -> Conversation:
    """
    Finds the most recent active conversation for the user within 24 hours.
    Creates a new conversation if none exists or if the last one expired (>24 hours).

    :param user: Customer model instance
    :return: Active Conversation model instance
    """
    cutoff_time = timezone.now() - timedelta(hours=SESSION_EXPIRATION_HOURS)

    # Find the most recent conversation ordered by last_updated descending
    latest_conversation = (
        Conversation.objects.filter(customer=user)
        .order_by('-last_updated')
        .first()
    )

    # Reuse existing conversation if within 24 hours
    if latest_conversation and latest_conversation.last_updated >= cutoff_time:
        return latest_conversation

    # Otherwise, start a fresh conversation session
    new_conversation = Conversation.objects.create(
        customer=user,
        last_updated=timezone.now()
    )
    return new_conversation


def save_message(
    conversation: Conversation,
    role: str,
    content: str,
    latency_ms: Optional[int] = None,
    guardrail_passed: Optional[bool] = None,
    whatsapp_id: Optional[str] = None
) -> Message:
    """
    Saves an incoming or outgoing message to the database linked to the conversation.
    Updates the conversation's last_updated timestamp.

    :param conversation: Conversation instance
    :param role: "user" or "assistant"
    :param content: Message text
    :param latency_ms: Response generation time in milliseconds (optional)
    :param guardrail_passed: Boolean indicating security guard check (optional)
    :param whatsapp_id: Meta WhatsApp message ID (optional)
    :return: Created Message model instance
    """
    if role not in ["user", "assistant"]:
        raise ValueError(f"Invalid role '{role}'. Role must be 'user' or 'assistant'.")

    # Create message record
    message = Message.objects.create(
        conversation=conversation,
        role=role,
        text=content,
        latency_ms=latency_ms,
        guardrail_passed=guardrail_passed,
        whatsapp_id=whatsapp_id
    )

    # Touch conversation last_updated
    conversation.last_updated = timezone.now()
    conversation.save(update_fields=['last_updated'])

    return message


def get_conversation_history(conversation: Conversation, last_n: int = 6) -> List[Dict[str, str]]:
    """
    Queries the last N messages for a conversation ordered chronologically (oldest first).

    :param conversation: Conversation instance
    :param last_n: Number of recent messages to retrieve (default: 6)
    :return: List of dicts in the format [{"role": "user"|"assistant", "content": "..."}]
    """
    if not conversation:
        return []

    # Fetch last N messages by created_at descending, then reverse to chronological order
    recent_messages = list(
        Message.objects.filter(conversation=conversation)
        .order_by('-created_at')[:last_n]
    )

    # Reverse to oldest first for LLM multi-turn context
    recent_messages.reverse()

    history = [
        {
            "role": msg.role,
            "content": msg.text
        }
        for msg in recent_messages
    ]

    return history
