# Personal CFO

A local-first personal financial dashboard for a household.
All financial data stays on your Mac — nothing is uploaded, and there's
no hosted version of this app.

## Requirements

- macOS (uses Apple's bundled Python 3.9 by default — see the note below)
- Python 3.10+ available as `python3` (or let `start.sh` fall back to
  `/usr/bin/python3`)
- Node.js 18+
- npm

## Getting Started From a Fresh Clone (Fork Setup)

```bash
git clone <your-fork-url> personal-cfo
cd personal-cfo
chmod +x start.sh
./start.sh
```

`start.sh` handles everything on first run: it creates a Python virtual
environment in `backend/venv`, installs backend and frontend dependencies,
starts both servers, and opens http://localhost:5173. A fresh clone has no
data in it — `backend/cfo.db` is created empty on first run and is
gitignored, so your data never gets committed or pushed.

**Python version note:** `pydantic==2.7.0` (pinned in `requirements.txt`)
has no prebuilt wheel for very new Python versions and fails to build from
source without a Rust toolchain. `start.sh` prefers `/usr/bin/python3`
(Apple's bundled Python 3.9) over whatever a newer Homebrew `python3`
resolves to, for exactly this reason. If setup fails on dependency builds,
try pointing at an older Python 3.10–3.12 explicitly.

### First launch: the lock screen

The first time you open the app, you'll be asked to either set a
passphrase or explicitly skip the lock. This is a local-only deterrent
(so a household member glancing at your screen — or a screen share —
doesn't casually load your finances), **not** internet-facing security;
the backend only ever binds to `127.0.0.1`. Skipping is a valid, expected
choice for a single-user machine. See `backend/auth.py`'s module docstring
for the full design if you want the details.

## First-Time Data Entry (5 minutes)

1. Go to **Planning Inputs / Settings** and fill in ages, Social Security
   estimates, pension, contributions, and healthcare cost assumptions.
2. Go to **Accounts** and add every account with its current balance
   (account_type must be one of the values in the dropdown — the backend
   now rejects anything else, so an account can't silently disappear from
   net worth or the retirement projection due to a typo).
3. Go to **Monthly Cash Flow** and add recurring take-home income and
   core spending — several other pages (Emergency Fund, Goals & Funding)
   use this once it exists, instead of a single manually-typed expense
   estimate.
4. Go to **Retirement Projection** and **Stress Test & What-If** to review
   your plan under different ages, market conditions, and assumptions.
5. Go to **Education** to review each child's 529 status.
6. Hit **Save Snapshot** on the Dashboard (**Net Worth** page) — do this
   monthly when you do bills, to build a real net-worth trend line.

## Monthly Routine (10 minutes)

1. Update account balances in the **Accounts** tab (or re-import a fresh
   Quicken Net Worth Summary CSV via the importer on that page).
2. Check the **Retirement Projection** and **Stress Test & What-If**
   pages — did anything change?
3. Check **Education** funding percentages and **Debt Payoff** progress.
4. Save a snapshot with a note like "May 2026".

## Running Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest -v
```

Coverage is enforced at 95% (`pytest.ini`'s `--cov-fail-under=95`). Tests
run against an isolated temp database (`tests/conftest.py`) — they never
touch your real `backend/cfo.db`.

```bash
cd frontend
npm test        # Vitest
npm run build   # production build check
```

CI (`.github/workflows/ci.yml`) runs both suites plus a secrets/
sensitive-data scan on every push and PR.

## Data Location

Your data is stored in `backend/cfo.db` — a local SQLite file, gitignored.
Back it up by copying that file anywhere. It's created empty on first run
by `db.py`; there is no seed data.

## Project Structure

```
personal-cfo/
  backend/
    main.py                     FastAPI app / all API routes
    db.py                       SQLite schema + migrations
    auth.py                     Local passphrase/Touch ID lock (optional)
    projection_engine.py        Retirement & education projections
    simulation_engine.py        Monte Carlo, stress tests, SWR, Roth conversion
    net_worth_engine.py         Shared net-worth bucketing + emergency fund
    cash_flow_engine.py         Monthly income/expense summary
    debt_engine.py               Avalanche/snowball debt payoff planning
    allocation_engine.py        Portfolio allocation & fee analysis
    rental_engine.py            Rental property cash flow
    estate_engine.py            Estate document tracking
    life_event_engine.py        Life-event classification helpers
    retirement_tools_engine.py  RMD planning, Roth conversion tools
    task_engine.py              Auto-generated planning task checklist
    cfo_briefing_engine.py      Dashboard's prioritized "next moves" agenda
    confidence_engine.py        Plan confidence scoring
    report_generator.py         Annual PDF report (ReportLab)
    quicken_importer.py         Quicken Net Worth CSV import
    requirements.txt / requirements-dev.txt
    cfo.db                      Your data (created on first run, gitignored)
    tests/                      pytest suite (95% coverage floor)
  frontend/
    src/
      pages/                    Dashboard, Accounts, Net Worth, Debt Payoff,
                                 Monthly Cash Flow, Goals & Funding, Assign
                                 Surplus, Action Tracker, Retirement
                                 Projection, Stress Test & What-If, Saved
                                 Scenarios, Tax Planning, Retirement Tools,
                                 Education, Kids, Insurance, Risk
                                 Management, Estate Planning, Settings,
                                 Annual Report, and more
      components/               Shared UI (Quicken import widget, task panel)
      hooks/                    Shared React hooks (scenario state, person names)
  start.sh                      Run everything
```
