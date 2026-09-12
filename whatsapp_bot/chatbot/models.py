from django.db import models
from django.utils import timezone


class Customer(models.Model):
    phone_number = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100, blank=True)
    last_active = models.DateTimeField(default=timezone.now)
    message_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.phone_number} ({self.name or 'Unknown'})"


class Conversation(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='conversations')
    last_updated = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.phone_number} - {self.created_at:%Y-%m-%d %H:%M}"


class Message(models.Model):
    ROLE_CHOICES = [
        ('user', 'User'),
        ('assistant', 'Assistant')
    ]
    DIRECTION_CHOICES = [
        ('in', 'Incoming'),
        ('out', 'Outgoing')
    ]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='user')
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES, blank=True)
    text = models.TextField()
    whatsapp_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    guardrail_passed = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Automatically sync direction from role if not explicitly set
        if not self.direction:
            self.direction = 'in' if self.role == 'user' else 'out'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.role}] {self.text[:50]}"


class Document(models.Model):
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="knowledge_base/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    indexed = models.BooleanField(default=False)
    indexed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class RequestLog(models.Model):
    STATUS_CHOICES = [
        ("success", "Success"),
        ("blocked_hmac", "Blocked - Invalid Signature"),
        ("blocked_rate", "Blocked - Rate Limited"),
        ("blocked_injection", "Blocked - Injection Attempt"),
        ("blocked_type", "Blocked - Unsupported Type"),
        ("failed", "Failed - Pipeline Error"),
        ("fallback", "Fallback - Guardrail Failed"),
    ]

    phone_number = models.CharField(max_length=50)
    message_text = models.TextField()
    response_text = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)
    latency_ms = models.IntegerField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.status}] {self.phone_number} - {self.timestamp:%Y-%m-%d %H:%M:%S}"