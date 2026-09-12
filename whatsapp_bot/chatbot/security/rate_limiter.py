import time
import logging
from threading import Lock

logger = logging.getLogger(__name__)

class RateLimiter:
    """
    In-memory Sliding Window Rate Limiter.
    Tracks message timestamps per phone number to prevent spam without Redis.
    """
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests_history = {}  # {phone_number: [timestamp1, timestamp2, ...]}
        self.lock = Lock()          # Ensures thread-safety across concurrent webhook requests

    def is_rate_limited(self, phone_number: str) -> bool:
        """
        Determines whether a given phone number has exceeded the allowed message rate.
        
        :param phone_number: Recipient's phone number string
        :return: True if rate limited (blocked), False if allowed
        """
        now = time.time()
        cutoff_time = now - self.window_seconds

        with self.lock:
            # Retrieve previous timestamps list for this phone number
            timestamps = self.requests_history.get(phone_number, [])

            # 1. Filter out timestamps older than the sliding window (60 seconds)
            valid_timestamps = [t for t in timestamps if t > cutoff_time]

            # 2. If the user sent max_requests (10) or more in the window, block them
            if len(valid_timestamps) >= self.max_requests:
                self.requests_history[phone_number] = valid_timestamps
                logger.warning(
                    f"[RATE LIMIT] User {phone_number} reached rate limit "
                    f"({len(valid_timestamps)}/{self.max_requests} msgs in {self.window_seconds}s)."
                )
                return True

            # 3. Otherwise, append current timestamp and allow the message
            valid_timestamps.append(now)
            self.requests_history[phone_number] = valid_timestamps
            return False

    def clear(self):
        """Clears all stored rate limit entries (useful for unit tests)."""
        with self.lock:
            self.requests_history.clear()


# Single global RateLimiter instance reused across all webhook requests
global_rate_limiter = RateLimiter(max_requests=10, window_seconds=60)

def is_rate_limited(phone_number: str) -> bool:
    """
    Convenience wrapper function using the singleton global_rate_limiter.
    """
    return global_rate_limiter.is_rate_limited(phone_number)
