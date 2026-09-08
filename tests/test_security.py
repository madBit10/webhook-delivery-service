from app.core.security import sign_payload

SECRET = "test-secret"
TIMESTAMP = 1700000000
BODY = b'{"amount": 100}'

# same input always produce same output
def test_signature_is_deterministic():
    assert sign_payload(SECRET, TIMESTAMP, BODY) == sign_payload(SECRET, TIMESTAMP, BODY)

# test if the the test signature changes with body
def test_signature_changes_with_body():
    other_body = b'{"amount": 101}'
    assert sign_payload(SECRET, TIMESTAMP, BODY) != sign_payload(SECRET, TIMESTAMP, other_body)

# test if the signature changes with timestamp
def test_signature_changes_with_timestamp():
    assert sign_payload(SECRET, TIMESTAMP, BODY) != sign_payload(SECRET, TIMESTAMP + 1, BODY)

# test if the signature changes with secret
def test_signature_changes_with_secret():
    assert sign_payload(SECRET, TIMESTAMP, BODY) != sign_payload("other-body", TIMESTAMP, BODY)

def test_signature_is_64_lowercase_hex():
    # test if the signature is of 64 bits
    assert len(sign_payload(SECRET, TIMESTAMP, BODY)) == 64
    # test if the signature is lowercase hex (base of 16)
    assert set(sign_payload(SECRET, TIMESTAMP, BODY)) <= set("0123456789abcdef")


def test_seperator_prevents_boundary_ambiguity():
    assert sign_payload(SECRET, 12, b"3abc") != sign_payload(SECRET, 123, b"abc")