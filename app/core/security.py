import hmac
import hashlib

def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    msg = f"{timestamp}.".encode() + body # signed message as the bytes
    mac = hmac.new(secret.encode(), msg, hashlib.sha256) # hmac.new() -> returns an hmac object, it does not return the signature we have to ask it for the result and hence we use .hexdigest()
    return mac.hexdigest()
    