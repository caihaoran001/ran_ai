# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

燃AI 1.0 — an internal community-operations system. Each WeChat-style group maps to one real user's 28-day服务 (service) window. The system handles service cases, an H5 questionnaire, LLM-generated user profiles and delivery documents, human review, check-in records, the **QiWe** experimental messaging channel, and a basic dashboard.

Stack: **FastAPI + SQLAlchemy 2.0 (async) + Alembic + PostgreSQL** (backend), **Next.js 15 + React 19 + TypeScript + Tailwind** (frontend), **Docker Compose** runtime, OpenAI-compatible AI (default DeepSeek).

## Companion docs — read these too

This file is a quick start. Three detailed knowledge bases (in Chinese) hold the rules that matter; read the relevant one before changing code:

- `AGENTS.md` (root) — conventions, anti-patterns, debugging, deployment, and the engineering discipline rules.
- `backend/AGENTS.md` — backend structure + a "WHERE TO LOOK" table mapping tasks to files.
- `frontend/AGENTS.md` — frontend structure, API endpoints, conventions.
- `docs/testing/README.md` + `docs/testing/TEST_CASES.md` — **the only entry point** when the user says "测试 / test". See Testing below.

## Commands

### Run everything (Docker — primary local/LAN workflow)
```bash
cp .env.example .env          # then set AI_API_KEY, QIWEI_*, ADMIN_*, BACKEND_ADMIN_API_KEY
docker compose up -d --build
# Frontend http://localhost:3002  ·  Backend http://localhost:8000  ·  Postgres :54322
```
The backend container runs **without uvicorn reload** (SSE long-connections would block it). **After editing any file under `backend/src/`, you must `docker restart ran-ai-backend`** or changes won't take effect — this is the most commonly forgotten step.

### Backend (local, in `backend/`, uses `uv`)
```bash
uv sync
uv run alembic upgrade head
uv run ran-ai-api               # API server
uv run ran-ai-scheduler         # reminder/knowledge-evolution/outcome scheduler
uv run ran-ai-wecom-worker      # legacy WeCom worker (new channel is QiWe)
uv run pytest tests/ -v         # full test suite (Mock LLM; real-LLM E2E is isolated)
uv run pytest tests/test_02_chat.py -v          # single file
uv run pytest tests/test_02_chat.py::test_name  # single test
```

### Frontend (in `frontend/`, uses `npm`)
```bash
npm install
npm run dev          # next dev on 0.0.0.0
npm run build        # MUST build locally before deploy — never compile on ECS
npm run lint
npm run test:e2e     # Playwright
```

### Debugging (from `AGENTS.md`)
```bash
docker logs -f --tail 100 ran-ai-backend                 # live backend logs
cd backend && uv run python ../scratch/query_latest_messages.py   # latest 10 real chat rows
```

## Architecture — the big picture

### Messaging is layered (channel-agnostic core)
Message handling is split into three layers so business logic never touches a specific channel's API:
```
app/services/channels/     MessageChannel protocol, channel registry, QiWe adapter
app/services/ranren_ai/    RanrenAIEngine — channel-agnostic business engine
app/services/qiwe_service/ QiWe group-message aggregation
```
A QiWe callback turns inbound text into a `ChannelMessage`, aggregates consecutive messages from the same user/group over an ~8s window, then hands it to `RanrenAIEngine` (engine.py). The engine handles the intake-questionnaire command, knowledge retrieval, LLM reply, and message persistence. **To send messages, always go through the `channels` abstraction — never call a channel API directly.**

### Knowledge retrieval / RAG (two-tier with fallback)
Bot replies first query the DB index `knowledge_chunks` (via `knowledge_index.py`), then pass hits to the LLM. The index ingests structured FAQs (`knowledge_faqs`), approved knowledge drafts, and Markdown/TXT chunks, with optional embedding (cosine in app layer; pgvector migration is pre-staged but the default `postgres:15-alpine` image lacks it). If the DB index misses, it **falls back** to local Markdown/LlamaIndex keyword search under `backend/src/app/services/knowledge_base/`. Keyword fallback is limited to the business whitelist `keyword_whitelist.json` — do not query the DB with every jieba token.

### Documents & profiles
Questionnaire submission → LLM generates a user profile. Service-case detail can generate two delivery docs (处方单, 28天执行单) and stage reports (3/7/14/21/28-day + graduation). Flow: LLM emits **structured JSON** for key fields → backend templates (`document_templates.py`) render editable Markdown → persisted as `content_json`/`content_md` → only at download/send time does `docx_service.py` render a temporary `.docx` (deleted after). **The authoritative system prompts live in `backend/src/app/services/document_service.py`; `手动版提示词.md` is reference only and must not be used at runtime.**

