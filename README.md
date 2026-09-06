# Personal CFO

A local-first personal financial dashboard for a household.
All financial data stays on your Mac.

## Requirements

- Python 3.10+
- Node.js 18+
- npm

## Setup & Run

```bash
chmod +x start.sh
./start.sh
```

Then open http://localhost:5173 in your browser.

## First-Time Setup (5 minutes)

1. Go to **Planning Inputs** and fill in your ages, SS estimates, pension, contributions
2. Go to **Accounts** and add all your accounts with current balances
3. Go to **Retirement** to review your projection
4. Go to **Education** to review each child's 529 status
5. Hit **Save Snapshot** on the Dashboard — do this monthly when you do bills

## Monthly Routine (10 minutes)

1. Update account balances in the **Accounts** tab
2. Check the **Retirement** projection — did anything change?
3. Check **Education** funding percentages
4. Save a snapshot with a note like "May 2026"

## Data Location

Your data is stored in `backend/cfo.db` — a local SQLite file.
Back it up by copying that file anywhere.

## Project Structure

```
personal-cfo/
  backend/
    main.py              FastAPI API server
    db.py                SQLite database setup
    projection_engine.py Retirement & education math
    requirements.txt
    cfo.db               Your data (created on first run)
  frontend/
    src/
      pages/
        Dashboard.jsx    Net worth + history
        Accounts.jsx     Account management
        Retirement.jsx   Retirement projections
        Education.jsx    529 planning
        Settings.jsx     Planning inputs
  start.sh               Run everything
```
