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
- access token, refresh token, client secret, authorization code
- production logs с заголовком `Authorization`

`.gitignore` уже исключает `.env` и `tokens.json`. Перед `git push` проверяйте `git status`.

Хранение MVP:

- секреты интеграции — в `.env` с правами `640`, владелец сервиса `amocrm-mcp`
- runtime-пара токенов — в `data/tokens.json` с правами `600`
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
3. Если был включён `MCP_AUTH_TOKEN` — замените его и все копии у клиентов.
4. Проверьте Git history: `git log -p` / `git grep`. Если секрет попал в git — ротация обязательна, одного `.gitignore` недостаточно.
5. Ограничьте права интеграции минимально необходимым набором.

## Защита MCP endpoint

Локально сервер слушает `127.0.0.1:8000`. Это нормально для теста.

Публичный URL обязан быть HTTPS. Не публикуйте `:8000` в интернет.

Официальный механизм MCP для Streamable HTTP — OAuth 2.1 resource server (`TokenVerifier` + `AuthSettings`, RFC 9728 metadata). Включите:

```env
MCP_AUTH_TOKEN=<длинный случайный секрет>
MCP_AUTH_ISSUER_URL=https://auth.example.com
MCP_PUBLIC_URL=https://mcp.example.com/mcp
```

Либо поставьте проверку `Authorization` на Caddy/Nginx/Traefik и не оставляйте MCP анонимным.

`/health` и `/ready` намеренно без MCP auth (liveness probe). Не кладите туда секреты.

## Write tools

Разрешены только `create_task`, `move_lead`, `add_note`. Нет удаления сущностей и произвольного API. `move_lead` отказывается писать статус, который не принадлежит целевой воронке.
