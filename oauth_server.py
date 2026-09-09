"""OAuth 2.0 Authorization Code + PKCE for ChatGPT → MCP.

This is independent of amoCRM OAuth. ChatGPT never receives amoCRM tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import bcrypt
from mcp.server.auth.provider import AccessToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from oauth_store import OAuthStore

logger = logging.getLogger(__name__)

BCRYPT_ROUNDS = 12
LOGIN_WINDOW_SEC = 15 * 60
LOGIN_MAX_FAILURES = 8
PENDING_TTL_SEC = 300
SUPPORTED_SCOPES = ("mcp", "offline_access")
PKCE_METHOD = "S256"
PKCE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
CSRF_COOKIE = "amocrm_oauth_csrf"

Handler = Callable[[Request], Awaitable[Response]]

LOGIN_SCRIPT = """
document.getElementById("oauth-form").addEventListener("submit",async(e)=>{
  e.preventDefault();
  const f=e.target,b=document.getElementById("oauth-submit");
  b.textContent="Подключаем…";
  const c=new AbortController();
  const t=setTimeout(()=>c.abort(),20000);
  try{
    const r=await fetch("/authorize",{
      method:"POST",
      headers:{"content-type":"application/x-www-form-urlencoded",accept:"application/json"},
      body:new URLSearchParams(new FormData(f)),
      credentials:"same-origin",
      signal:c.signal
    });
    clearTimeout(t);
    const type=r.headers.get("content-type")||"";
    if(type.includes("application/json")){
      const d=await r.json();
      if(d.redirect){location.replace(d.redirect);return}
    }
    document.open();
    document.write(await r.text());
    document.close();
  }catch{
    clearTimeout(t);
    b.textContent="Повторить подключение";
  }
});
""".strip()


def _now() -> int:
    return int(time.time())


def secret() -> str:
    return secrets.token_urlsafe(32)


def hash_token(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def password_hash(password: str) -> str:
    return bcrypt.hashpw(str(password).encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def password_matches(password: str, stored: str) -> bool:
    try:
        return bcrypt.checkpw(str(password).encode("utf-8"), str(stored or "").encode("ascii"))
    except ValueError:
        return False


def safe_equal(left: str, right: str) -> bool:
    a = hashlib.sha256(str(left).encode("utf-8")).digest()
    b = hashlib.sha256(str(right).encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def parse_scopes(value: str) -> list[str] | None:
    requested = [item for item in re.split(r"[+\s]+", str(value or "").strip()) if item]
    if not requested:
        return list(SUPPORTED_SCOPES)
    if any(scope not in SUPPORTED_SCOPES for scope in requested):
        return None
    return list(dict.fromkeys(requested))


def _cookie_value(header: str | None, name: str) -> str:
    if not header:
        return ""
    prefix = name + "="
    for part in header.split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix) :]
    return ""


def _html_page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{margin:0;background:#f4f6fb;color:#1c2434;font:16px/1.6 system-ui}"
        "main{max-width:480px;margin:10vh auto;padding:38px;border:1px solid #d9e0ee;"
        "border-radius:24px;background:white}small{color:#5b6b86}h1{font-size:28px;line-height:1.2}"
        "input,button{box-sizing:border-box;width:100%;padding:14px;border-radius:12px;font:inherit}"
        "input{border:1px solid #c5d0e0;margin:8px 0 18px}button{border:0;background:#2f6fed;color:white;cursor:pointer}"
        ".note{background:#eef3ff;padding:14px;border-radius:12px;font-size:14px}"
        ".error{background:#fdecec;color:#7a1f1f;padding:14px;border-radius:12px}label{display:block}</style>"
        f"<main>{body}</main></html>"
    )


class McpOAuth:
    def __init__(
        self,
        *,
        public_url: str,
        issuer: str,
        resource: str,
        client_id: str,
        redirect_uris: list[str],
        login: str,
        password_hash_value: str,
        data_dir: Path,
        access_token_ttl: int = 3600,
        refresh_token_ttl: int = 2592000,
        auth_code_ttl: int = 300,
    ) -> None:
        self.public_url = public_url.rstrip("/")
        self.issuer = issuer.rstrip("/")
        self.resource = resource
        self.client_id = client_id
        self.redirect_uris = [uri for uri in redirect_uris if uri]
        self.login = login
        self.password_hash_value = password_hash_value
        self.access_token_ttl = access_token_ttl
        self.refresh_token_ttl = refresh_token_ttl
        self.auth_code_ttl = auth_code_ttl
        self.store = OAuthStore(Path(data_dir))
        self.pending: dict[str, dict[str, Any]] = {}
        self.failures: list[int] = []
        self.dummy_hash = password_hash(secret())

    @classmethod
    def from_settings(cls, settings: Any) -> McpOAuth:
        return cls(
            public_url=settings.mcp_public_url,
            issuer=settings.oauth_issuer_url,
            resource=settings.mcp_resource_url,
            client_id=settings.oauth_client_id,
            redirect_uris=settings.oauth_redirect_uris,
            login=settings.mcp_login,
            password_hash_value=settings.mcp_password_hash,
            data_dir=settings.oauth_store_dir,
            access_token_ttl=settings.oauth_access_token_ttl,
            refresh_token_ttl=settings.oauth_refresh_token_ttl,
            auth_code_ttl=settings.oauth_auth_code_ttl,
        )

    def close(self) -> None:
        self.store.close()

    def authorization_server_metadata(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/authorize",
            "token_endpoint": self.issuer + "/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": [PKCE_METHOD],
            "scopes_supported": list(SUPPORTED_SCOPES),
            "token_endpoint_auth_methods_supported": ["none"],
            "authorization_response_iss_parameter_supported": True,
        }

    def protected_resource_metadata(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "authorization_servers": [self.issuer],
            "scopes_supported": list(SUPPORTED_SCOPES),
            "bearer_methods_supported": ["header"],
        }

    def www_authenticate(self, error: str | None = None) -> str:
        metadata = self.public_url + "/.well-known/oauth-protected-resource"
        parts = ['Bearer realm="amoCRM ROP MCP"', f'resource_metadata="{metadata}"', 'scope="mcp"']
        if error:
            parts.insert(1, f'error="{error}"')
        return ", ".join(parts)

    def allowed_redirect(self, uri: str) -> bool:
        return str(uri or "") in self.redirect_uris

    def check_resource(self, resource: str) -> bool:
        if not resource:
            return True
        return str(resource) == self.resource

    def verify_access_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        row = self.store.get_access(hash_token(token))
        if row is None or row["revoked"] or row["expires_at"] < _now():
            return None
        scopes = [item for item in str(row.get("scope") or "").split(" ") if item]
        return AccessToken(
            token=token,
            client_id=str(row["client_id"]),
            scopes=scopes,
            expires_at=int(row["expires_at"]),
            resource=self.resource,
        )

    def handlers(self) -> list[tuple[str, list[str], Handler]]:
        return [
            ("/.well-known/oauth-authorization-server", ["GET", "OPTIONS"], self.handle_as_metadata),
            ("/.well-known/oauth-authorization-server/mcp", ["GET", "OPTIONS"], self.handle_as_metadata),
            ("/.well-known/oauth-protected-resource", ["GET", "OPTIONS"], self.handle_resource_metadata),
            ("/.well-known/oauth-protected-resource/mcp", ["GET", "OPTIONS"], self.handle_resource_metadata),
            ("/authorize", ["GET", "POST"], self.handle_authorize),
            ("/token", ["POST", "OPTIONS"], self.handle_token),
            ("/", ["GET"], self.handle_root),
        ]

    async def handle_root(self, _request: Request) -> Response:
        return JSONResponse(
            {
                "name": "amoCRM ROP MCP",
                "endpoint": "/mcp",
                "authentication": "OAuth 2.0 Authorization Code + PKCE S256",
            }
        )

    async def handle_as_metadata(self, request: Request) -> Response:
        if request.method == "OPTIONS":
            return self._cors(Response(status_code=204))
        return self._cors(JSONResponse(self.authorization_server_metadata()))

    async def handle_resource_metadata(self, request: Request) -> Response:
        if request.method == "OPTIONS":
            return self._cors(Response(status_code=204))
        return self._cors(JSONResponse(self.protected_resource_metadata()))

    async def handle_token(self, request: Request) -> Response:
        if request.method == "OPTIONS":
            return self._cors(Response(status_code=204))
        body = await self._read_form(request)
        client_id = str(body.get("client_id") or "")
        if not client_id or not safe_equal(client_id, self.client_id):
            return self._token_error(401, "invalid_client", "Неверный client_id")
        grant = str(body.get("grant_type") or "")
        if grant == "authorization_code":
            return self._exchange_code(body)
        if grant == "refresh_token":
            return self._exchange_refresh(body)
        return self._token_error(400, "unsupported_grant_type", "Поддерживаются authorization_code и refresh_token")

    async def handle_authorize(self, request: Request) -> Response:
        if request.method == "GET":
            return await self.authorize_get(request)
        return await self.authorize_post(request)

    async def authorize_get(self, request: Request) -> Response:
        checked = self._validate_authorize_params(self._query_params(request))
        if checked.get("http_error"):
            return self._html(
                400,
                "Авторизация amoCRM MCP",
                f"<h1>Авторизация недоступна</h1><p class=\"error\">{html.escape(str(checked['message']))}</p>",
            )
        if not checked.get("ok"):
            return self._redirect_error(checked["params"], str(checked["oauth_error"]))
        self._prune_pending()
        if len(self.pending) >= 100:
            return self._html(
                429,
                "Авторизация amoCRM MCP",
                "<h1>Слишком много попыток входа</h1><p>Подождите минуту и откройте подключение заново.</p>",
            )
        request_id = secret()
        csrf = secret()
        self.pending[request_id] = {
            **checked["params"],
            "csrf": csrf,
            "csrf_hash": hash_token(csrf),
            "expires_at": _now() + PENDING_TTL_SEC,
        }
        response = self._login_form(request_id, "", csrf)
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            httponly=True,
            secure=self.public_url.startswith("https:"),
            samesite="lax",
            max_age=PENDING_TTL_SEC,
            path="/authorize",
        )
        return response

    async def authorize_post(self, request: Request) -> Response:
        origin = request.headers.get("origin") or ""
        if origin and origin != "null" and origin != self.public_url:
            return self._html(
                403,
                "Авторизация amoCRM MCP",
                "<h1>Неверный источник запроса</h1><p>Откройте страницу входа заново из ChatGPT.</p>",
            )
        body = await self._read_form(request)
        request_id = str(body.get("requestId") or "")
        self._prune_pending()
        pending = self.pending.get(request_id)
        csrf = _cookie_value(request.headers.get("cookie"), CSRF_COOKIE) or str(body.get("csrf") or "")
        if (
            pending is None
            or pending["expires_at"] < _now()
            or not csrf
            or hash_token(csrf) != pending["csrf_hash"]
        ):
            return self._html(
                400,
                "Авторизация amoCRM MCP",
                "<h1>Запрос входа истёк</h1><p>Начните подключение заново из ChatGPT.</p>",
            )
        if self._login_locked():
            return self._html(
                429,
                "Авторизация amoCRM MCP",
                "<h1>Слишком много неверных попыток</h1><p>Подождите 15 минут и повторите вход.</p>",
            )
        login = str(body.get("login") or "")
        password = str(body.get("password") or "")
        login_ok = safe_equal(login, self.login)
        stored = self.password_hash_value if login_ok else self.dummy_hash
        if not login_ok or not password_matches(password, stored):
            self._record_failure()
            return self._login_form(
                request_id,
                "Неверный логин или пароль. Токен amoCRM сюда вводить не нужно.",
                pending["csrf"],
            )
        code = pending.get("authorization_code")
        existing = self.store.peek_code(hash_token(code)) if code else {"ok": False}
        if not existing.get("ok"):
            code = secret()
            pending["authorization_code"] = code
            created_at = _now()
            scopes = parse_scopes(pending["scope"]) or list(SUPPORTED_SCOPES)
            self.store.save_code(
                {
                    "token_hash": hash_token(code),
                    "client_id": pending["client_id"],
                    "redirect_uri": pending["redirect_uri"],
                    "code_challenge": pending["code_challenge"],
                    "code_challenge_method": pending["code_challenge_method"],
                    "scope": " ".join(scopes),
                    "resource": pending["resource"] or self.resource,
                    "pending_id": request_id,
                    "created_at": created_at,
                    "expires_at": created_at + self.auth_code_ttl,
                }
            )
        target = self._callback_url(pending["redirect_uri"], {"code": code, "state": pending.get("state") or "", "iss": self.issuer})
        logger.info("OAuth login granted")
        return self._finish_redirect(request, target)

    def _finish_redirect(self, request: Request, target: str) -> Response:
        accept = request.headers.get("accept") or ""
        if "application/json" in accept:
            return JSONResponse({"redirect": target})
        nonce = secret()
        return self._html(
            200,
            "Авторизация amoCRM MCP",
            (
                "<h1>Подключение выполнено</h1><p>Сейчас откроется ChatGPT.</p>"
                f"<p><a id=\"continue\" href=\"{html.escape(target)}\">Продолжить в ChatGPT</a></p>"
                f"<script nonce=\"{nonce}\">location.replace({target!r})</script>"
            ),
            nonce,
        )

    def _exchange_code(self, body: dict[str, str]) -> Response:
        code = str(body.get("code") or "")
        verifier = str(body.get("code_verifier") or "")
        redirect_uri = str(body.get("redirect_uri") or "")
        resource = str(body.get("resource") or "")
        if not code or not verifier or not redirect_uri:
            return self._token_error(400, "invalid_request", "Не хватает параметров")
        if not self.allowed_redirect(redirect_uri):
            return self._token_error(400, "invalid_grant", "redirect_uri не совпадает")
        if resource and not self.check_resource(resource):
            return self._token_error(400, "invalid_target", "Неверный resource")
        if not PKCE_RE.fullmatch(verifier):
            return self._token_error(400, "invalid_grant", "Неверный code_verifier")
        peeked = self.store.peek_code(hash_token(code))
        if not peeked.get("ok"):
            reason = "Код уже использован" if peeked.get("reason") == "replay" else "Код недействителен или истёк"
            return self._token_error(400, "invalid_grant", reason)
        row = peeked["row"]
        if row["client_id"] != self.client_id or row["redirect_uri"] != redirect_uri:
            return self._token_error(400, "invalid_grant", "Код недействителен")
        if row["code_challenge_method"] != PKCE_METHOD:
            return self._token_error(400, "invalid_grant", "Поддерживается только PKCE S256")
        if not safe_equal(pkce_challenge(verifier), row["code_challenge"]):
            return self._token_error(400, "invalid_grant", "Проверка PKCE не пройдена")
        consumed = self.store.consume_code(hash_token(code))
        if not consumed.get("ok"):
            return self._token_error(400, "invalid_grant", "Код уже использован")
        pending_id = consumed["row"].get("pending_id")
        if pending_id:
            self.pending.pop(str(pending_id), None)
        return JSONResponse(self._issue_tokens(row["client_id"], row["scope"]))

    def _exchange_refresh(self, body: dict[str, str]) -> Response:
        refresh = str(body.get("refresh_token") or "")
        resource = str(body.get("resource") or "")
        if not refresh:
            return self._token_error(400, "invalid_request", "Нужен refresh_token")
        if resource and not self.check_resource(resource):
            return self._token_error(400, "invalid_target", "Неверный resource")
        access = secret()
        next_refresh = secret()
        now_sec = _now()
        rotated = self.store.rotate_refresh(
            hash_token(refresh),
            {
                "access_hash": hash_token(access),
                "refresh_hash": hash_token(next_refresh),
                "now_sec": now_sec,
                "access_ttl": self.access_token_ttl,
                "refresh_ttl": self.refresh_token_ttl,
            },
        )
        if not rotated.get("ok"):
            reason = (
                "Refresh token уже использован; подключитесь заново"
                if rotated.get("reason") == "replay"
                else "Refresh token недействителен"
            )
            return self._token_error(400, "invalid_grant", reason)
        if rotated["row"]["client_id"] != self.client_id:
            return self._token_error(400, "invalid_grant", "Refresh token недействителен")
        return JSONResponse(
            {
                "access_token": access,
                "refresh_token": next_refresh,
                "token_type": "Bearer",
                "expires_in": self.access_token_ttl,
                "scope": rotated["row"]["scope"],
            }
        )

    def _issue_tokens(self, client_id: str, scope: str, family: str | None = None) -> dict[str, Any]:
        access = secret()
        refresh = secret()
        now_sec = _now()
        family = family or secret()
        self.store.save_tokens(
            access_hash=hash_token(access),
            refresh_hash=hash_token(refresh),
            client_id=client_id,
            scope=scope,
            family=family,
            now_sec=now_sec,
            access_ttl=self.access_token_ttl,
            refresh_ttl=self.refresh_token_ttl,
        )
        body: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.access_token_ttl,
            "scope": scope,
        }
        if "offline_access" in scope.split(" "):
            body["refresh_token"] = refresh
        return body

    def _validate_authorize_params(self, params: dict[str, str]) -> dict[str, Any]:
        if not self.redirect_uris:
            return {
                "http_error": True,
                "message": "Задайте OAUTH_REDIRECT_URI в .env (callback URL из ChatGPT) и перезапустите сервис.",
            }
        if not params["client_id"] or not safe_equal(params["client_id"], self.client_id):
            return {"http_error": True, "message": "Неизвестный OAuth client_id."}
        if not self.allowed_redirect(params["redirect_uri"]):
            return {"http_error": True, "message": "redirect_uri не входит в список разрешённых адресов."}
        if params["response_type"] != "code":
            return {"ok": False, "oauth_error": "unsupported_response_type", "params": params}
        if params["code_challenge_method"] != PKCE_METHOD:
            return {"ok": False, "oauth_error": "invalid_request", "params": params}
        if not PKCE_RE.fullmatch(params["code_challenge"]):
            return {"ok": False, "oauth_error": "invalid_request", "params": params}
        if not self.check_resource(params["resource"]):
            return {"ok": False, "oauth_error": "invalid_target", "params": params}
        if parse_scopes(params["scope"]) is None:
            return {"ok": False, "oauth_error": "invalid_scope", "params": params}
        return {"ok": True, "params": params}

    def _query_params(self, request: Request) -> dict[str, str]:
        src = request.query_params
        return {
            "client_id": str(src.get("client_id") or ""),
            "redirect_uri": str(src.get("redirect_uri") or ""),
            "response_type": str(src.get("response_type") or ""),
            "state": "" if src.get("state") is None else str(src.get("state")),
            "scope": str(src.get("scope") or ""),
            "code_challenge": str(src.get("code_challenge") or ""),
            "code_challenge_method": str(src.get("code_challenge_method") or ""),
            "resource": str(src.get("resource") or ""),
        }

    async def _read_form(self, request: Request) -> dict[str, str]:
        content_type = request.headers.get("content-type") or ""
        if "application/json" in content_type:
            payload = await request.json()
            return {str(key): "" if value is None else str(value) for key, value in dict(payload).items()}
        form = await request.form()
        return {str(key): "" if value is None else str(value) for key, value in form.items()}

    def _prune_pending(self) -> None:
        t = _now()
        expired = [key for key, item in self.pending.items() if item["expires_at"] < t]
        for key in expired:
            self.pending.pop(key, None)

    def _login_locked(self) -> bool:
        cutoff = _now() - LOGIN_WINDOW_SEC
        self.failures = [item for item in self.failures if item > cutoff]
        return len(self.failures) >= LOGIN_MAX_FAILURES

    def _record_failure(self) -> None:
        self.failures.append(_now())

    def _login_form(self, request_id: str, error: str, csrf: str) -> HTMLResponse:
        error_html = f"<p class=\"error\">{html.escape(error)}</p>" if error else ""
        nonce = secret()
        body = (
            "<small>AMOCRM ROP MCP</small><h1>Авторизация amoCRM MCP</h1>"
            "<p class=\"note\">Это доступ ChatGPT к MCP. Токен amoCRM сюда вводить не нужно.</p>"
            f"{error_html}"
            "<form id=\"oauth-form\" method=\"post\" action=\"/authorize\">"
            f"<input type=\"hidden\" name=\"requestId\" value=\"{html.escape(request_id)}\">"
            f"<input type=\"hidden\" name=\"csrf\" value=\"{html.escape(csrf)}\">"
            "<label>Логин<input name=\"login\" required autocomplete=\"username\" maxlength=\"128\"></label>"
            "<label>Пароль<input type=\"password\" name=\"password\" required autocomplete=\"current-password\" maxlength=\"256\"></label>"
            "<button id=\"oauth-submit\" type=\"submit\">Войти и разрешить доступ</button></form>"
            f"<script nonce=\"{nonce}\">{LOGIN_SCRIPT}</script>"
        )
        return self._html(401 if error else 200, "Авторизация amoCRM MCP", body, nonce)

    def _html(self, status: int, title: str, body: str, script_nonce: str | None = None) -> HTMLResponse:
        nonce = script_nonce or secret()
        response = HTMLResponse(_html_page(title, body), status_code=status)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"script-src 'nonce-{nonce}'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    def _redirect_error(self, params: dict[str, str], error: str) -> Response:
        target = self._callback_url(params["redirect_uri"], {"error": error, "state": params.get("state") or "", "iss": self.issuer})
        response = RedirectResponse(target, status_code=302)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _callback_url(self, redirect_uri: str, extra: dict[str, str]) -> str:
        parts = urlsplit(redirect_uri)
        query = []
        if parts.query:
            query.append(parts.query)
        payload = {key: value for key, value in extra.items() if value != ""}
        if payload:
            query.append(urlencode(payload))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query), parts.fragment))

    def _token_error(self, status: int, error: str, description: str) -> JSONResponse:
        return JSONResponse({"error": error, "error_description": description}, status_code=status)

    def _cors(self, response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Cache-Control"] = "no-store"
        return response
