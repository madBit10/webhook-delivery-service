def test_create_endpoint_does_not_leak_secret(client):
    resp = client.post("/endpoints", json = {"url": "https://example.com/hook", "event_types": "order.created"})

    assert resp.status_code == 201

    assert "secret" not in resp.json()