# DataStraw Smart Roster

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

### Optional hours allocation

The original day-level minimum-staff allocator remains Layer 1. The manager can enable the optional half-hour Layer 2 in **Brands → Hours allocation layer**, or through `PUT /api/v1/allocation-settings`. Defaults are 12 units (6 hours) per employee/day, 1 unit (30-minute) minimum task chunk, at most 2 brands per employee/day, and a 60-second time limit. A generation snapshots those settings and priority components for audit. If Layer 2 is disabled, infeasible, times out without a usable result, or fails independent validation, the generated Layer 1 draft is retained.

For every process/date, Layer 2 first minimizes shortfall from the guaranteed floor of `minimum_staff × daily capacity` (capped by configured demand); then it maximizes priority-weighted demand above that floor. The first surplus block has full priority weight and further units receive half weight to model diminishing returns. It then prefers primary owners, fewer brand assignments per employee/day, and a more balanced monthly load. It enforces shared employee capacity across all brands and pods, min/max staffing, max brands/day, weekly hours and workdays, approved leave and weekly offs, eligibility, skills, pod membership, same-day sharing rules, and incompatible pod windows. Uncovered staffing floors remain draft gaps and block publishing; hard rule violations always block publishing.

Brand priority is `client_tier × urgency × backlog_factor × manager_override`. Client tier is 1–5. Urgency is 1 with no deadline and otherwise `min(5, 30 / max(days_to_deadline, 1))`. A manager can add a date-ranged multiplier through the brand page or `/api/v1/priority-overrides`. Backlog is the previous same-month draft's unserved demand divided by that period's total demand, floored at 0.05; on a brand's first generation in a month it starts at 1.0 because no earlier service is recorded. The API exposes the score components, served versus demand units, solver status, and solve time.

The hours layer does not model exact start/end task times, intraday continuity, inter-shift rest, or weekly limits that cross the selected month boundary. The first-generation backlog factor is an initial-period assumption. The feature flag defaults off until a manager enables it.

### Role-based views (development mode)

Use the **Demo role** selector in the top bar to preview the Manager, Pod lead, and Employee experiences. Pod leads must choose an employee configured as a pod lead; their view is read-only and scoped to their led pods. Employee views are read-only and scoped to that employee's own schedule and availability. Manager-only configuration, assignment edits, replanning, and publishing are also checked by the API.

This is role-based workflow preview without authentication. The browser sends `X-App-Role` and, for pod leads and employees, `X-Employee-Code`; a caller can change those headers and impersonate another demo role or employee. Do not treat these checks as identity verification or expose this unauthenticated mode as a secure production access control system. Add a trusted sign-in provider before using real employee data.

### Run the complete app with containers

Copy `.env.example` to `.env`, set a local `POSTGRES_PASSWORD`, then run:

```sh
docker compose up --build -d
docker compose exec backend uv run python -m app.seed
```

Open `http://localhost:8080`. The React site and API share one origin through the included web proxy. PostgreSQL data is kept in a named volume. Do not reuse the sample password on a public host. For a public deployment, configure the same services with managed PostgreSQL, a private database connection string, HTTPS, and the deployed frontend origin in `FRONTEND_ORIGINS`.

The manager screen lets you manage employees, pods, brands, processes, employee skills, brand/process eligibility, public holidays, and daily coverage. It includes company, pod, brand, process, and employee roster filters. Draft assignment edits are validated immediately. Existing leave, comp-off, and unavailability records can be moved between pending, approved, and declined; approved changes can be previewed and applied as a targeted draft replan. The current roster remains in place until the manager applies a proposed replan; applying it creates a new draft and preserves assignments outside the affected dates.

No employee/date-specific absence scenario is seeded. The manager chooses the employee, leave/comp-off/unavailability type, approval status, and date range; the solver uses the saved availability when generating or replanning. Pending requests appear as warnings and do not block scheduling. Re-running the seed removes only the legacy fixed absence examples and preserves availability records entered by a manager.

All seeded names, brands, holidays, rules, priorities, and availability examples are fictional. Demo assumptions include two configurable pod windows, each spanning eight clock hours with a one-hour lunch and up to six allocatable work hours per employee, plus a five-day weekly contract capped at 40 hours for non-CS employees and 45 hours for CS-skilled employees. The separate six-hour daily cap means the effective weekly allocatable ceiling remains 30 hours until daily capacity or maximum workdays are raised. These are not verified company policies. A clean database seed includes varied coverage calendars, shared ownership, cross-pod backups, deadline priorities, and one deliberate no-backup case. Seeding does not reset or overwrite an existing database; an existing installation keeps its saved configuration and rosters. Existing databases need manager configuration of pod windows, process-to-pod mappings, and cross-pod memberships to use the new behavior. Back up manager data before creating a fresh demo database to use the revised sample defaults. Pod windows filter candidates and prevent overlapping-window assignments; exact task start/end times, continuous intraday coverage, inter-shift rest, and work outside the selected month for weekly limits are not modeled. These are demo limitations, not claims of legal compliance.

