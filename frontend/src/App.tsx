import { useEffect, useMemo, useState, type FormEvent } from "react";

const API = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const today = new Date();

type Page = "roster" | "employees" | "brands";
type AvailabilityException = {
  type: string;
  approval_status: string;
  start_date: string;
  end_date: string;
  note?: string;
};
type Employee = {
  code: string;
  name: string;
  role: string;
  status: string;
  pod: string;
  is_pod_lead: boolean;
  skills: string[];
  weekly_off_weekdays: number[];
  availability_exceptions: AvailabilityException[];
  contract: {
    daily_capacity_hours: number;
    max_days_per_week: number;
    max_hours_per_week: number;
    minimum_days_off_per_week: number;
  };
};
type Rule = {
  rule_id: number;
  code: string;
  name: string;
  operating_weekdays: number[];
  operate_on_public_holidays: boolean;
  can_share_same_day: boolean;
  effort_hours_per_employee: number;
  required_skill: string;
  coverage_by_weekday: { weekday: number; minimum_staff: number; maximum_staff: number | null }[];
  eligible_employees: { employee_code: string; eligibility_type: string; active: boolean }[];
};
type Brand = { code: string; name: string; active: boolean; processes: Rule[] };
type Summary = { employees: Employee[]; brands: Brand[]; public_holidays: { name: string; date: string }[] };
type Assignment = { date: string; employee_code: string | null; brand_code: string; process_code: string; effort_hours: number; status: string; explanation: string };
type ValidationIssue = { severity: string; code: string; message: string; date?: string; brand_code?: string; process_code?: string; employee_code?: string };
type Validation = { status: string; issue_count: number; error_count: number; warning_count: number; issues: ValidationIssue[] };
type Roster = {
  roster_run_id: number;
  year: number;
  month: number;
  generation: { required_slots: number; filled_slots: number; uncovered_slots: number; result_status: string } | null;
  assignments: Assignment[];
  employee_days: { date: string; employee_code: string; status: string; assigned_effort_hours: number }[];
};
type Change = { roster_run_id: number; affected_dates: string[]; validation?: Validation };
type ChangeResponse = { affected_runs?: Change[]; [key: string]: unknown };
type ChangeValidation = { before: Validation; after: Validation; affected_dates: string[] };
type Candidate = { employee_code: string; name: string; eligibility_type: string; projected_weekly_hours: number; reason: string };
type CandidateResult = { date: string; brand_code: string; process_code: string; candidates: Candidate[]; explanation: string; common_blockers?: Record<string, number> };
type Replacement = { date: string; brand_code: string; process_code: string; employee_code: string };
type Comparison = { before: Validation; after: Validation; preserved: boolean };

