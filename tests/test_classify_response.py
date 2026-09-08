import pytest
from app.services.event import classify_response, DeliveryOutcome

@pytest.mark.parametrize("status_code, expected", [
    (None, DeliveryOutcome.RETRYABLE), #timeout/connection refused/DNS
    (200, DeliveryOutcome.DELIVERED), # success 
    (201, DeliveryOutcome.DELIVERED), # success
    (408, DeliveryOutcome.RETRYABLE),
    (429, DeliveryOutcome.RETRYABLE),
    (400, DeliveryOutcome.TERMINAL),
    (401, DeliveryOutcome.TERMINAL),
    (404, DeliveryOutcome.TERMINAL),
    (500, DeliveryOutcome.RETRYABLE),
    (503, DeliveryOutcome.RETRYABLE),
    (301, DeliveryOutcome.TERMINAL)
])
def test_classify_response(status_code, expected):
    assert classify_response(status_code) is expected