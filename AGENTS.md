<!-- Documentation header: concise operating rules for future Favlist contributors. -->
# Favlist contributor guide

## Project

Favlist is a single-user, self-hosted React/FastAPI/SQLite collection library for organizing item metadata and cover images.

## Run and verify

- Docker: copy `.env.example` to `.env`, set the two required secrets, then run `docker compose up --build`.
- API tests: `cd backend; pytest`. Web tests: `cd frontend; npm test`; production build: `npm run build`.
- Check deployment YAML with `docker compose config`. Never commit `.env`, generated data, or user cookies.

## Layout and conventions

- `backend/app/`: API, database, auth, metadata adapter, jobs, and covers; `backend/tests/`: backend tests.
- `frontend/src/`: React UI and tests; root `config.yaml`: ordered tag emphasis; `docker-compose.yml`: deployment.
- Every code file needs a concise documentation header; every function/method needs a useful docstring or JSDoc. Keep tests deterministic and mock external services.

## Security and current state

- Preserve authentication on all data endpoints. Secrets are environment variables; `COOKIE_SECURE=true` is mandatory for public HTTPS.
- Docker stores SQLite and covers in `favlist-data`; avoid schema or compose changes without updating README and tests.
