# DataStraw Smart Roster

Stages 1–3 provide the configurable workforce foundation, CP-SAT monthly allocation, independent validation, and manager-approved targeted replanning. Stage 4 provides manager-managed team and work configuration, company/pod/brand/process/employee roster perspectives, daily coverage counts, and validated assignment editing. The approved Stage 6 extension adds two configurable pod work windows, cross-pod memberships, window-aware assignment candidates, and deadline priority as a scheduling tie-breaker.

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

### Run the complete app with containers

Copy `.env.example` to `.env`, set a local `POSTGRES_PASSWORD`, then run:

```sh
docker compose up --build -d
docker compose exec backend uv run python -m app.seed
```

Open `http://localhost:8080`. The React site and API share one origin through the included web proxy. PostgreSQL data is kept in a named volume. Do not reuse the sample password on a public host. For a public deployment, configure the same services with managed PostgreSQL, a private database connection string, HTTPS, and the deployed frontend origin in `FRONTEND_ORIGINS`.

The manager screen lets you manage employees, pods, brands, processes, employee skills, brand/process eligibility, public holidays, and daily coverage. It includes company, pod, brand, process, and employee roster filters. Draft assignment edits are validated immediately. Existing leave, comp-off, and unavailability records can be moved between pending, approved, and declined; approved changes can be previewed and applied as a targeted draft replan. The current roster remains in place until the manager applies a proposed replan; applying it creates a new draft and preserves assignments outside the affected dates.

No employee/date-specific absence scenario is seeded. The manager chooses the employee, leave/comp-off/unavailability type, approval status, and date range; the solver uses the saved availability when generating or replanning. Pending requests appear as warnings and do not block scheduling. Re-running the seed removes only the legacy fixed absence examples and preserves availability records entered by a manager.

All seeded names, brands, holidays, rules, priorities, and availability examples are fictional. Demo assumptions include two configurable pod windows, each spanning eight clock hours with a one-hour lunch and up to six allocatable work hours per employee, plus a five-day/40-hour weekly contract. These are not verified company policies. A clean database seed includes varied coverage calendars, shared ownership, cross-pod backups, deadline priorities, and one deliberate no-backup case. Seeding does not reset or overwrite an existing database; an existing installation keeps its saved configuration and rosters. Existing databases need manager configuration of pod windows, process-to-pod mappings, and cross-pod memberships to use the new behavior. Back up manager data before creating a fresh demo database to use the revised sample defaults. Pod windows filter candidates and prevent overlapping-window assignments; exact task start/end times, continuous intraday coverage, inter-shift rest, and work outside the selected month for weekly limits are not modeled. These are demo limitations, not claims of legal compliance.

## Assessment approach

1. **Data model:** Employees have a primary pod and configurable memberships, role, skills, work contract, weekly offs, preferences, and availability records. Brand/process rules hold calendars, effort, same-day sharing, minimum/maximum staffing, a pod work window, and brand deadline priority. Eligibility remains a separate primary/backup relationship; pod membership does not grant eligibility. Roster runs own dated assignments and employee-day statuses.
2. **Generation and validation:** OR-Tools CP-SAT selects employees for each configured brand/process/date slot. Hard rules cover active status, required skill and eligibility, pod membership, approved unavailability, weekly offs, daily and weekly effort, work-day limits, and overlapping pod-window conflicts. The independent validator checks persisted assignments against current inputs and returns coverage gaps and rule conflicts. Total feasible coverage is maximized first; when solutions tie on coverage, manager-set higher deadline priority is preferred.
3. **Single- and multi-resource brands:** Most sample brands have one primary owner, with separately qualified backups covering weekly offs and absences. Other sample brands show separate process owners and multiple employees sharing the same process. Multiple people count toward same-day coverage only when that process permits sharing.
4. **Unavailable sole owner:** The manager records and approves an absence. The validator identifies affected slots, the candidate list reports qualified employees who still have capacity, and the manager reviews a proposed replan. If no replacement is feasible, the slot stays uncovered with an explanation.
5. **Conflicts and insufficient staffing:** The solver maximizes filled coverage without violating hard constraints. It can return a partial draft with uncovered slots and concrete blockers. Preferences are soft goals. The UI labels the result as a draft and shows validation before a manager decides what to change.
6. **Automation and human control:** Allocation, constraint checks, candidate screening, and proposed replanning are automated. A manager controls availability status, configuration, deadline priority, manual assignment changes, and whether to apply a proposed roster. The manual picker uses the same hard candidate rules as generation.
7. **Doubling the team and brands:** Keep the relational model and solver constraints, but add database migrations, API pagination, role-based access, solver-performance monitoring, and load tests. If solve time grows, benchmark the model and consider decomposing by independent coverage groups while preserving global employee capacity constraints.
8. **One more week:** Add exact task-time blocks and rest constraints if the business needs intraday coverage validation, then run the expanded model against real historical demand and employee contracts. Replace fictional data with approved operational inputs before production use.

## Tests and guardrails

Run the backend test suite from the project root with:

```sh
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

The suite checks manager input guardrails and CRUD for employees, pods/leads, brands, processes, and brand/process coverage rules; solver/validator agreement across 50 deterministic small fixtures; partial rosters for infeasible demand; approved/pending/declined availability; public holidays; primary and cross-pod membership; overlapping work windows; deadline-priority tie-breaking; manual edits; and targeted replan preservation. Generation independently validates a proposed roster before committing: uncovered demand may remain in a partial draft, but a hard-rule violation rolls the transaction back.

In the local timing run on 2026-11-21, the 30-employee seeded monthly roster had 421 required slots and solved optimally in 24.129 seconds (417 filled, 4 uncovered). A duplicated 60-employee fixture returned a feasible roster in 60.267 seconds (same coverage totals) at the configured 60-second solver limit; the larger run reached a feasible answer but did not prove optimality. Wall-clock timings depend on the machine and are not a deployment benchmark.

## Demo walkthrough (3–5 minutes)

1. Generate a monthly roster and show the company view, employee-day statuses, and a brand-specific calendar.
2. Filter the roster by pod, brand, process, and employee; open an assignment reason and edit a draft assignment to show immediate validation.
3. Open a brand/process rule and change a coverage day or minimum; review the affected dates and before/after allocation proposal.
4. Record or approve an absence for one of three eligible employees, inspect ranked backup options, and apply the targeted replan.
5. Show the deliberate sole-owner/no-backup case and its uncovered-slot explanation.

## Deployment and submission checklist

The source is prepared for a container deployment. The assessment additionally requires a public application URL, public GitHub repository, demo video, approach note, accessible LinkedIn link, and a submission email with a specific subject. Those items require the submitter's hosting account, repository visibility, video, and personal profile link; do not use fictional links or claim a deployment that has not been published.

## Project stages

Work is gated by the stages in `requirement.txt`. Stages 1–4 cover the data foundation, allocation, independent validation, manager configuration, roster perspectives, and validated manual draft edits. Stage 5 covers assessment change workflow and submission readiness; Stage 6 is the approved time-window, cross-pod, and deadline-priority extension. Stage 7+ contains optional work. Public hosting, repository visibility, demo recording, and the submitter's own profile link still require the submitter's accounts and input.
