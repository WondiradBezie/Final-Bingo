# JOY BINGO Architecture

`free_deploy.py` is the single application entry point.

- `free_deploy.py` — Telegram bot + FastAPI API + WebSocket gateway + admin API.
- `game_service.py` — authoritative Bingo game state, fixed 400-card catalog, number sequence, winner detection, and payout orchestration.
- `database.py` — PostgreSQL-only persistence and atomic wallet operations.
- `cards.json` — exactly 400 validated Bingo cards.
- `webapp/` — player lobby/game UI and admin UI.

The old JSON wallet, duplicate game engines, SQLAlchemy model set, and alternate server entry point were removed because they created multiple sources of truth and could cause money/state inconsistencies.

## Money flow

1. Deposit request is recorded as `pending`.
2. Admin verifies the external payment.
3. Approval credits the wallet once using a unique transaction reference.
4. Joining a game atomically reserves the card price.
5. A winner payout is credited once using a unique `win:<game>:<user>` reference.
6. Withdrawal requests reserve funds immediately and remain `pending` until admin approval. Rejection refunds the reservation.

No client/browser value is trusted for balance, prize, called numbers, or Bingo validity.
