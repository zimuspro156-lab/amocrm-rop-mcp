from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlparse

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from oauth_server import McpOAuth, password_hash, pkce_challenge

REDIRECT = "http://127.0.0.1:9876/callback"
CLIENT_ID = "chatgpt-amocrm-mcp"
PASSWORD = "test-owner-password"


def _verifier() -> str:
    return secrets.token_urlsafe(32)


def _oauth(tmp_path, public_url: str = "http://testserver") -> McpOAuth:
    return McpOAuth(
        public_url=public_url,
        issuer=public_url,
        resource=public_url + "/mcp",
        client_id=CLIENT_ID,
        redirect_uris=[REDIRECT],
        login="mcp-admin",
        password_hash_value=password_hash(PASSWORD),
        data_dir=tmp_path,
        access_token_ttl=3600,
        refresh_token_ttl=2592000,
        auth_code_ttl=300,
    )


def _app(oauth: McpOAuth) -> TestClient:
    routes = [Route(path, endpoint=handler, methods=methods) for path, methods, handler in oauth.handlers()]
    return TestClient(Starlette(routes=routes))


def test_redirect_allowlist(tmp_path) -> None:
    oauth = _oauth(tmp_path, public_url="https://amocrm.example")
    try:
        assert oauth.allowed_redirect(REDIRECT) is True
        assert oauth.allowed_redirect("https://attacker.example/callback") is False
        metadata = oauth.authorization_server_metadata()
        assert "registration_endpoint" not in metadata
        assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
        assert metadata["issuer"] == "https://amocrm.example"
    finally:
        oauth.close()


def test_pkce_s256_matches_rfc_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_http_oauth_public_client_pkce_login_and_refresh(tmp_path) -> None:
    oauth = _oauth(tmp_path)
    client = _app(oauth)
    try:
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["authorization_endpoint"].endswith("/authorize")
        assert metadata["grant_types_supported"] == ["authorization_code", "refresh_token"]
        resource = client.get("/.well-known/oauth-protected-resource").json()
        assert resource["resource"].endswith("/mcp")

        verifier = _verifier()
        challenge = pkce_challenge(verifier)
        auth = client.get(
            "/authorize",
            params={
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "mcp offline_access",
                "state": "test-state",
                "resource": "http://testserver/mcp",
            },
        )
        assert auth.status_code == 200
        assert "Авторизация amoCRM MCP" in auth.text
        request_id = auth.text.split('name="requestId" value="', 1)[1].split('"', 1)[0]
        cookie = auth.headers.get("set-cookie", "").split(";", 1)[0]

        denied = client.post(
            "/authorize",
            data={"requestId": request_id, "login": "mcp-admin", "password": "wrong-password"},
            headers={"origin": "http://testserver", "cookie": cookie},
        )
        assert denied.status_code == 401
        assert denied.headers.get("location") is None

        missing_cookie = _app(oauth).post(
            "/authorize",
            data={"requestId": request_id, "login": "mcp-admin", "password": PASSWORD},
            headers={"origin": "http://testserver"},
        )
        assert missing_cookie.status_code == 400

        wrong_origin = client.post(
            "/authorize",
            data={"requestId": request_id, "login": "mcp-admin", "password": PASSWORD},
            headers={"origin": "https://evil.example", "cookie": cookie},
        )
        assert wrong_origin.status_code == 403

        login = client.post(
            "/authorize",
            data={"requestId": request_id, "login": "mcp-admin", "password": PASSWORD},
            headers={
                "origin": "null",
                "cookie": cookie,
                "accept": "application/json",
            },
        )
        assert login.status_code == 200
        granted = login.json()["redirect"]
        parsed = urlparse(granted)
        qs = parse_qs(parsed.query)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == REDIRECT
        assert qs["iss"] == ["http://testserver"]
        assert qs["state"] == ["test-state"]
        code = qs["code"][0]

        retry = client.post(
            "/authorize",
            data={"requestId": request_id, "login": "mcp-admin", "password": PASSWORD},
            headers={"origin": "null", "cookie": cookie, "accept": "application/json"},
        )
        assert retry.status_code == 200
        assert retry.json()["redirect"] == granted

        bad_pkce = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": "wrong-verifier-wrong-verifier-wrong-verifie",
                "redirect_uri": REDIRECT,
                "resource": "http://testserver/mcp",
            },
        )
        assert bad_pkce.status_code == 400
        assert bad_pkce.json()["error"] == "invalid_grant"

        tokens = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT,
                "resource": "http://testserver/mcp",
            },
        )
        assert tokens.status_code == 200
        payload = tokens.json()
        assert payload["token_type"] == "Bearer"
        assert payload["expires_in"] == 3600
        assert payload["access_token"]
        assert payload["refresh_token"]
        assert "mcp" in payload["scope"]

        replay_code = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT,
                "resource": "http://testserver/mcp",
            },
        )
        assert replay_code.status_code == 400

        verified = oauth.verify_access_token(payload["access_token"])
        assert verified is not None
        assert verified.client_id == CLIENT_ID
        assert "mcp" in verified.scopes
        assert oauth.verify_access_token("nope") is None

        refreshed = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": payload["refresh_token"],
            },
        )
        assert refreshed.status_code == 200
        next_tokens = refreshed.json()
        assert next_tokens["access_token"] != payload["access_token"]
        assert next_tokens["refresh_token"]

        replay_refresh = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": payload["refresh_token"],
            },
        )
        assert replay_refresh.status_code == 400

        blocked = client.get(
            "/authorize",
            params={
                "client_id": CLIENT_ID,
                "redirect_uri": "https://attacker.example/callback",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert blocked.status_code == 400
        assert blocked.headers.get("location") is None
        assert client.get("/register").status_code == 404
    finally:
        oauth.close()


def test_authorize_without_redirect_uri_configured(tmp_path) -> None:
    oauth = McpOAuth(
        public_url="https://amocrm.example",
        issuer="https://amocrm.example",
        resource="https://amocrm.example/mcp",
        client_id=CLIENT_ID,
        redirect_uris=[],
        login="admin",
        password_hash_value=password_hash("x"),
        data_dir=tmp_path,
    )
    client = _app(oauth)
    try:
        response = client.get(
            "/authorize",
            params={
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "code_challenge": pkce_challenge(_verifier()),
                "code_challenge_method": "S256",
            },
        )
        assert response.status_code == 400
        assert "OAUTH_REDIRECT_URI" in response.text
    finally:
        oauth.close()
