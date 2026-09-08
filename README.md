# amoCRM ROP MCP Server

Production MCP-сервер для руководителя отдела продаж. ChatGPT и другие MCP-клиенты получают **бизнес-инструменты** поверх amoCRM API v4: сводка по отделу, зависшие сделки, просроченные задачи, сравнение менеджеров, история сделки и безопасные write-операции.

Версия: **1.1.0** (`version.py`).

## Что это

Это не сырая обёртка `get_leads` / `patch_lead`.

Пользователь спрашивает: «Какие сделки зависли больше чем на 2 дня?»  
Модель вызывает `find_stale_leads`, а не собирает HTTP-запрос amoCRM вручную.

```text
ChatGPT / MCP Client
        ↓ HTTPS + OAuth 2.0 (PKCE)
MCP Streamable HTTP  (/mcp)
        ↓
amoCRM ROP MCP
        ↓
Business / Analytics Layer
        ↓
amoCRM API Client
        ↓
amoCRM API v4
```

Архитектура специально разделена, чтобы позже добавить webhooks → PostgreSQL → analytics, не ломая MCP-инструменты.

## Возможности (MCP tools)

**Чтение**

| Tool | Назначение |
| --- | --- |
| `sales_overview` | Сводка по отделу за период (по умолчанию — текущий день) |
| `pipeline_summary` | Снимок воронки по этапам: количество и сумма |
| `manager_performance` | Результаты и текущая нагрузка одного менеджера |
| `compare_managers` | Сопоставление 2+ менеджеров без рейтинга «хороший/плохой» |
| `find_stale_leads` | Активные сделки без значимой активности N дней |
| `find_overdue_tasks` | Невыполненные задачи с `complete_till` в прошлом |
| `find_leads_without_tasks` | Активные сделки без следующей задачи |
| `lead_history` | Хронология: события, примечания, задачи |
| `search_leads` | Поиск сделки перед действием |

**Запись (только эти три)**

| Tool | Назначение |
| --- | --- |
| `create_task` | Поставить задачу |
| `add_note` | Текстовое примечание к сделке |
| `move_lead` | Сменить этап после проверки, что статус принадлежит воронке |

Нет `delete_*`, `raw_api_request` и произвольного PATCH.

Примеры формулировок: [USAGE_EXAMPLES.md](USAGE_EXAMPLES.md). Безопасность: [SECURITY.md](SECURITY.md).

## Требования

- Python 3.11+ (предпочтительно 3.12)
- Приватная интеграция amoCRM (OAuth, не API-ключ)
- Для production: Ubuntu VPS + HTTPS reverse proxy

## Создание интеграции amoCRM

1. Войдите в аккаунт amoCRM.
2. Откройте **amoМаркет** → **Интеграции** → создайте **приватную интеграцию**.
3. Сохраните **Integration ID** (`AMOCRM_CLIENT_ID`) и **Secret Key** (`AMOCRM_CLIENT_SECRET`).
4. Укажите **Redirect URI** (`AMOCRM_REDIRECT_URI`). Для первой настройки допустим `https://localhost`.
5. На странице интеграции получите **Authorization Code**. Код **короткоживущий**: он нужен только один раз, чтобы обменять его на access + refresh token.
6. Выдайте интеграции права на сделки, задачи, примечания, события и пользователей.

Документация amoCRM:

