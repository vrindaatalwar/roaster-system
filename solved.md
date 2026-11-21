# Smart Roster: Functional Coverage and Scheduling Logic

## Assessment coverage review

| Assessment requirement | Project implementation | Status |
|---|---|---|
| Central employee, brand, process, pod, lead and allocation data | PostgreSQL relational models with SQLAlchemy; manager CRUD in the React app | Implemented |
| At least 30 employees across multiple pods and brands | Demo seed supplies 30 employees, two pods, 15 fictional brands and four process types | Implemented |
| Different process calendars and minimum staffing | Brand/process rules configure weekdays, holiday operation, effort, minimum coverage, skills, shareability and pod window | Implemented |
| Monthly roster generation | FastAPI invokes OR-Tools CP-SAT and persists a draft roster | Implemented |
| Availability, weekly offs, approved leave and comp-off | Availability requests with approval state feed the solver and validator; weekly off and contract rules are per employee | Implemented |
| Coverage and conflict validation | Independent validator checks persisted assignments against current rules; gaps remain visible on partial drafts | Implemented |
| Company, pod, brand, process and employee views | Manager filters and roster perspectives plus pod-lead and employee read-only views | Implemented |
| Manual assignment edits with validation | Draft assignment changes use qualified candidate screening and revalidation | Implemented |
| Changes after generation | Preview and targeted replan show affected dates and recommendations; manager confirms before a new draft is saved | Implemented |
| Public holiday dates | Backend startup sync imports Maharashtra state-government holidays into `public_holidays`; generation and validation use this table. The former manual holiday editor was removed. | Implemented; live feed refresh not verified in this session because the backend was not running. |
| Public deployment, demo video, LinkedIn profile and email submission | These require hosting/account details and user-owned content | Outside the functional app; not claimed complete here |

## How the scheduling problem is solved

### 1. Model the operation as related data

- **Employee:** stable employee code, name, role, active status, skills, work contract, weekly offs, availability records, preferences and pod memberships.
- **Pod:** team, specialization, lead and scheduled work window. Employees have one primary pod and may have an additional membership. Membership does not imply a skill or brand qualification.
- **Brand and process:** a brand can require several processes. Each process has a required skill and default effort.
- **Brand/process rule:** operating weekdays, public-holiday behavior, minimum/maximum staffing where configured, effort per person, whether multiple employees can share a process on a day, and its assigned pod window.
- **Eligibility:** separate primary-owner or backup authorization for a brand/process. This is deliberately independent of pod membership and skills.
- **Roster run:** a dated draft or published snapshot containing assignments and employee-day statuses. Changes are reviewed as new drafts rather than silently mutating a published roster.

This structure supports one-person brands, multi-person brands with separate process owners, and shared-process teams without treating all brands as identical.

### 2. Generate a month with hard constraints and ordered preferences

The backend creates candidate assignments for each required brand/process/day slot and asks OR-Tools CP-SAT to choose a feasible set. A person is a valid candidate only when active, qualified, explicitly eligible, in the required pod/window, available, and within all applicable limits.

Hard constraints include:

1. Required skill and explicit brand/process eligibility.
2. Employee availability: approved leave, approved comp-off, unavailable dates and weekly offs block work. Pending requests follow their approval state and do not silently become approved absences.
3. Employee daily and weekly hour limits and maximum scheduled days.
4. Pod allocation capacity and shift-window overlap restrictions.
5. Required distinct headcount for each brand/process/day. Sharing is allowed only when that process rule permits it.
6. No assignment is counted as coverage when it is marked uncovered.

The objective is lexicographic in intent: maximize required slots covered first; break equal-coverage solutions using brand deadline priority; then prefer primary owners, preserve assignments during repair, balance workloads and weekend/holiday work, and consider employee preferences. Soft preferences never make a feasible required slot intentionally unfilled.

The demo assumptions distinguish a pod's clock window from productive allocation time: a window can span eight hours with a lunch break while its assignable capacity is six hours. CS-skilled demo contracts may have a 45-hour weekly cap; other default contracts use 40 hours. These are configurable example values, not company policy.

### 3. Validate independently

After generation or a manual edit, the validator checks the proposed/persisted roster against the current employee contracts, availability, eligibility, skills, calendars, capacity and coverage requirements. The solver and validator are separate so a solver result is not trusted without a second check.

The result can be a **partial draft** when total coverage is infeasible. Unfilled slots have a concrete reason based on candidate eligibility, availability, capacity, calendar or window constraints. A coverage gap is distinct from a hard-rule violation: gaps can be reviewed, but hard violations block publication. Managers may explicitly publish a freshly validated partial roster while its gaps remain visible.

### 4. Explain single-owner and multi-person cases

- A single primary owner covers the configured processes when available and qualified. Weekly offs and leave require an eligible backup or shared pool; pod membership alone cannot substitute for qualification.
- For multi-person brands, separate employees can own different processes. A process requiring several people gets distinct assignments up to its configured minimum. Same-day sharing is controlled per process.
- Cross-brand/cross-pod employees can be candidates only where both explicit eligibility and the needed skill are present. Their total daily/weekly capacity and simultaneous pod-window conflicts still apply.

### 5. Handle an employee absence or a changed brand brief

The manager enters availability or edits a brand/process rule. The app computes affected dates and previews retained, reassigned, added, removed and unfilled coverage. Candidates are ranked using hard feasibility first, then existing eligibility, remaining workload, owner/backup status, fairness and preferences. All qualifying options remain available for review.

Preview does not mutate the roster. The manager explicitly confirms a targeted replan; assignments outside the affected dates are preserved. The new draft is validated before saving. Undo/redo supports reviewing roster edits, and publication remains an explicit manager action.

### 6. Divide automated work from human decisions

Automated: candidate filtering, constraint optimization, capacity accounting, conflict detection, validation, impact previews and ranked suggestions.

Human-controlled: employee/pod/brand/process configuration, availability approvals, priority values, manual assignment selection, accepting a proposed replan and publication. The solver proposes a roster; it does not approve leave or silently publish an infeasible plan.

### 7. Roles and visibility

Manager view supports configuration, roster generation, review, editing and publication. Pod leads see their relevant pod schedule and conflicts; employees see their own relevant roster/availability view. These role views are development/demo role selection without authentication, so they are not a production access-control boundary.

### 8. Scaling and next improvement

For a doubled organization, first add authenticated identity-to-role binding, API pagination, migrations, load/solver monitoring and load tests. Benchmark before decomposing the solver; any decomposition must preserve employee-wide capacity and cross-pod conflict constraints.

The next scheduling extension is intraday task-time/rest modeling if actual operations require overlap and minimum-rest guarantees. Before that, replace synthetic workforce and holiday inputs with approved data. For Mumbai, the startup sync refreshes the existing holiday source from the Maharashtra state-government calendar feed. The sync retains the last successful cache during temporary feed failure.

## Boundaries and assumptions

- Demo names, process descriptions, calendars, priorities, staffing numbers and contract limits are synthetic unless explicitly supplied by the business.
- The assessment asks for a 3–5 minute walkthrough in its opening deliverables section; a later submission section says “35 minute,” which appears to be a formatting typo. Confirm the intended duration before recording.
- The assessment mentions a public working URL, repository, demo, approach note and profile link. This document evaluates the functional application and does not claim external submission/deployment items are complete.
