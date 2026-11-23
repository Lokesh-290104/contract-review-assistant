"""Clerk session-token check. Runs on every API request.

The frontend sends `Authorization: Bearer <Clerk session JWT>`. We verify the RS256
signature with Clerk's public keys (JWKS), check expiry and the authorized party
(`azp`), and use the `sub` claim as the user id. A user id sent by the client is
never trusted.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

import jwt
from fastapi import Depends, HTTPException, Request, status

from .config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentUser:
    id: str


class KeyProvider(Protocol):
    def signing_key(self, token: str) -> object: ...


class JwksKeyProvider:
    """Fetches and caches Clerk's public signing keys."""

    def __init__(self, url: str, secret_key: str = ""):
        # Clerk's API refuses Python's default "Python-urllib" User-Agent with 403, so send our own.
        headers = {"User-Agent": "contract-review-assistant/1.0"}
        # The Backend API JWKS endpoint needs the secret key; the Frontend API one is public.
        if "api.clerk.com" in url and secret_key:
            headers["Authorization"] = f"Bearer {secret_key}"
        self._client = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, headers=headers)

    def signing_key(self, token: str) -> object:
        return self._client.get_signing_key_from_jwt(token).key


class AuthError(Exception):
    pass


class KeysUnavailable(AuthError):
    """Clerk's signing keys could not be downloaded: a server problem, not the user's."""


def verify_token(token: str, keys: KeyProvider, authorized_parties: list[str]) -> CurrentUser:
    try:
        key = keys.signing_key(token)
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            options={"require": ["exp", "iat", "sub"]},
            leeway=5,
        )
    except jwt.PyJWKClientConnectionError as exc:
        raise KeysUnavailable(f"could not load signing keys: {exc}") from exc
    except jwt.PyJWKClientError as exc:  # e.g. the token's key id is not in Clerk's key set
        raise AuthError(f"unknown signing key: {exc}") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    azp = claims.get("azp")
    if azp and authorized_parties and azp not in authorized_parties:
        raise AuthError("token issued for another origin")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("token has no user")
    return CurrentUser(id=sub)


_provider: KeyProvider | None = None


def get_key_provider() -> KeyProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        _provider = JwksKeyProvider(s.clerk_jwks_url, s.clerk_secret_key)
    return _provider


def current_user(request: Request, keys: KeyProvider = Depends(get_key_provider)) -> CurrentUser:
    """FastAPI dependency used by every route."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    try:
        return verify_token(token, keys, get_settings().authorized_parties)
    except KeysUnavailable as exc:
        log.error("sign-in check failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in cannot be checked right now. Please try again in a minute."
        ) from exc
    except AuthError as exc:
        log.warning("rejected token: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session is invalid or expired. Please sign in again.") from exc
