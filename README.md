# JOY BINGO — Production Build

JOY BINGO is a Telegram Bingo platform with a FastAPI/WebSocket web app, PostgreSQL wallet, admin API, and 400 fixed Bingo cards.

## Important

This build is designed for testing and controlled deployment. Before accepting real-money play, verify all applicable Ethiopian laws, licensing requirements, payment-provider rules, tax obligations, and responsible-gaming requirements.

## Main components

- `free_deploy.py` — application entry point (Telegram webhook + FastAPI + WebSocket + admin API)
- `game_service.py` — authoritative Bingo engine
- `database.py` — PostgreSQL persistence and atomic wallet operations
- `cards.json` — exactly 400 validated cards
- `webapp/` — player and admin interfaces
- `Dockerfile` / `docker-compose.yaml` — local container deployment
- `koyeb.yaml` — Koyeb starting configuration
- `.env.example` — required environment variables
- `RUNBOOK.txt` — complete installation, deployment, testing, and troubleshooting guide
- `ARCHITECTURE.md` — technical architecture and money flow

## Quick local start

1. Copy `.env.example` to `.env` and replace every placeholder.
2. Start PostgreSQL and Redis, or use `docker compose up -d postgres redis`.
3. Install Python 3.11+ dependencies:

```bash
pip install -r requirements-free.txt
```

4. Start the application:

```bash
uvicorn free_deploy:app --host 0.0.0.0 --port 8000
```

5. Open `http://localhost:8000/webapp/lobby.html` for the web app.

For the full setup and deployment procedure, read `RUNBOOK.txt`.
