# backend/app/pin.py
import secrets
import string

ALPHABET = string.ascii_letters + string.digits  # 62 chars, case-sensitive + digits

def generate_pin() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(5))  # 62^5 ≈ 916M
