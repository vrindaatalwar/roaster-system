# DataStraw Smart Roster

Stage 1 provides the configurable workforce and brand/process data foundation and fictional demo data. Stage 2 generates monthly draft rosters through the CP-SAT solver. Stage 3 independently validates assignments and provides manager-selected availability and coverage changes, backup recommendations, and targeted replanning.

## Stack

- React with TypeScript
- FastAPI API
- PostgreSQL with SQLAlchemy
- Google OR-Tools CP-SAT monthly allocator

## Run the backend and manager screen

Requires Python 3.12+, PostgreSQL, and `uv`.

1. Create a PostgreSQL database named `datastraw_roster`.
2. Set `DATABASE_URL` to a PostgreSQL connection string. The default uses your local OS username as the PostgreSQL role; if your PostgreSQL setup requires a password or uses a different role, set the matching URL explicitly. `.env.example` shows the format.
3. Install dependencies and seed the fictional records:

   ```sh
   uv sync
   PYTHONPATH=backend uv run python -m app.seed
   ```

4. Start the API:

   ```sh
   uv run uvicorn app.main:app --app-dir backend --reload
   ```

5. In a second terminal, start the React manager screen:

   ```sh
   cd frontend
   npm install
   npm run dev
   ```

   Open the local Vite URL shown in the terminal. The API permits the local Vite origins by default; set `FRONTEND_ORIGINS` to a comma-separated origin list when using another frontend URL. Set `VITE_API_BASE_URL` when the API is not at `http://127.0.0.1:8000`.

The interactive API docs are at `/docs`. Use `POST /api/v1/rosters/generate` with an integer year from the current year through five years ahead and an integer month from 1 to 12 to generate a draft. The response reports coverage totals and pending-availability warnings. Inspect assignments, uncovered slots, and employee-day statuses at `GET /api/v1/rosters/{roster_run_id}`; independent validation is at `/api/v1/rosters/{roster_run_id}/validation`.

The manager screen lets you select an employee and dates for leave, comp-off, or unavailability, update a brand/process calendar, weekday staffing minimum, or same-day sharing setting, inspect affected coverage and backup candidates, and confirm a targeted replan. The replan creates a new draft and preserves unaffected assignments unless a move is needed to restore coverage. The APIs are `POST /api/v1/availability-exceptions`, `PATCH /api/v1/brand-process-rules/{rule_id}`, `GET /api/v1/rosters/{roster_run_id}/replacement-candidates`, and `POST /api/v1/rosters/{roster_run_id}/replan`.

No employee/date-specific absence scenario is seeded. The manager chooses the employee, leave/comp-off/unavailability type, approval status, and date range; the solver uses the saved availability when generating or replanning. Pending requests appear as warnings and do not block scheduling. Re-running the seed removes only the legacy fixed absence examples and preserves availability records entered by a manager.

All seeded names, brands, holidays, rules, and availability examples are fictional. The demo's 8-hour/day, 5-day/40-hour week, staggered weekly offs, and coverage figures are configurable assumptions, not verified company policy. The scheduler works at a daily level: it does not model shift start/end times, intraday continuity, rest between shifts, or work outside the selected month when applying weekly limits. These are demo limitations, not claims of legal compliance.

## Project stages

Work is gated by the stages in `requirement.txt`. Stages 1–3 build the data foundation, monthly allocation, independent validation, and explainable change handling. Stages 4–5 cover the remaining assessment workflows after the core scheduling gate passes.
