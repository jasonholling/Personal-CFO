# Personal CFO

Local-only personal financial dashboard for Jason & Justin. All data stays on the Mac; nothing leaves the machine except anonymized queries to the Claude API. Not deployed — runs locally.

**Repo:** https://github.com/jasonholling/Personal-CFO (private)
**Path:** ~/Projects/personal-CFO

## Stack
- **Backend:** Python 3.10+, FastAPI 0.115 + Uvicorn, Pydantic 2.7, ReportLab (PDF reports), SQLite. Runs on 127.0.0.1:8000.
- **Frontend:** React 18 + Vite 8, Recharts (charts), Axios. Dev server on :5173. Fonts (DM Sans / DM Serif Display) are self-hosted in `public/fonts/` — not loaded from Google Fonts, to keep the "nothing leaves the Mac" promise (fixed 2026-09-05).
- **DB:** SQLite at `backend/cfo.db`, created on first run by `db.py` (`init_db()`, `init_tasks_table()`). Tables: accounts, planning_inputs, snapshots, tasks.

## Run
```bash
./start.sh   # boots backend (venv) + frontend, opens http://localhost:5173
```
Note: `start.sh` currently hardcodes `~/Desktop/personal-cfo/...` paths — repo lives at `~/Projects/personal-CFO`, so paths may need updating on a fresh setup.

## Backend modules
- `main.py` — FastAPI app / routes
- `projection_engine.py` — net worth / retirement projections
- `simulation_engine.py` — Monte Carlo / scenario simulation
- `report_generator.py` — PDF report generation (ReportLab)
- `quicken_importer.py` — Quicken data import
- `task_engine.py` — task/checklist logic

## Frontend pages (frontend/src/pages)
Dashboard, NetWorth, Accounts, Retirement, RetirementSensitivity, RothConversion,
TaxPlanning, Simulation, WhatIf, SideBySide, Risk, Insurance, Estate, Education, Kids, Report, Settings

## Gotchas
- **`cfo.db` is gitignored** — it holds real financial data and stays local only. A fresh clone starts with an empty DB (created on first run). Never commit it.
- Accidental `* 2` / `* 3` Finder/iCloud duplicate files are gitignored — don't commit them.
- No API keys are committed; keep it that way (use env vars / .env, which is gitignored).
- **All real personal data belongs in `cfo.db` only, never hardcoded in source** — this app previously had real salary/mortgage/SS-benefit/insurance figures and family names baked into `db.py`/`main.py`/`projection_engine.py`/`simulation_engine.py`/frontend pages as fallback defaults and UI labels (fixed 2026-08-26). Person/child names are now configurable via Settings (`person1_name` etc., see `usePersonNames()` hook) instead of hardcoded "Jason"/"Justin"/"Abby"/"Cooper".

## Tests (`backend/tests/`)
```bash
cd backend
pip install -r requirements-dev.txt
pytest -v
```
Covers every backend module: `db.py` (schema/migrations), `task_engine.py` (auto-task rules), `quicken_importer.py` (CSV parsing), `projection_engine.py` + `simulation_engine.py` (the calc engines — parametrized across every retirement age 55–67, not just the three Settings anchor points, since that's exactly where the 2026-08-26 calc-consistency bugs lived: KeyErrors and silently-wrong data for "in-between" ages), and `main.py` (every FastAPI route, via `TestClient`). `report_generator.py` (PDF generation) gets covered incidentally through the `main.py` report test rather than a dedicated file.

**Coverage is enforced at 95%** via `--cov-fail-under=95` in `pytest.ini` (currently ~97%) — a commit/PR that drops coverage below that fails CI. `.coveragerc` excludes `venv/`, `tests/`, and site-packages from the count.

**Critical safety mechanism** — `db.py` touches its sqlite file *at import time* (`init_tasks_table()` runs unconditionally at module load; `main.py` calls `init_db()` at import time too), and both default to your real `cfo.db`. `tests/conftest.py` sets a `CFO_DB_PATH` env var override *before anything else*, so `db`/`main` are never imported against the real path — plus an autouse `_guard_never_touch_real_db` fixture that hard-fails if `db.DB_PATH` ever equals the real path. If you add a new test file, don't import `db` or `main` anywhere except through this mechanism (i.e. via the `client`/`temp_db` fixtures) — verify with `stat`/`md5` on `cfo.db` before and after a full run if you're ever unsure.

`tests/conftest.py` provides synthetic fixture data — never copy real figures from `cfo.db` into a test. `test_no_sensitive_data.py` runs the CI sensitive-data check locally too. Runs in CI on every push/PR (see below).

## CI (`.github/workflows/ci.yml`)
Runs on every push/PR, two jobs:
- **secrets** — **gitleaks** (generic API-key/token/credential scanning) plus `scripts/check_sensitive_data.py`, a project-specific regression test that fails the build if any of the real numbers/names from the 2026-08-26 incident (or `cfo.db`/`.env`/Finder-duplicate files) ever reappear in tracked source. If you find a *new* instance of real personal data hardcoded in source, add it to `SENSITIVE_STRINGS` in that script rather than assuming the existing list covers it — it's a denylist of known past leaks, not a general secret scanner.
- **backend-tests** — runs the pytest suite above.
