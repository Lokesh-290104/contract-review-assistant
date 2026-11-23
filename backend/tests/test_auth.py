"""The backend must verify the Clerk token itself and never trust a user id from the client."""

from cryptography.hazmat.primitives.asymmetric import rsa


def test_no_token_is_401(client):
    assert client.get("/api/contracts").status_code == 401


def test_garbage_token_is_401(client):
    r = client.get("/api/contracts", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_token_signed_by_another_key_is_401(client, make_token):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    r = client.get("/api/contracts", headers={"Authorization": f"Bearer {make_token('user_a', key=other)}"})
    assert r.status_code == 401


def test_expired_token_is_401(client, make_token):
    r = client.get("/api/contracts", headers={"Authorization": f"Bearer {make_token('user_a', exp_in=-60)}"})
    assert r.status_code == 401


def test_wrong_authorized_party_is_401(client, make_token):
    token = make_token("user_a", azp="https://evil.example")
    assert client.get("/api/contracts", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_valid_token_works(client, auth):
    assert client.get("/api/contracts", headers=auth("user_a")).status_code == 200


def test_health_needs_no_token(client):
    assert client.get("/api/health").json() == {"ok": True}
