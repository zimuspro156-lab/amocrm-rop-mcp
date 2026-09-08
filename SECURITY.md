# Security

## Что защищать

Есть **два независимых контура**:

1. MCP Client → MCP Server (`/mcp`)
2. MCP Server → amoCRM API (OAuth 2.0)

Токены amoCRM **не** аутентифицируют ChatGPT. Открытый `/mcp` даёт доступ ко всем read/write tools.

## Секреты

Никогда не коммитьте:

- `.env`
- `data/tokens.json`
- `data/oauth.sqlite`
- access token, refresh token, client secret, authorization code
- MCP login password / `MCP_PASSWORD_HASH`
- production logs с заголовком `Authorization`

`.gitignore` уже исключает `.env`, `tokens.json` и `oauth.sqlite`. Перед `git push` проверяйте `git status`.

Хранение MVP:

- секреты интеграции — в `.env` с правами `640`, владелец сервиса `amocrm-mcp`
- runtime-пара токенов amoCRM — в `data/tokens.json` с правами `600`
- токены ChatGPT → MCP — в `data/oauth.sqlite` с правами `600`
- `TokenStorage` абстрагирован: позже можно заменить файл на PostgreSQL / Redis / Vault / Secrets Manager

Не логируются: `Authorization`, Bearer token, refresh token, `client_secret`.

## Почему нельзя коммитить `.env`

`.env` содержит `AMOCRM_CLIENT_SECRET` и может содержать bootstrap-токены. Утечка секрета интеграции позволяет выпустить новые токены, пока секрет не ротирован.

## Ротация amoCRM secret

1. В amoМаркете сгенерируйте новый Secret Key.
2. Обновите `AMOCRM_CLIENT_SECRET` в `.env`.
3. Получите новый Authorization Code и выполните `python scripts/oauth_setup.py`.
4. Перезапустите сервис: `sudo systemctl restart amocrm-rop-mcp`.
5. Старый secret считайте скомпрометированным.

## Замена токенов

Refresh token amoCRM одноразовый: после refresh нужно сохранить **новую пару**. Это делает `token_manager.py`.

Если refresh сломан:

```bash
python scripts/oauth_setup.py
sudo systemctl restart amocrm-rop-mcp
```

Не вставляйте токены в чат, тикеты и CI variables без секретов GitHub.

## Утечка

1. Сразу ротируйте Secret Key интеграции в amoCRM.
2. Перевыпустите токены через `oauth_setup.py`.
3. Если был включён ChatGPT OAuth — очистите `data/oauth.sqlite`, заново сгенерируйте пароль MCP (`MCP_PASSWORD_HASH`) и переподключите ChatGPT.
4. Проверьте Git history: `git log -p` / `git grep`. Если секрет попал в git — ротация обязательна, одного `.gitignore` недостаточно.
5. Ограничьте права интеграции минимально необходимым набором.

## Защита MCP endpoint

Локально сервер слушает `127.0.0.1:8000`. Это нормально для теста.

Публичный URL обязан быть HTTPS. Не публикуйте `:8000` в интернет. Reverse proxy / Tunnel должен отдавать весь origin, не только `/mcp`: ChatGPT ходит на `/authorize`, `/token` и `/.well-known/oauth-authorization-server`.

Входящий доступ ChatGPT → MCP — OAuth 2.0 Authorization Code + PKCE S256 (как в WB MCP). Статический `MCP_AUTH_TOKEN` для ChatGPT не используется.

```env
MCP_PUBLIC_URL=https://mcp.example.com
OAUTH_ISSUER=https://mcp.example.com
OAUTH_CLIENT_ID=chatgpt-amocrm-mcp
OAUTH_REDIRECT_URI=<точный Callback URL из ChatGPT>
MCP_LOGIN=admin
MCP_PASSWORD_HASH=<bcrypt, генерирует scripts/setup_mcp_oauth.py>
```

`/health` и `/ready` намеренно без MCP auth (liveness probe). Не кладите туда секреты.

Токены подключений ChatGPT хранятся в `data/oauth.sqlite` (права `600`). Это не токены amoCRM.

## Write tools

Разрешены только `create_task`, `move_lead`, `add_note`. Нет удаления сущностей и произвольного API. `move_lead` отказывается писать статус, который не принадлежит целевой воронке.