function pageFromHash(): Page {
  const path = window.location.hash.replace(/^#\/?/, "");
  return path === "employees" || path === "brands" ? path : "roster";
}

function issueKey(issue: ValidationIssue): string {
  return JSON.stringify([issue.severity, issue.code, issue.date, issue.brand_code,
    issue.process_code, issue.employee_code, issue.message]);
}

function issuesOnDates(validation: Validation, affectedDates: string[]): ValidationIssue[] {
  const dates = new Set(affectedDates);
  return validation.issues.filter((issue) => issue.date && dates.has(issue.date));
}

function newlyIntroducedIssues(before: Validation, after: Validation, affectedDates: string[]): ValidationIssue[] {
  const existingCounts = new Map<string, number>();
  issuesOnDates(before, affectedDates).forEach((issue) => {
    const key = issueKey(issue);
    existingCounts.set(key, (existingCounts.get(key) ?? 0) + 1);
  });
  return issuesOnDates(after, affectedDates).filter((issue) => {
    const key = issueKey(issue);
    const count = existingCounts.get(key) ?? 0;
    if (count === 0) return true;
    existingCounts.set(key, count - 1);
    return false;
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data as T;
}

function App() {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [roster, setRoster] = useState<Roster | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [employeeCode, setEmployeeCode] = useState("");
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employeeDayFilter, setEmployeeDayFilter] = useState("");
  const [exceptionType, setExceptionType] = useState("leave");
  const [approvalStatus, setApprovalStatus] = useState("approved");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [ruleId, setRuleId] = useState<number | "">("");
  const [coverageWeekday, setCoverageWeekday] = useState(0);
  const [minimumStaff, setMinimumStaff] = useState("");
  const [selectedDays, setSelectedDays] = useState<number[]>([]);
  const [operateOnHolidays, setOperateOnHolidays] = useState(false);
  const [canShareSameDay, setCanShareSameDay] = useState(false);
  const [change, setChange] = useState<Change | null>(null);
  const [changeKind, setChangeKind] = useState("");
  const [candidates, setCandidates] = useState<CandidateResult[]>([]);
  const [selectedReplacement, setSelectedReplacement] = useState<Replacement | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [changeValidation, setChangeValidation] = useState<ChangeValidation | null>(null);
  const [brandFilter, setBrandFilter] = useState("all");
  const [processFilter, setProcessFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    request<Summary>("/api/v1/seed-summary").then((data) => {
      setSummary(data);
      const firstActive = data.employees.find((employee) => employee.status === "active");
      setEmployeeCode(firstActive?.code ?? "");
      setEmployeeDayFilter(firstActive?.code ?? "");
      const firstRule = data.brands.flatMap((brand) => brand.processes)[0];
      if (firstRule) {
        setRuleId(firstRule.rule_id);
        setSelectedDays(firstRule.operating_weekdays);
        setOperateOnHolidays(firstRule.operate_on_public_holidays);
        setCanShareSameDay(firstRule.can_share_same_day);
      }
    }).catch((reason: Error) => setError(reason.message));
  }, []);

  const rules = useMemo(() => summary?.brands.flatMap((brand) => brand.processes.map((rule) => ({
    ...rule, brand_code: brand.code, brand_name: brand.name,
  }))) ?? [], [summary]);
  const activeRule = rules.find((rule) => rule.rule_id === ruleId);
  const activeEmployees = summary?.employees.filter((employee) => employee.status === "active") ?? [];
  const visibleProcesses = [...new Set(rules.filter((rule) => brandFilter === "all" || rule.brand_code === brandFilter).map((rule) => rule.code))];
  const visibleAssignments = useMemo(() => (roster?.assignments ?? []).filter((item) =>
    (brandFilter === "all" || item.brand_code === brandFilter)
    && (processFilter === "all" || item.process_code === processFilter)
    && (!dateFilter || item.date === dateFilter)), [roster, brandFilter, processFilter, dateFilter]);
  const visibleEmployees = useMemo(() => (summary?.employees ?? []).filter((employee) => {
    const query = employeeSearch.trim().toLowerCase();
    return !query || [employee.code, employee.name, employee.role, employee.pod].some((value) => value.toLowerCase().includes(query));
  }), [summary, employeeSearch]);

  function navigate(nextPage: Page) {
    if (page === nextPage) return;
    window.location.hash = `/${nextPage}`;
    setPage(nextPage);
    setNotice("");
    setError("");
  }

  async function loadRoster(id: number) {
    const [data, checks] = await Promise.all([
      request<Roster>(`/api/v1/rosters/${id}`),
      request<Validation>(`/api/v1/rosters/${id}/validation`),
    ]);
    setRoster(data);
    setValidation(checks);
    setBrandFilter("all");
    setProcessFilter("all");
    setDateFilter("");
  }

  async function runAction(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Something went wrong."); }
    finally { setBusy(false); }
  }

  function changeFromResponse(response: ChangeResponse, kind: string, before: Validation | null) {
    const runs = response.affected_runs ?? [];
    const target = runs.find((run) => run.roster_run_id === roster?.roster_run_id) ?? runs[0];
    setChange(target ?? null);
    setChangeKind(kind);
    setChangeValidation(target?.validation && before ? {
      before, after: target.validation, affected_dates: target.affected_dates,
    } : null);
    setCandidates([]);
    setSelectedReplacement(null);
    if (!target) {
      setNotice("The setting was saved. No generated roster overlaps the selected dates yet; generate one for those dates to see its impact.");
      return;
    }
    setValidation(target.validation ?? null);
    setNotice(`Saved. ${target.affected_dates.length} date(s) are affected in roster ${target.roster_run_id}. Review the coverage impact, then choose whether to replan.`);
  }

  async function loadCandidates(target: Change, employee: string) {
    if (!roster) return;
    const days = new Set(target.affected_dates);
    const impacted = roster.assignments.filter((item) => item.employee_code === employee && days.has(item.date) && item.status === "working");
    const unique = new Map<string, Assignment>();
    impacted.forEach((item) => unique.set(`${item.date}-${item.brand_code}-${item.process_code}`, item));
    const results = await Promise.all([...unique.values()].map((item) => {
      const query = new URLSearchParams({ assignment_date: item.date, brand_code: item.brand_code, process_code: item.process_code, excluding_employee_code: employee });
      return request<CandidateResult>(`/api/v1/rosters/${target.roster_run_id}/replacement-candidates?${query}`);
    }));
    setCandidates(results);
  }

  async function loadGapCandidates(target: Change, before: Validation | null) {
    const added = target.validation && before
      ? newlyIntroducedIssues(before, target.validation, target.affected_dates)
      : target.validation?.issues ?? [];
    const gaps = added.filter((issue) => issue.code === "insufficient_coverage" && issue.date && issue.brand_code && issue.process_code && target.affected_dates.includes(issue.date));
    const unique = new Map<string, { date: string; brand_code: string; process_code: string }>();
    gaps.forEach((gap) => unique.set(`${gap.date}-${gap.brand_code}-${gap.process_code}`, { date: gap.date!, brand_code: gap.brand_code!, process_code: gap.process_code! }));
    const results = await Promise.all([...unique.values()].map((item) => {
      const query = new URLSearchParams({ assignment_date: item.date, brand_code: item.brand_code, process_code: item.process_code });
      return request<CandidateResult>(`/api/v1/rosters/${target.roster_run_id}/replacement-candidates?${query}`);
    }));
    setCandidates(results);
  }

  async function saveAvailability(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const before = validation;
      const result = await request<ChangeResponse>("/api/v1/availability-exceptions", {
        method: "POST",
        body: JSON.stringify({ employee_code: employeeCode, exception_type: exceptionType,
          approval_status: approvalStatus, start_date: startDate, end_date: endDate }),
      });
      changeFromResponse(result, "availability", before);
      const target = (result.affected_runs ?? []).find((item) => item.roster_run_id === roster?.roster_run_id)
        ?? (result.affected_runs ?? [])[0];
      if (target) await loadCandidates(target, employeeCode);
      setSummary(await request<Summary>("/api/v1/seed-summary"));
    });
  }

  async function saveBrandRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ruleId) return;
    await runAction(async () => {
      const before = validation;
      const body: Record<string, unknown> = {
        operating_weekdays: selectedDays,
        operate_on_public_holidays: operateOnHolidays,
        can_share_same_day: canShareSameDay,
      };
      if (minimumStaff !== "") {
        body.weekday = coverageWeekday;
        body.minimum_staff = Number(minimumStaff);
      }
      const result = await request<ChangeResponse>(`/api/v1/brand-process-rules/${ruleId}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
      changeFromResponse(result, "brand rule", before);
      const target = (result.affected_runs ?? []).find((item) => item.roster_run_id === roster?.roster_run_id)
        ?? (result.affected_runs ?? [])[0];
      if (target) await loadGapCandidates(target, before);
      const updated = await request<Summary>("/api/v1/seed-summary");
      setSummary(updated);
      const updatedRule = updated.brands.flatMap((brand) => brand.processes).find((item) => item.rule_id === ruleId);
      if (updatedRule) setSelectedDays(updatedRule.operating_weekdays);
    });
  }

  async function confirmReplan() {
    if (!change) return;
    await runAction(async () => {
      const result = await request<{ roster_run_id: number; affected_dates: string[]; unaffected_assignments_preserved: boolean; before_validation: Validation; after_validation: Validation }>(
        `/api/v1/rosters/${change.roster_run_id}/replan`, {
          method: "POST",
          body: JSON.stringify({ affected_dates: change.affected_dates, replacement: selectedReplacement }),
        },
      );
      setComparison({ before: result.before_validation, after: result.after_validation, preserved: result.unaffected_assignments_preserved });
      await loadRoster(result.roster_run_id);
      setChange(null);
      setChangeValidation(null);
      setSelectedReplacement(null);
      setNotice(`Replan complete. New draft #${result.roster_run_id}.`);
      navigate("roster");
    });
  }

  function renderChangeImpact() {
    if (!change || !changeKind || (page === "employees" && changeKind !== "availability")
      || (page === "brands" && changeKind !== "brand rule")) return null;

    const beforeIssues = changeValidation ? issuesOnDates(changeValidation.before, changeValidation.affected_dates) : [];
    const afterIssues = changeValidation ? issuesOnDates(changeValidation.after, changeValidation.affected_dates) : [];
    const newIssues = changeValidation
      ? newlyIntroducedIssues(changeValidation.before, changeValidation.after, changeValidation.affected_dates) : [];
    const newlyClosedAssignments = newIssues.filter((issue) => issue.code === "closed_day_assignment").length;
    const newAbsenceConflicts = newIssues.filter((issue) => issue.code === "approved_absence_conflict").length;

    return <div className="change-impact">
      <div className="impact-heading">
        <div>
          <span className="impact-label">CHANGE SAVED · {changeKind.toUpperCase()}</span>
          <h3>{change.affected_dates.length} affected date(s)</h3>
          <p>Review the affected coverage and suggested backups. Confirming a targeted replan creates a new draft and preserves assignments on other dates.</p>
        </div>
        <button className="primary-button" disabled={busy} onClick={() => void confirmReplan()}>
          {selectedReplacement ? `Confirm ${selectedReplacement.employee_code} and replan →` : "Confirm targeted replan →"}
        </button>
      </div>
      {changeValidation && <div className="comparison-strip impact-comparison">
        <strong>Impact on affected dates</strong>
        <span>Errors: <b>{beforeIssues.filter((issue) => issue.severity === "error").length}</b> → <b>{afterIssues.filter((issue) => issue.severity === "error").length}</b></span>
        <span>New issues: <b>{newIssues.length}</b></span>
        {newlyClosedAssignments > 0 && <span>{newlyClosedAssignments} assignment(s) remain on newly closed days until replanning.</span>}
        {newAbsenceConflicts > 0 && <span>{newAbsenceConflicts} assignment(s) conflict with this approved absence until replanning.</span>}
        <span>Unrelated dates and existing gaps are excluded.</span>
      </div>}
      {candidates.length > 0 ? <div className="candidate-grid">{candidates.map((result) => <div className="candidate-card" key={`${result.date}-${result.brand_code}-${result.process_code}`}>
        <strong>{result.brand_code} · {result.process_code}<span>{new Date(`${result.date}T12:00:00`).toLocaleDateString("en", { month: "short", day: "numeric" })}</span></strong>
        {result.candidates.length ? result.candidates.map((candidate) => {
          const selected = selectedReplacement?.date === result.date && selectedReplacement.brand_code === result.brand_code
            && selectedReplacement.process_code === result.process_code && selectedReplacement.employee_code === candidate.employee_code;
          return <button className={`candidate-row ${selected ? "candidate-selected" : ""}`} key={candidate.employee_code}
            onClick={() => setSelectedReplacement(selected ? null : { date: result.date, brand_code: result.brand_code, process_code: result.process_code, employee_code: candidate.employee_code })}>
            <span>{candidate.employee_code} · {candidate.name}</span>
            <small>{candidate.eligibility_type} · {candidate.projected_weekly_hours}h this week {selected ? "· selected" : "· select"}</small>
          </button>;
        }) : <p className="no-candidate">{result.explanation}{result.common_blockers && Object.keys(result.common_blockers).length > 0 ? ` ${Object.entries(result.common_blockers).map(([reason, count]) => `${count} ${reason}`).join(" · ")}.` : ""}</p>}
      </div>)}</div> : <p className="muted impact-empty">No newly introduced uncovered slots on these dates. Existing roster gaps remain visible on the Roster page.</p>}
    </div>;
  }

  const pageInfo: Record<Page, { title: string; description: string }> = {
    roster: { title: "Roster", description: "Generate a monthly plan and review coverage, assignments, and conflicts." },
    employees: { title: "Employees & leave", description: "Review team members and record availability changes for leave, comp-off, or unavailability." },
    brands: { title: "Brands", description: "Review brand processes and update operating calendars and coverage rules." },
  };

  return <main className="page-shell">
    <header className="topbar">
      <div className="brand-lockup"><span className="brand-mark">DS</span><div><strong>DataStraw</strong><small>Smart Roster</small></div></div>
      <div className="topbar-right"><span className="live-dot" /> Manager workspace <span className="avatar">OM</span></div>
    </header>

    <nav className="dashboard-nav" aria-label="Dashboard pages">
      {(["roster", "employees", "brands"] as Page[]).map((item) => <button key={item}
        className={`dashboard-nav-item ${page === item ? "active" : ""}`} aria-current={page === item ? "page" : undefined}
        onClick={() => navigate(item)}>
        <span className={`nav-icon ${item}`}>{item === "roster" ? "▦" : item === "employees" ? "♙" : "◇"}</span>
        <span>{item === "roster" ? "Roster" : item === "employees" ? "Employees & leave" : "Brands"}</span>
        {item === "roster" && roster && <small>#{roster.roster_run_id}</small>}
      </button>)}
    </nav>

    <section className="hero dashboard-hero">
      <div><div className="eyebrow">OPERATIONS PLANNING</div><h1>{pageInfo[page].title}</h1><p>{pageInfo[page].description}</p></div>
      <div className="hero-meta"><span className="assumption-tag">DEMO ASSUMPTIONS</span><span>Daily planning · 8 hour day · 40 hour week</span></div>
    </section>

    {error && <div className="alert error-alert"><strong>Couldn’t complete that action</strong><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss">×</button></div>}
    {notice && <div className="alert notice-alert"><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss">×</button></div>}
    {comparison && <div className="comparison-strip"><strong>Before and after this replan</strong><span>Validation errors: <b>{comparison.before.error_count}</b> → <b>{comparison.after.error_count}</b></span><span>Warnings: <b>{comparison.before.warning_count}</b> → <b>{comparison.after.warning_count}</b></span><span>{comparison.preserved ? "Unaffected assignments stayed fixed." : "Some unaffected assignments changed."}</span><button onClick={() => setComparison(null)} aria-label="Dismiss">×</button></div>}

    <section className="workspace-grid">
      <div className="main-column">
        {page === "roster" && <>
          <section className="card generate-card">
            <div className="section-heading"><div><span className="step-badge">01</span><div><h2>Generate a roster</h2><p>Choose the month to plan for the active team.</p></div></div><span className="small-label">DRAFT ONLY</span></div>
            <form className="generate-form" onSubmit={(event) => { event.preventDefault(); void runAction(async () => {
              const result = await request<{ roster_run_id: number; result_status: string }>("/api/v1/rosters/generate", { method: "POST", body: JSON.stringify({ year, month }) });
              await loadRoster(result.roster_run_id); setChange(null); setComparison(null); setChangeValidation(null); setSelectedReplacement(null);
              setNotice(`Roster ${result.roster_run_id} generated as a ${result.result_status} draft.`);
            }); }}>
              <label>Year<input type="number" min={today.getFullYear()} max={today.getFullYear() + 5} value={year} onChange={(event) => setYear(Number(event.target.value))} required /></label>
              <label>Month<select value={month} onChange={(event) => setMonth(Number(event.target.value))}>{Array.from({ length: 12 }, (_, index) => <option key={index + 1} value={index + 1}>{new Date(2026, index, 1).toLocaleString("en", { month: "long" })}</option>)}</select></label>
              <button className="primary-button" disabled={busy}>{busy ? "Working…" : roster ? "Generate new draft" : "Generate roster"}<span>→</span></button>
            </form>
          </section>

          {roster ? <>
            <section className="card roster-card">
              <div className="section-heading roster-heading"><div><span className="step-badge">02</span><div><h2>Roster review <span className="draft-chip">Draft #{roster.roster_run_id}</span></h2><p>{new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })} · All configured brands and processes</p></div></div><span className={`status-pill ${validation?.status ?? ""}`}>{validation?.status?.replace(/_/g, " ") ?? "checking"}</span></div>
              <div className="metric-grid">
                <div className="metric"><span>Required slots</span><strong>{roster.generation?.required_slots ?? 0}</strong></div>
                <div className="metric"><span>Covered</span><strong className="good-text">{roster.generation?.filled_slots ?? 0}</strong></div>
                <div className="metric"><span>Uncovered</span><strong className="risk-text">{roster.generation?.uncovered_slots ?? 0}</strong></div>
                <div className="metric"><span>Validation issues</span><strong className={validation?.error_count ? "risk-text" : "good-text"}>{validation?.issue_count ?? 0}</strong></div>
              </div>
              <div className="coverage-panel">
                <div className="coverage-title"><div><h3>Coverage and conflicts</h3><span>{validation?.error_count ?? 0} mandatory gaps or rule violations · {validation?.warning_count ?? 0} warnings</span></div><button className="text-button" disabled={busy} onClick={() => void runAction(async () => { await loadRoster(roster.roster_run_id); setNotice("Roster validation refreshed against the latest saved inputs."); })}>Refresh validation ↻</button></div>
                <div className="issue-list">
                  {(validation?.issues ?? []).filter((issue) => issue.severity === "error").slice(0, 5).map((issue, index) => <div className="issue-row" key={`${issue.code}-${issue.date}-${index}`}><span className="issue-icon">!</span><span><strong>{issue.brand_code && `${issue.brand_code} ${issue.process_code} · `}{issue.date ?? "Roster"}</strong><small>{issue.message}</small></span></div>)}
                  {validation?.error_count === 0 && <div className="empty-success">No mandatory coverage gaps or hard-rule violations found.</div>}
                  {(validation?.issues ?? []).filter((issue) => issue.severity === "warning").slice(0, 2).map((issue, index) => <div className="issue-row warning-row" key={`${issue.code}-${index}`}><span className="issue-icon">i</span><span><strong>{issue.employee_code ?? "Preference"}</strong><small>{issue.message}</small></span></div>)}
                </div>
              </div>
              <div className="table-toolbar"><div><h3>Daily assignments</h3><span>{visibleAssignments.length} rows shown</span></div><div className="filters"><input aria-label="Filter date" type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /><select value={brandFilter} onChange={(event) => { setBrandFilter(event.target.value); setProcessFilter("all"); }}><option value="all">All brands</option>{summary?.brands.map((brand) => <option key={brand.code} value={brand.code}>{brand.code}</option>)}</select><select value={processFilter} onChange={(event) => setProcessFilter(event.target.value)}><option value="all">All processes</option>{visibleProcesses.map((process) => <option key={process} value={process}>{process}</option>)}</select></div></div>
              <div className="table-wrap"><table><thead><tr><th>Date</th><th>Brand</th><th>Process</th><th>Employee</th><th>Hours</th><th>Status</th></tr></thead><tbody>{visibleAssignments.slice(0, 60).map((item, index) => <tr key={`${item.date}-${item.brand_code}-${item.process_code}-${index}`}><td>{new Date(`${item.date}T12:00:00`).toLocaleDateString("en", { day: "2-digit", month: "short", weekday: "short" })}</td><td>{item.brand_code}</td><td>{item.process_code}</td><td>{item.employee_code ?? <span className="unassigned">Uncovered</span>}</td><td>{item.effort_hours}h</td><td><span className={`row-status ${item.status}`}>{item.status}</span></td></tr>)}</tbody></table>{visibleAssignments.length > 60 && <div className="table-footnote">Showing the first 60 matching assignments. Filter by date, brand, or process to narrow the list.</div>}</div>
            </section>

            <section className="card employee-days-card">
              <div className="table-toolbar"><div><h3>Employee daily status</h3><span>Working, weekly off, approved absence, or available without an assignment</span></div><select value={employeeDayFilter} onChange={(event) => setEmployeeDayFilter(event.target.value)}>{activeEmployees.map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></div>
              <div className="table-wrap"><table><thead><tr><th>Date</th><th>Employee</th><th>Status</th><th>Assigned effort</th></tr></thead><tbody>{roster.employee_days.filter((item) => item.employee_code === employeeDayFilter && (!dateFilter || item.date === dateFilter)).map((item) => <tr key={`${item.date}-${item.employee_code}`}><td>{new Date(`${item.date}T12:00:00`).toLocaleDateString("en", { day: "2-digit", month: "short", weekday: "short" })}</td><td>{item.employee_code}</td><td><span className={`row-status ${item.status}`}>{item.status}</span></td><td>{item.assigned_effort_hours}h</td></tr>)}</tbody></table></div>
            </section>
          </> : <div className="welcome-panel"><div className="welcome-art"><span>01</span><span>02</span><span>03</span><span>04</span><span>05</span><span>06</span></div><h2>Your plan starts with coverage</h2><p>Generate a monthly draft to see daily staffing, uncovered work, and employee availability in one place.</p></div>}
        </>}

        {page === "employees" && <>
          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">01</span><div><h2>Employee directory</h2><p>{activeEmployees.length} active team members across {summary?.brands.length ?? 0} brands.</p></div></div><input className="search-input" aria-label="Search employees" placeholder="Search name, role, or pod" value={employeeSearch} onChange={(event) => setEmployeeSearch(event.target.value)} /></div>
            {summary ? <div className="table-wrap page-table"><table><thead><tr><th>Employee</th><th>Role</th><th>Pod</th><th>Skills</th><th>Weekly offs</th><th>Status</th></tr></thead><tbody>{visibleEmployees.map((employee) => <tr key={employee.code}><td><strong>{employee.code} · {employee.name}</strong>{employee.is_pod_lead && <small className="table-subtext">Pod lead</small>}</td><td>{employee.role}</td><td>{employee.pod}</td><td><div className="tag-list">{employee.skills.map((skill) => <span className="soft-tag" key={skill}>{skill}</span>)}</div></td><td>{employee.weekly_off_weekdays.map((day) => weekdays[day]).join(", ")}</td><td><span className={`row-status ${employee.status}`}>{employee.status}</span></td></tr>)}</tbody></table></div> : <p className="muted">Loading employees…</p>}
          </section>

          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">02</span><div><h2>Record employee leave or unavailability</h2><p>Choose the employee and dates. Pending requests are warnings and do not block scheduling.</p></div></div></div>
            <form className="change-form page-form" onSubmit={(event) => void saveAvailability(event)}>
              <label>Employee<select value={employeeCode} onChange={(event) => setEmployeeCode(event.target.value)}>{activeEmployees.map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></label>
              <div className="field-row"><label>Type<select value={exceptionType} onChange={(event) => setExceptionType(event.target.value)}><option value="leave">Leave</option><option value="comp_off">Comp-off</option><option value="unavailable">Unavailable</option></select></label><label>Approval status<select value={approvalStatus} onChange={(event) => setApprovalStatus(event.target.value)}><option value="approved">Approved</option><option value="pending">Pending</option><option value="declined">Declined</option></select></label></div>
              <div className="field-row"><label>From<input type="date" value={startDate} onChange={(event) => { setStartDate(event.target.value); if (!endDate) setEndDate(event.target.value); }} required /></label><label>Through<input type="date" value={endDate} min={startDate} onChange={(event) => setEndDate(event.target.value)} required /></label></div>
              <button className="secondary-button" disabled={busy || !employeeCode}>{busy ? "Saving…" : "Save and check roster impact"}</button>
            </form>
            {changeKind === "availability" && renderChangeImpact()}
          </section>

          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">03</span><div><h2>Recorded availability</h2><p>Leave, comp-off, and unavailable dates entered by the manager.</p></div></div></div>
            <div className="table-wrap page-table"><table><thead><tr><th>Employee</th><th>Type</th><th>Status</th><th>From</th><th>Through</th></tr></thead><tbody>{(summary?.employees ?? []).flatMap((employee) => employee.availability_exceptions.map((item, index) => <tr key={`${employee.code}-${item.start_date}-${index}`}><td>{employee.code} · {employee.name}</td><td className="capitalize">{item.type.replace(/_/g, " ")}</td><td><span className={`row-status ${item.approval_status}`}>{item.approval_status}</span></td><td>{item.start_date}</td><td>{item.end_date}</td></tr>))}</tbody></table>{!(summary?.employees ?? []).some((employee) => employee.availability_exceptions.length) && <div className="table-footnote">No availability exceptions have been recorded.</div>}</div>
          </section>
        </>}

        {page === "brands" && <>
          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">01</span><div><h2>Brand and process directory</h2><p>{summary?.brands.length ?? 0} brands · {rules.length} configured process rules</p></div></div><span className="small-label">FICTIONAL DEMO DATA</span></div>
            <div className="brand-grid">{(summary?.brands ?? []).map((brand) => <article className="brand-card" key={brand.code}>
              <div className="brand-card-heading"><div><span className="brand-code">{brand.code}</span><h3>{brand.name}</h3></div><span className="brand-count">{brand.processes.length} process{brand.processes.length === 1 ? "" : "es"}</span></div>
              <div className="brand-process-list">{brand.processes.map((rule) => {
                const coverage = rule.coverage_by_weekday.filter((item) => rule.operating_weekdays.includes(item.weekday));
                const minimums = [...new Set(coverage.map((item) => item.minimum_staff))].sort((a, b) => a - b);
                const staffText = minimums.length ? minimums.map((count) => `${count} staff`).join(" / ") : "No daily coverage";
                return <div className="brand-process-row" key={rule.rule_id}>
                  <div><strong>{rule.code}</strong><small>{rule.name}</small></div>
                  <span>{rule.operating_weekdays.map((day) => weekdays[day]).join(", ") || "No operating days"}</span>
                  <span>{staffText}</span>
                  <button className="text-button" onClick={() => { setRuleId(rule.rule_id); setSelectedDays(rule.operating_weekdays); setOperateOnHolidays(rule.operate_on_public_holidays); setCanShareSameDay(rule.can_share_same_day); setMinimumStaff(""); setCoverageWeekday(rule.operating_weekdays[0] ?? 0); document.getElementById("brand-rule-form")?.scrollIntoView({ behavior: "smooth" }); }}>Edit rules</button>
                </div>;
              })}</div>
            </article>)}</div>
          </section>

          <section className="card page-card" id="brand-rule-form">
            <div className="section-heading"><div><span className="step-badge">02</span><div><h2>Update a brand/process rule</h2><p>Change its working calendar, public-holiday behavior, same-day sharing, or minimum staffing.</p></div></div></div>
            <form className="change-form page-form" onSubmit={(event) => void saveBrandRule(event)}>
              <label>Brand and process<select value={ruleId} onChange={(event) => { const nextId = Number(event.target.value); setRuleId(nextId); const match = rules.find((rule) => rule.rule_id === nextId); if (match) { setSelectedDays(match.operating_weekdays); setOperateOnHolidays(match.operate_on_public_holidays); setCanShareSameDay(match.can_share_same_day); setMinimumStaff(""); setCoverageWeekday(match.operating_weekdays[0] ?? 0); } }}><option value="">Choose a rule</option>{rules.map((rule) => <option key={rule.rule_id} value={rule.rule_id}>{rule.brand_code} · {rule.code} · {rule.name}</option>)}</select></label>
              {activeRule && <div className="rule-context"><span>Required skill: <strong>{activeRule.required_skill}</strong></span><span>Effort per person: <strong>{activeRule.effort_hours_per_employee}h</strong></span><span>Currently eligible: <strong>{activeRule.eligible_employees.filter((employee) => employee.active).length}</strong></span></div>}
              <fieldset><legend>Operating weekdays</legend><div className="weekday-pills">{weekdays.map((day, index) => <label key={day} className={selectedDays.includes(index) ? "selected" : ""}><input type="checkbox" checked={selectedDays.includes(index)} onChange={() => setSelectedDays((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index].sort())} />{day}</label>)}</div></fieldset>
              <label>Public holiday operation<select value={operateOnHolidays ? "yes" : "no"} onChange={(event) => setOperateOnHolidays(event.target.value === "yes")}><option value="no">Closed</option><option value="yes">Open</option></select></label>
              <label className="share-toggle"><input type="checkbox" checked={canShareSameDay} onChange={(event) => setCanShareSameDay(event.target.checked)} /> Multiple employees may cover this process on one day</label>
              <div className="field-row"><label>Coverage weekday<select value={coverageWeekday} onChange={(event) => setCoverageWeekday(Number(event.target.value))}>{weekdays.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label><label>New minimum staff<input type="number" min="0" max={activeEmployees.length} placeholder="Leave unchanged" value={minimumStaff} onChange={(event) => setMinimumStaff(event.target.value)} /></label></div>
              <button className="secondary-button" disabled={busy || !ruleId || selectedDays.length === 0}>{busy ? "Saving…" : "Save rule and check roster impact"}</button>
            </form>
            {changeKind === "brand rule" && renderChangeImpact()}
          </section>

          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">03</span><div><h2>Public holidays</h2><p>Configured demo dates used by each brand’s holiday rule.</p></div></div></div>
            <div className="holiday-list">{(summary?.public_holidays ?? []).map((holiday) => <div className="holiday-row" key={`${holiday.date}-${holiday.name}`}><span className="holiday-mark">✦</span><strong>{holiday.name}</strong><span>{holiday.date}</span></div>)}</div>
          </section>
        </>}
      </div>

      <aside className="side-column">
        <section className="card side-card"><div className="side-title"><span className="side-icon">✓</span><div><h3>Validation status</h3><p>Independent check of saved roster rows</p></div></div>{validation ? <><div className={`validation-banner ${validation.status}`}><strong>{validation.status.replace(/_/g, " ")}</strong><span>{validation.error_count} mandatory · {validation.warning_count} warnings</span></div><div className="mini-stat"><span>Employee-day rows</span><strong>{roster?.employee_days.length ?? 0}</strong></div><div className="mini-stat"><span>Assigned work rows</span><strong>{roster?.assignments.filter((item) => item.status === "working").length ?? 0}</strong></div></> : <p className="muted">Generate a roster to see validation results.</p>}</section>
        <section className="card side-card"><div className="side-title"><span className="side-icon purple">◷</span><div><h3>Planning rules</h3><p>Visible demo assumptions</p></div></div><div className="rule-list"><div><span>Daily capacity</span><strong>8 hours</strong></div><div><span>Weekly cap</span><strong>40 hours · 5 days</strong></div><div><span>Planning unit</span><strong>Calendar day</strong></div><div><span>Horizon</span><strong>{today.getFullYear()}–{today.getFullYear() + 5}</strong></div></div><p className="side-note">This demo does not model shift times, intraday coverage, or work outside the selected month for weekly limits.</p></section>
        {page === "roster" && <section className="card side-card help-card"><div className="side-title"><span className="side-icon orange">i</span><div><h3>How to review a change</h3><p>Manager-controlled workflow</p></div></div><ol><li>Open Employees & leave or Brands.</li><li>Choose the change and review affected dates.</li><li>Check qualified backup options.</li><li>Confirm a targeted replan to create a new draft.</li></ol></section>}
        <div className="footer-note">DataStraw Smart Roster <span>·</span> Stage 3 core review</div>
      </aside>
    </section>
  </main>;
}

export default App;