- [OAuth, шаг за шагом](https://www.amocrm.ru/developers/content/oauth/step-by-step)
- [API v4, сделки](https://www.amocrm.ru/developers/content/crm_platform/leads-api)
- [События и примечания](https://www.amocrm.ru/developers/content/crm_platform/events-and-notes)

MCP:

- [Model Context Protocol](https://modelcontextprotocol.io)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io)

## Локальная настройка (Windows / любой Python 3.11+)

```powershell
cd amocrm-rop-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
```

Заполните в `.env`:

- `AMOCRM_SUBDOMAIN`
- `AMOCRM_CLIENT_ID`
- `AMOCRM_CLIENT_SECRET`
- `AMOCRM_REDIRECT_URI`

Затем:

```powershell
python scripts/oauth_setup.py
python scripts/check_connection.py
python server.py
```

Проверка:

```powershell
curl http://127.0.0.1:8000/health
```

MCP endpoint:

```text
http://127.0.0.1:8000/mcp
```

Readiness (конфиг и наличие токенов, без запроса в amoCRM на каждый health):

```text
GET http://127.0.0.1:8000/ready
```

Тесты (без реального amoCRM, API замокан):

```powershell
pytest
```

## Две разные авторизации

`MCP Client → MCP Server` и `MCP Server → amoCRM` — независимые контуры.

- amoCRM OAuth хранится в `data/tokens.json` (файл в `.gitignore`). Это доступ сервера к amoCRM. В ChatGPT его вводить не нужно.
- Входящий доступ ChatGPT → `/mcp` — OAuth 2.0 Authorization Code + PKCE, как в WB MCP: статический `OAUTH_CLIENT_ID`, страница логина `/authorize`, одноразовый code, refresh rotation.
- Логин и пароль MCP (`MCP_LOGIN` / `MCP_PASSWORD_HASH`) — отдельные учётные данные. Это не токен amoCRM.
- Для local-теста на `127.0.0.1` ChatGPT OAuth можно не включать.
- Публичный `/mcp` без OAuth оставлять нельзя.

Подключение ChatGPT:

1. Включите режим разработчика для собственных MCP.
2. URL: `https://ВАШ-АДРЕС/mcp`. Не `/sse`, не `/health`, не URL amoCRM.
3. OAuth → пользовательский клиент. Client ID: `chatgpt-amocrm-mcp`. Client Secret пустой. Token endpoint auth: `none`. Scopes: `mcp offline_access`.
4. Callback URL из ChatGPT скопируйте в `OAUTH_REDIRECT_URI` и перезапустите сервис.
5. На странице «Авторизация amoCRM MCP» введите логин и пароль, которые показал `scripts/setup_mcp_oauth.py`.

Сервер по умолчанию слушает только `127.0.0.1:8000`. Не публикуйте сырой HTTP :8000 в интернет.

## GitHub / clone на сервер

Репозиторий: https://github.com/zimuspro156-lab/amocrm-rop-mcp

```bash
# SSH deploy key (рекомендуется) или GitHub CLI. Пароль GitHub на сервере не хранить.
sudo mkdir -p /opt
sudo git clone git@github.com:zimuspro156-lab/amocrm-rop-mcp.git /opt/amocrm-rop-mcp
cd /opt/amocrm-rop-mcp
sudo chmod +x install.sh update.sh
sudo ./install.sh
```

Затем OAuth и проверка:

```bash
sudo -u amocrm-mcp /opt/amocrm-rop-mcp/.venv/bin/python /opt/amocrm-rop-mcp/scripts/oauth_setup.py
sudo -u amocrm-mcp /opt/amocrm-rop-mcp/.venv/bin/python /opt/amocrm-rop-mcp/scripts/check_connection.py
sudo systemctl restart amocrm-rop-mcp
sudo systemctl status amocrm-rop-mcp
curl http://127.0.0.1:8000/health
```

`install.sh` создаёт пользователя `amocrm-mcp`, venv, systemd unit `amocrm-rop-mcp` и **не открывает порт 8000 наружу**.

## HTTPS перед MCP

Нужен публичный URL вида `https://mcp.example.com/mcp`. В `.env` укажите `MCP_PUBLIC_URL=https://mcp.example.com` **без `/mcp`**. Tunnel/прокси должен пропускать весь origin: `/mcp`, `/authorize`, `/token`, `/.well-known/...`. Варианты (любой один):

- **Caddy**
- **Nginx**
- **Traefik**
- **Cloudflare Tunnel**

Пример Caddy:

```caddy
mcp.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Для browser-клиентов reverse proxy не должен буферизовать SSE (`proxy_buffering off` в Nginx).

Если `Host` не localhost, задайте `MCP_ALLOWED_HOSTS=mcp.example.com,mcp.example.com:*` либо `MCP_DISABLE_DNS_REBINDING_PROTECTION=true` **только** когда proxy уже контролирует Host.

Временный туннель (ngrok / Cloudflare quick tunnel) — **только development**.

## Обновление

Локально:

```bash
git add .
git commit -m "..."
git push
```

На сервере:

```bash
cd /opt/amocrm-rop-mcp
sudo ./update.sh
```

`update.sh` делает `git pull --ff-only`, ставит зависимости, гоняет `pytest` и **не перезапускает production**, если тесты упали.

## Логи systemd

```bash
sudo systemctl status amocrm-rop-mcp
sudo journalctl -u amocrm-rop-mcp -f
sudo journalctl -u amocrm-rop-mcp -n 100 --no-pager
```

## Troubleshooting

| Симптом | Что проверить |
| --- | --- |
| 401 amoCRM | Токены протухли или Authorization Code уже использован. Повторите `oauth_setup.py`. |
| token refresh error | Неверный `client_secret` / `redirect_uri`, либо refresh token отозван. |
| 403 | У интеграции нет прав на пользователей, события или сделки. Tool вернёт `insufficient_permissions`, сервер не падает. |
| 429 | Превышен лимит amoCRM. Клиент делает ограниченный retry с backoff. |
| MCP server unavailable | `systemctl status`, `journalctl`, `curl /health`. |
| systemd error | Путь venv, права `amocrm-mcp` на `/opt/amocrm-rop-mcp`, синтаксис `.env`. |
| wrong pipeline | Задайте `DEFAULT_PIPELINE_ID` или передайте `pipeline_id` в tool. Сначала `pipeline_summary`. |
| ChatGPT пишет, что redirect_uri неверный | Скопируйте точный Callback URL в `OAUTH_REDIRECT_URI` и перезапустите сервис |
| ChatGPT не коннектится | Нужен HTTPS, не raw :8000. Проверьте OAuth metadata, `MCP_PUBLIC_URL` без `/mcp`, логин MCP |
| HTTP 421 Invalid Host | Заполните `MCP_ALLOWED_HOSTS` или отключите DNS-rebinding за доверенным proxy. |

## Разработка

```text
server.py            MCP Streamable HTTP + /health + /ready + OAuth routes
oauth_server.py      ChatGPT → MCP OAuth 2.0 Authorization Code + PKCE
oauth_store.py       SQLite codes/tokens for MCP OAuth
services.py          бизнес-оркестрация
analytics.py         чистые расчёты без HTTP
amocrm_client.py     amoCRM API v4
token_manager.py     OAuth + file storage (заменяется на Vault/Postgres)
tools/               MCP tools
```
