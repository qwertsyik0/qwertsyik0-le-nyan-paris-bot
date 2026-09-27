# Le Nyan Paris Bot

Отдельный бот и Mini App для проекта Le Nyan Paris.

## Что уже есть в MVP

- Telegram bot на webhook.
- FastAPI backend.
- Mini App для подачи анкеты.
- PostgreSQL таблицы `paris_users` и `paris_applications`.
- Админские кнопки в боте: принять, отправить на правки, отклонить.
- Принятие с назначением роли.
- Уведомление игроку после решения.

## Логика проекта

- Чат Telegram остается местом всей ролевой игры.
- Бот нужен для анкет, уведомлений, личных писем, поручений и админки.
- Mini App нужен для анкеты и будущего кабинета игрока.
- Публичный сайт Le Nyan Paris остается отдельным городским листом.

## Переменные окружения

Смотри `.env.example`.

Главные переменные:

```env
BOT_TOKEN=
DATABASE_URL=
PUBLIC_BASE_URL=
ADMIN_IDS=8587776155
WEBHOOK_SECRET=
```

`BOT_TOKEN` нельзя коммитить в репозиторий.

## Запуск локально

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn server.main:app --reload
```

Для полноценной работы webhook нужен публичный HTTPS URL, например Render.

## Render

Start command:

```bash
uvicorn server.main:app --host 0.0.0.0 --port $PORT
```

После деплоя поставь:

```env
PUBLIC_BASE_URL=https://<render-service>.onrender.com
MINI_APP_URL=https://<render-service>.onrender.com/miniapp/
```
