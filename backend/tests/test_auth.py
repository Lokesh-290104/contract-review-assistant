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


def test_key_download_failure_is_503_not_401(client, auth):
    import jwt as pyjwt

    from app.auth import get_key_provider
    from app.main import app

    class Broken:
        def signing_key(self, token):
            raise pyjwt.PyJWKClientConnectionError("HTTP Error 403: Forbidden")

    app.dependency_overrides[get_key_provider] = lambda: Broken()
    r = client.get("/api/contracts", headers=auth("user_a"))
    assert r.status_code == 503
    assert "cannot be checked" in r.json()["detail"]


def test_jwks_client_sends_a_user_agent():
    from app.auth import JwksKeyProvider

    provider = JwksKeyProvider("https://api.clerk.com/v1/jwks", "sk_test_x")
    headers = provider._client.headers
    assert headers["User-Agent"].startswith("contract-review-assistant")
    assert headers["Authorization"] == "Bearer sk_test_x"