## Assessment questions and answers

### 1. How did you model employees, brands, processes, pods, and their relationships?

Employees have a primary pod and can belong to additional pods. Their records include role, skills, work contract, weekly offs, preferences, and dated availability. A brand can have multiple processes, and each brand/process rule defines its coverage calendar, staffing or effort needs, sharing policy, and pod work window. Employee eligibility is recorded separately for each brand/process as a primary or backup assignment; pod membership or a skill alone does not grant eligibility. Roster runs store dated assignments and employee-day statuses.

### 2. How does your system generate a roster and determine whether it satisfies the required constraints?

For a requested month, OR-Tools CP-SAT selects eligible employees for configured brand/process/date requirements. It enforces hard rules for active status, skills and eligibility, pod membership, approved unavailability, weekly offs, daily and weekly capacity, workday limits, staffing bounds, sharing rules, and conflicting pod windows. An independent validator checks the saved roster. Hard-rule violations reject the proposed roster; coverage gaps can remain in a draft with explanations. When feasible solutions cover the same number of slots, deadline priority can break the tie.

### 3. How did you handle the difference between single-resource brands and brands with multiple employees?

A process requiring one person is assigned to one eligible employee per day. Where sharing is allowed, multiple eligible employees can cover the process within its configured staffing bounds. Primary and backup eligibility are explicit, and capacity is shared across all brands and pods so an employee is not over-allocated.

### 4. What happens when an employee manages all processes for a brand but is unavailable on a required working day?

The manager records and approves the absence. The system identifies affected dates, screens eligible backup employees against capacity and other hard rules, and can prepare a targeted replan. The manager reviews and applies the proposal, leaving the current roster in place until then. If no replacement is feasible, the slot remains uncovered in the draft with an explanation.

### 5. How does your system handle conflicting requirements or insufficient staffing?

The solver maximizes feasible coverage without breaking hard constraints. If qualified capacity is insufficient, it returns a partial draft with uncovered slots and blockers. Preferences are treated as soft goals. Independent validation reports conflicts, and publishing is blocked by hard-rule violations or unresolved gaps unless a manager explicitly confirms the gaps.

### 6. Which parts of the process did you automate, and which did you leave under human control? Why?

The system automates candidate screening, roster allocation, constraint validation, gap reporting, and proposed replanning. Managers configure employees and brand/process rules, record or approve availability, set priority overrides, edit drafts, review replans, and choose whether to apply or publish them. This keeps repetitive allocation and checks consistent while leaving decisions with operational impact under manager review. Manual assignment uses the same hard-rule checks as generated assignments.

### 7. If the company doubled its team and brand count, what would you change about your solution?

I would first add trusted authentication with roles tied to verified identities, database migrations, API pagination, and monitoring for solver time, outcomes, and service errors. I would benchmark larger workloads and add load tests. If solve time became a bottleneck, I would explore splitting independent coverage groups while preserving shared employee capacity and cross-pod constraints. The current role selector is a development preview, not production access control.

### 8. If you had one additional week to work on this, what would you improve first?

I would reduce manual coordination by prototyping a manager assistant for leave requests. It would explain roster impact, identify qualified backups, and prepare a recommendation or replan for manager review, without approving leave or changing assignments automatically. I would also add clearer priority guidance because current priorities use demo data. For employees and pod leads, I would improve the calendar with assigned work and meetings, then explore calendar integration and assignment notifications, including Slack. These additions would need role-appropriate access to real employee and calendar data.

## Tests and guardrails

Run the backend test suite from the project root with:

```sh
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

The suite checks manager input guardrails and CRUD for employees, pods/leads, brands, processes, and brand/process coverage rules; solver/validator agreement across 50 deterministic small fixtures; partial rosters for infeasible demand; approved/pending/declined availability; public holidays; primary and cross-pod membership; overlapping work windows; deadline-priority tie-breaking; manual edits; and targeted replan preservation. Generation independently validates a proposed roster before committing: uncovered demand may remain in a partial draft, but a hard-rule violation rolls the transaction back.

Layer 1 uses two CP-SAT workers and a 12-second solve limit so roster generation stays responsive on a small hosted API replica. A feasible answer is returned as soon as one is available; proving that answer optimal is not required to save a valid draft. The seeded 30-employee, 421-slot November 2026 fixture returned the same 417 assignments and 4 uncovered slots in 12.1 seconds locally. Wall-clock timings vary by machine and are not a deployment benchmark.

After adding the hours layer, an isolated SQLite run of the complete 30-employee seed for November 2026 completed generation in 23.344 seconds with Layer 2 enabled. The hours model was optimal in 0.889 seconds across 2,280 assignment/hour variables; it preserved 417 of 421 minimum staffing slots, with four allowed coverage gaps. This is a local smoke benchmark, not a production performance guarantee.