### Outcome tracking
`outcome_engine.py` extracts structured outcome evidence per chat message into `outcome_events` (deduped by `(service_case_id, message_id, field_key)`), maintains per-milestone `outcome_stage_states`, aggregates `outcome_snapshots` at milestones, and `user_state_profile.py` projects events into a JSONB `user_state_profiles` (action_focus / need_analysis / body_feedback / risk_profile). AI replies cite the current action focus as implicit background.

### Risk control
MVP risk is identified by **LLM intent/info extraction**, not hardcoded keywords. High-risk users never auto-receive AI content (forced human review); only the preset high-risk safety script may send immediately. Content policy (`content_policy.py`): `manual_review` (default — AI content goes to human review) vs `auto_send` (low-risk auto-sends, high-risk still reviewed).

### Auth model
Backend admin `/api/*` requires header `X-Admin-API-Key` (`BACKEND_ADMIN_API_KEY`); `/health` and public questionnaire (token-based) are exceptions. The admin key lives **only** between the Next server and FastAPI — never expose it to `NEXT_PUBLIC_*` or browser code. The frontend proxies backend API same-origin.

## Hard rules (CRITICAL — see AGENTS.md for the full list)

- **All DB ops are async.** No `session.query()` / sync DB calls anywhere (concurrency).
- **Soft-delete only.** Set `deleted_at`; filter `deleted_at.is_(None)`. Never hard-delete.
- **Never hand-edit an already-generated/applied Alembic migration.**
- **No hardcoded business keywords for logic** — prefer LLM structured output; the few exceptions are risk-code parsing and document consistency that genuinely need a code backstop.
- **FAQ ↔ chunk sync:** approved/maintained FAQs must persist to `knowledge_faqs` and sync to `knowledge_chunks` — never just append to a Markdown file.
- **Prompts before code:** for business-rule changes, edit the system prompt and test first; only add code constraints when prompt tuning is exhausted. Never submit prompt + code changes in parallel for the same rule.
- **Frontend:** never hardcode API URLs (use env); never bypass the `api<T>()` client; the questionnaire page (`/q/*`) shows no admin sidebar.

## Testing protocol (when the user just says "测试" / "test")

This is strict — read `docs/testing/README.md` + `docs/testing/TEST_CASES.md` first; do not invent commands or use `docs/reviews/` history as the test-case source.
- **Default: test only, do not change code.** Don't modify business code, tests, fixtures, or expected results to make tests pass. Record evidence + fix suggestions and wait for explicit approval to fix.
- **Always write a new dated report** under `docs/reviews/` after a run. Never flip an old report's results to "通过".
- **Report failures honestly**, distinguishing 通过/失败/跳过/未执行/环境阻塞 with commands and key evidence.
- **Real tests need authorization.** Tests that call a real LLM (cost), send to a real group, or permanently persist data are forbidden by default. The real-LLM E2E (`tests/test_e2e_01_real_llm.py`, shown as `E2E-01`) runs only on explicit request. The一键 health self-test runs only the Mock + rolled-back checks `01-08` (frontend `/system-diagnostics`).

## Deployment notes

- **Code deploy vs config change — keep separate.** Deploy code with `scratch/local_prebuilt_deploy.py` (builds frontend locally → packages → uploads → rebuilds on ECS). It **only ships code; it does NOT touch the prod `.env` or `docker-compose.prod.yml`** (both excluded from the tar). To change prod config (secrets/DB/QiWe guid, or compose structure) run `scratch/configure_remote_files.py` separately (edit its inline template first). **Never re-add an `.env`/`docker-compose.prod.yml` overwrite step to the deploy script** — it would clobber server-side hand edits. See `docs/deployment.md` §1.5.
- **Build the frontend locally** (`npm run build`, standalone artifact) and upload — never compile on ECS (resource lock-up).
- Next.js standalone needs `frontend/.next/standalone/node_modules/`. In `.dockerignore` use prefix-anchored `/node_modules` (not bare `node_modules`); in `tar` exclude `frontend/node_modules` specifically (not a global `--exclude='node_modules'`).
- Alpine/slim prod images have no `curl` — health-check via the in-container runtime instead:
  - backend: `docker exec ran_ai-backend-1 python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`
  - frontend: `docker exec ran_ai-frontend-1 node -e "const http=require('http'); http.get('http://localhost:3000')"`
- Production: `docker compose -f docker-compose.prod.yml up -d --build`. Full guide: `docs/deployment.md`.
