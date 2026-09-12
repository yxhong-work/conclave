# backend/tests/test_pin.py
import string
from app.pin import generate_pin, ALPHABET

def test_pin_is_5_chars():
    assert len(generate_pin()) == 5

def test_pin_uses_only_alphabet():
    valid = set(ALPHABET)
    for _ in range(1000):
        assert set(generate_pin()) <= valid

def test_pin_is_case_sensitive_alphabet():
    assert set(string.ascii_letters + string.digits) == set(ALPHABET)
    assert len(ALPHABET) == 62

def test_pin_space_size():
    assert 62 ** 5 == 916_132_832

def test_pin_is_cryptographically_random():
    # extremely unlikely two consecutive pins are equal
    pins = {generate_pin() for _ in range(100)}
    assert len(pins) > 95
