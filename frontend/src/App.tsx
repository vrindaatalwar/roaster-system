import { useEffect, useMemo, useState, type FormEvent } from "react";

const API = import.meta.env.VITE_API_BASE_URL ?? "";
const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const today = new Date();

type Page = "roster" | "employees" | "brands";
type Role = "manager" | "pod_lead" | "employee";
type DemoIdentity = { employee_code: string; full_name: string; is_pod_lead: boolean };
type AvailabilityException = {
  id: number;
  type: string;
  approval_status: string;
  start_date: string;
  end_date: string;
  note?: string;
};
type Employee = {
  id: number;
  code: string;
  name: string;
  role: string;
  status: string;
  pod: string;
  pods: string[];
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
  pod_name: string | null;
  deadline_priority: number;
  deadline_note: string;
  required_skill: string;
  coverage_by_weekday: { weekday: number; minimum_staff: number; maximum_staff: number | null }[];
  eligible_employees: { employee_code: string; eligibility_type: string; active: boolean }[];
};
type Brand = { code: string; name: string; active: boolean; deadline_priority: number; deadline_note: string; processes: Rule[] };
type Pod = { id: number; name: string; specialization: string; lead_employee_code: string | null; members: string[]; shift: { name: string; start: string; end: string; break_start: string; break_end: string; allocation_capacity_hours: number } | null };
type Holiday = { id: number; name: string; date: string };
type Summary = { employees: Employee[]; brands: Brand[]; pods: Pod[]; process_catalog: { code: string; name: string; required_skill: string; default_effort_hours: number; enabled: boolean }[]; public_holidays: Holiday[] };
type Assignment = { assignment_id: number; date: string; employee_code: string | null; brand_code: string; process_code: string; effort_hours: number; pod_name: string | null; shift: Pod["shift"]; deadline_priority: number; available_candidates: { employee_code: string; name: string; eligibility_type: string; reason: string }[]; status: string; explanation: string };
type ValidationIssue = { severity: string; code: string; message: string; date?: string; brand_code?: string; process_code?: string; employee_code?: string };
type Validation = { roster_run_id: number; status: string; issue_count: number; error_count: number; warning_count: number; issues: ValidationIssue[] };
type Roster = {
  roster_run_id: number;
  year: number;
  month: number;
  status: string;
  generation: { required_slots: number; filled_slots: number; uncovered_slots: number; result_status: string } | null;
  assignments: Assignment[];
  employee_days: { date: string; employee_code: string; status: string; assigned_effort_hours: number; explanation: string }[];
};
type Change = {
  roster_run_id: number;
  affected_dates: string[];
  employee_code?: string;
  brand_code?: string;
  process_code?: string;
  approval_status?: string;
  availability_replan_required?: boolean;
};
type ChangeResponse = { affected_runs?: Change[]; [key: string]: unknown };
type Candidate = { employee_code: string; name: string; eligibility_type: string; projected_weekly_hours: number; reason: string };
type CandidateResult = { date: string; brand_code: string; process_code: string; candidates: Candidate[]; explanation: string; common_blockers?: Record<string, number> };
type Replacement = { date: string; brand_code: string; process_code: string; employee_code: string };
type Recommendation = {
  date: string;
  brand_code: string;
  process_code: string;
  current_employee_code: string | null;
  proposed_employee_code: string | null;
  proposed_status: string;
  action: "retained" | "reassigned" | "added" | "unfilled" | "demand_removed";
  effort_hours: number;
  explanation: string;
};
type ChangePreview = {
  source_roster_run_id: number;
  year: number;
  month: number;
  affected_dates: string[];
  result_status: string;
  required_slots: number;
  filled_slots: number;
  uncovered_slots: number;
  validation_status: string;
  unaffected_assignments_preserved: boolean;
  recommendations: Recommendation[];
};
type Comparison = { uncovered_slots: number; preserved: boolean };
type SavedRoster = { roster_run_id: number; year: number; month: number; status: string; result_status: string; required_slots: number; filled_slots: number; uncovered_slots: number };

function pageFromHash(): Page {
  const path = window.location.hash.replace(/^#\/?/, "");
  return path === "employees" || path === "brands" ? path : "roster";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const role = (localStorage.getItem("datastraw-role") as Role | null) ?? "manager";
  const employeeCode = localStorage.getItem("datastraw-employee-code") ?? "";
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-App-Role": role,
      ...(role === "manager" ? {} : { "X-Employee-Code": employeeCode }),
      ...init?.headers,
    },
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
  const [role] = useState<Role>((localStorage.getItem("datastraw-role") as Role | null) ?? "manager");
  const [actorCode, setActorCode] = useState(localStorage.getItem("datastraw-employee-code") ?? "");
  const [identities, setIdentities] = useState<DemoIdentity[]>([]);
  const [identitiesReady, setIdentitiesReady] = useState(false);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [roster, setRoster] = useState<Roster | null>(null);
  const [savedRosters, setSavedRosters] = useState<SavedRoster[]>([]);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [employeeCode, setEmployeeCode] = useState("");
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employeeEditCode, setEmployeeEditCode] = useState("");
  const [employeeForm, setEmployeeForm] = useState({ employee_code: "", full_name: "", role_title: "Operations Associate", pod_name: "", pod_names: [] as string[], is_pod_lead: false, status: "active", skills: "", weekly_off_weekdays: "5,6", daily_capacity_hours: "6", max_days_per_week: "5", max_hours_per_week: "40", minimum_days_off_per_week: "2" });
  const [podName, setPodName] = useState("");
  const [podSpecialization, setPodSpecialization] = useState("");
  const [podLead, setPodLead] = useState("");
  const [podMembers, setPodMembers] = useState<string[]>([]);
  const [podEditId, setPodEditId] = useState<number | null>(null);
  const [podShift, setPodShift] = useState({ shift_name: "Day Window", shift_start: "09:00", shift_end: "18:00", break_start: "13:00", break_end: "14:00", allocation_capacity_hours: "6" });
  const [eligibilityEmployee, setEligibilityEmployee] = useState("");
  const [eligibilityType, setEligibilityType] = useState("backup");
  const [holidayName, setHolidayName] = useState("");
  const [holidayDate, setHolidayDate] = useState("");
  const [newBrandCode, setNewBrandCode] = useState("");
  const [newBrandName, setNewBrandName] = useState("");
  const [brandPriority, setBrandPriority] = useState("3");
  const [brandDeadlineNote, setBrandDeadlineNote] = useState("");
  const [editingBrandCode, setEditingBrandCode] = useState("");
  const [newProcessCode, setNewProcessCode] = useState("");
  const [newProcessName, setNewProcessName] = useState("");
  const [newProcessSkill, setNewProcessSkill] = useState("");
  const [newProcessHours, setNewProcessHours] = useState("6");
  const [editingProcessCode, setEditingProcessCode] = useState("");
  const [newRuleBrand, setNewRuleBrand] = useState("");
  const [newRuleProcess, setNewRuleProcess] = useState("");
  const [newRuleDays, setNewRuleDays] = useState<number[]>([0, 1, 2, 3, 4]);
  const [newRuleMinimum, setNewRuleMinimum] = useState("1");
  const [newRulePod, setNewRulePod] = useState("");
  const [rulePod, setRulePod] = useState("");
  const [ruleEffort, setRuleEffort] = useState("6");
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
  const [changePreview, setChangePreview] = useState<ChangePreview | null>(null);
  const [candidates, setCandidates] = useState<CandidateResult[]>([]);
  const [selectedReplacement, setSelectedReplacement] = useState<Replacement | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [showRetained, setShowRetained] = useState(false);
  const [brandFilter, setBrandFilter] = useState("all");
  const [processFilter, setProcessFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState("");
  const [podFilter, setPodFilter] = useState("all");
  const [assignmentEmployeeFilter, setAssignmentEmployeeFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    request<DemoIdentity[]>("/api/v1/demo-identities").then((options) => {
      setIdentities(options);
      setIdentitiesReady(true);
      if (role !== "manager" && !options.some((item) => item.employee_code === actorCode
        && (role !== "pod_lead" || item.is_pod_lead))) {
        const preferred = options.find((item) => role !== "pod_lead" || item.is_pod_lead);
        if (preferred) {
          localStorage.setItem("datastraw-employee-code", preferred.employee_code);
          setActorCode(preferred.employee_code);
        }
      }
    }).catch((reason: Error) => setError(reason.message));
  }, [role]);

  useEffect(() => {
    if (role !== "manager" && (!actorCode || !identitiesReady)) return;
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
        setRulePod(firstRule.pod_name ?? "");
        setRuleEffort(String(firstRule.effort_hours_per_employee));
        setNewRulePod(data.pods[0]?.name ?? "");
      }
      request<SavedRoster[]>("/api/v1/rosters")
        .then(async (runs) => {
          setSavedRosters(runs);
          const selectedMonthRun = runs.find((run) => run.year === today.getFullYear() && run.month === today.getMonth() + 1);
          if (selectedMonthRun) await loadRoster(selectedMonthRun.roster_run_id);
        })
        .catch((reason: Error) => setError(reason.message));
    }).catch((reason: Error) => setError(reason.message));
  }, [role, actorCode, identitiesReady]);

  const rules = useMemo(() => summary?.brands.flatMap((brand) => brand.processes.map((rule) => ({
    ...rule, brand_code: brand.code, brand_name: brand.name,
  }))) ?? [], [summary]);
  const activeRule = rules.find((rule) => rule.rule_id === ruleId);
  const activeEmployees = summary?.employees.filter((employee) => employee.status === "active") ?? [];
  const visibleProcesses = [...new Set(rules.filter((rule) => brandFilter === "all" || rule.brand_code === brandFilter).map((rule) => rule.code))];
  const coverageCounts = useMemo(() => {
    const counts = new Map<string, { filled: number; required: number }>();
    for (const item of roster?.assignments ?? []) {
      if ((brandFilter !== "all" && item.brand_code !== brandFilter)
        || (processFilter !== "all" && item.process_code !== processFilter)
        || (podFilter !== "all" && item.pod_name !== podFilter)
        || (dateFilter && item.date !== dateFilter)) continue;
      const key = `${item.date}|${item.brand_code}|${item.process_code}`;
      const count = counts.get(key) ?? { filled: 0, required: 0 };
      count.required += 1;
      if (item.status === "working") count.filled += 1;
      counts.set(key, count);
    }
    return counts;
  }, [roster, brandFilter, processFilter, podFilter, dateFilter]);
  const visibleAssignments = useMemo(() => (roster?.assignments ?? []).filter((item) =>
    (brandFilter === "all" || item.brand_code === brandFilter)
    && (processFilter === "all" || item.process_code === processFilter)
    && (assignmentEmployeeFilter === "all" || item.employee_code === assignmentEmployeeFilter)
    && (podFilter === "all" || item.pod_name === podFilter)
    && (!dateFilter || item.date === dateFilter)), [roster, summary, brandFilter, processFilter, dateFilter, podFilter, assignmentEmployeeFilter]);
  const coverageLabel = (item: Assignment) => {
    const count = coverageCounts.get(`${item.date}|${item.brand_code}|${item.process_code}`);
    return count ? `${count.filled}/${count.required}` : "—";
  };
  const visibleEmployees = useMemo(() => (summary?.employees ?? []).filter((employee) => {
    const query = employeeSearch.trim().toLowerCase();
    return !query || [employee.code, employee.name, employee.role, ...employee.pods].some((value) => value.toLowerCase().includes(query));
  }), [summary, employeeSearch]);

  function navigate(nextPage: Page) {
    if (page === nextPage) return;
    window.location.hash = `/${nextPage}`;
    setPage(nextPage);
    setNotice("");
    setError("");
  }

  function changeDemoRole(nextRole: Role) {
    const current = identities.find((item) => item.employee_code === actorCode);
    const nextIdentity = nextRole === "manager" ? null
      : nextRole === "pod_lead"
        ? (current?.is_pod_lead ? current : identities.find((item) => item.is_pod_lead))
        : (current ?? identities[0]);
    localStorage.setItem("datastraw-role", nextRole);
    if (nextIdentity) localStorage.setItem("datastraw-employee-code", nextIdentity.employee_code);
    window.location.reload();
  }

  function changeDemoIdentity(code: string) {
    localStorage.setItem("datastraw-employee-code", code);
    window.location.reload();
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

  async function refreshSummary() {
    const data = await request<Summary>("/api/v1/seed-summary");
    setSummary(data);
  }

  async function refreshSavedRosters() {
    setSavedRosters(await request<SavedRoster[]>("/api/v1/rosters"));
  }

  function startEditingEmployee(code: string) {
    setEmployeeEditCode(code);
    const employee = summary?.employees.find((item) => item.code === code);
    if (!employee) {
      setEmployeeForm({ employee_code: "", full_name: "", role_title: "Operations Associate", pod_name: summary?.pods[0]?.name ?? "", pod_names: summary?.pods[0] ? [summary.pods[0].name] : [], is_pod_lead: false, status: "active", skills: "", weekly_off_weekdays: "5,6", daily_capacity_hours: "6", max_days_per_week: "5", max_hours_per_week: "40", minimum_days_off_per_week: "2" });
      return;
    }
    setEmployeeForm({
      employee_code: employee.code, full_name: employee.name, role_title: employee.role,
      pod_name: employee.pod, pod_names: employee.pods, is_pod_lead: employee.is_pod_lead, status: employee.status,
      skills: employee.skills.join(", "), weekly_off_weekdays: employee.weekly_off_weekdays.join(","),
      daily_capacity_hours: String(employee.contract.daily_capacity_hours),
      max_days_per_week: String(employee.contract.max_days_per_week),
      max_hours_per_week: String(employee.contract.max_hours_per_week),
      minimum_days_off_per_week: String(employee.contract.minimum_days_off_per_week),
    });
  }

  async function runAction(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Something went wrong."); }
    finally { setBusy(false); }
  }

  function changeFromResponse(response: ChangeResponse, kind: string): Change | null {
    const runs = response.affected_runs ?? [];
    const target = runs.find((run) => run.roster_run_id === roster?.roster_run_id) ?? runs[0];
    const scopedTarget = target ? {
      ...target,
      employee_code: typeof response.employee_code === "string" ? response.employee_code : undefined,
      brand_code: typeof response.brand_code === "string" ? response.brand_code : undefined,
      process_code: typeof response.process_code === "string" ? response.process_code : undefined,
      approval_status: typeof response.approval_status === "string" ? response.approval_status : undefined,
    } : null;
    setChange(scopedTarget);
    setChangeKind(kind);
    setChangePreview(null);
    setShowRetained(false);
    setCandidates([]);
    setSelectedReplacement(null);
    if (!target) {
      setNotice("The setting was saved. No generated roster overlaps the selected dates yet; generate one for those dates to see its impact.");
      return null;
    }
    setNotice(`Change saved. Calculating a proposed allocation for ${target.affected_dates.length} affected date(s) in roster ${target.roster_run_id}.`);
    return scopedTarget;
  }

  async function loadProposal(target: Change, replacement: Replacement | null = null) {
    const proposal = await request<ChangePreview>(`/api/v1/rosters/${target.roster_run_id}/replan-preview`, {
      method: "POST",
      body: JSON.stringify({ affected_dates: target.affected_dates, replacement }),
    });
    setChangePreview(proposal);
    setNotice(proposal.uncovered_slots === 0
      ? `Proposed allocation covers all ${proposal.required_slots} affected coverage slot(s). Review it, then apply the new draft if it looks right.`
      : `Proposed allocation fills ${proposal.filled_slots} of ${proposal.required_slots} affected coverage slot(s). Review the remaining slots and available options.`);
    return proposal;
  }

  async function loadCandidates(target: Change, employee: string) {
    if (!roster || roster.roster_run_id !== target.roster_run_id) return;
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

  async function loadGapCandidates(target: Change, proposal: ChangePreview) {
    const gaps = proposal.recommendations.filter((item) => item.action === "unfilled");
    const unique = new Map<string, { date: string; brand_code: string; process_code: string }>();
    gaps.forEach((gap) => unique.set(`${gap.date}-${gap.brand_code}-${gap.process_code}`, { date: gap.date, brand_code: gap.brand_code, process_code: gap.process_code }));
    const results = await Promise.all([...unique.values()].map((item) => {
      const query = new URLSearchParams({ assignment_date: item.date, brand_code: item.brand_code, process_code: item.process_code });
      return request<CandidateResult>(`/api/v1/rosters/${target.roster_run_id}/replacement-candidates?${query}`);
    }));
    setCandidates(results);
  }

  async function chooseCandidate(result: CandidateResult, candidate: Candidate) {
    if (!change) return;
    const alreadySelected = selectedReplacement?.date === result.date
      && selectedReplacement.brand_code === result.brand_code
      && selectedReplacement.process_code === result.process_code
      && selectedReplacement.employee_code === candidate.employee_code;
    const replacement: Replacement | null = alreadySelected ? null : {
      date: result.date, brand_code: result.brand_code, process_code: result.process_code,
      employee_code: candidate.employee_code,
    };
    await runAction(async () => {
      setSelectedReplacement(replacement);
      await loadProposal(change, replacement);
    });
  }

  async function saveAvailability(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const result = await request<ChangeResponse>("/api/v1/availability-exceptions", {
        method: "POST",
        body: JSON.stringify({ employee_code: employeeCode, exception_type: exceptionType,
          approval_status: approvalStatus, start_date: startDate, end_date: endDate }),
      });
      const target = changeFromResponse(result, "availability");
      if (target && approvalStatus === "approved") {
        await loadProposal(target);
        await loadCandidates(target, employeeCode);
      }
      await refreshSummary();
    });
  }

  async function changeAvailabilityStatus(item: AvailabilityException, employee: Employee, next: string) {
    await runAction(async () => {
      const result = await request<ChangeResponse>(`/api/v1/availability-exceptions/${item.id}`, {
        method: "PATCH", body: JSON.stringify({ approval_status: next }),
      });
      const target = changeFromResponse(result, "availability");
      const replanRequired = Boolean(target && (item.approval_status === "approved" || next === "approved"));
      if (target && replanRequired) {
        const actionable = { ...target, availability_replan_required: true };
        setChange(actionable);
        await loadProposal(actionable);
        await loadCandidates(target, employee.code);
      } else if (target) {
        setChange({ ...target, availability_replan_required: false });
        setNotice(next === "pending" ? "Request marked pending. It remains a warning and does not block scheduling." : "Request declined. The saved roster remains unchanged.");
      }
      await refreshSummary();
    });
  }

  async function saveEmployee(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const body = {
        ...employeeForm,
        skills: employeeForm.skills.split(",").map((value) => value.trim()).filter(Boolean),
        weekly_off_weekdays: employeeForm.weekly_off_weekdays.split(",").map((value) => Number(value.trim())).filter(Number.isInteger),
        daily_capacity_hours: Number(employeeForm.daily_capacity_hours),
        max_days_per_week: Number(employeeForm.max_days_per_week),
        max_hours_per_week: Number(employeeForm.max_hours_per_week),
        minimum_days_off_per_week: Number(employeeForm.minimum_days_off_per_week),
      };
      const editing = Boolean(employeeEditCode);
      await request(editing ? `/api/v1/employees/${encodeURIComponent(employeeEditCode)}` : "/api/v1/employees", {
        method: editing ? "PATCH" : "POST", body: JSON.stringify(body),
      });
      await refreshSummary();
      setEmployeeEditCode(body.employee_code);
      setNotice(editing ? "Employee details saved." : "Employee added to the team.");
    });
  }

  async function savePod(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const body = { name: podName, specialization: podSpecialization, lead_employee_code: podLead || null,
        member_employee_codes: podMembers,
        ...podShift, allocation_capacity_hours: Number(podShift.allocation_capacity_hours) };
      await request(podEditId ? `/api/v1/pods/${podEditId}` : "/api/v1/pods", {
        method: podEditId ? "PATCH" : "POST", body: JSON.stringify(body),
      });
      setPodEditId(null); setPodName(""); setPodSpecialization(""); setPodLead(""); setPodMembers([]);
      await refreshSummary(); setNotice("Pod details saved.");
    });
  }

  async function saveEligibility() {
    if (!activeRule) return;
    await runAction(async () => {
      await request("/api/v1/eligibility", { method: "PUT", body: JSON.stringify({
        employee_code: eligibilityEmployee, rule_id: activeRule.rule_id, eligibility_type: eligibilityType, active: true,
      }) });
      await refreshSummary(); setNotice("Employee eligibility saved for this brand and process.");
    });
  }

  async function removeEligibility(rule: Rule, employeeCodeToRemove: string) {
    await runAction(async () => {
      await request("/api/v1/eligibility", { method: "PUT", body: JSON.stringify({
        employee_code: employeeCodeToRemove, rule_id: rule.rule_id, eligibility_type: "backup", active: false,
      }) });
      await refreshSummary(); setNotice(`${employeeCodeToRemove} is no longer eligible for ${rule.code}.`);
    });
  }

  async function toggleBrand(brand: Brand) {
    await runAction(async () => {
      await request(`/api/v1/brands/${encodeURIComponent(brand.code)}`, { method: "PATCH", body: JSON.stringify({ code: brand.code, name: brand.name, active: !brand.active, deadline_priority: brand.deadline_priority, deadline_note: brand.deadline_note }) });
      await refreshSummary(); setNotice(`${brand.code} ${brand.active ? "archived" : "reactivated"}.`);
    });
  }

  async function saveNewBrand(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const existing = Boolean(editingBrandCode);
      await request(existing ? `/api/v1/brands/${encodeURIComponent(editingBrandCode)}` : "/api/v1/brands", { method: existing ? "PATCH" : "POST", body: JSON.stringify({ code: newBrandCode, name: newBrandName, active: true, deadline_priority: Number(brandPriority), deadline_note: brandDeadlineNote }) });
      setNewBrandCode(""); setNewBrandName(""); setEditingBrandCode(""); setBrandPriority("3"); setBrandDeadlineNote(""); await refreshSummary(); setNotice(existing ? "Brand details saved." : "Brand added.");
    });
  }

  async function saveNewProcess(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const existing = Boolean(editingProcessCode);
      await request(existing ? `/api/v1/processes/${encodeURIComponent(editingProcessCode)}` : "/api/v1/processes", { method: existing ? "PATCH" : "POST", body: JSON.stringify({ code: newProcessCode, name: newProcessName, required_skill: newProcessSkill, default_effort_hours: Number(newProcessHours), description: `${newProcessName} operations`, enabled: true }) });
      setNewProcessCode(""); setNewProcessName(""); setNewProcessSkill(""); setEditingProcessCode(""); await refreshSummary(); setNotice(existing ? "Process details saved." : "Process and its required skill added.");
    });
  }

  async function saveNewRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const minimum = Number(newRuleMinimum);
      await request("/api/v1/brand-process-rules", { method: "POST", body: JSON.stringify({
        brand_code: newRuleBrand, process_code: newRuleProcess, operating_weekdays: newRuleDays,
        operate_on_public_holidays: false, effort_hours_per_employee: Number(newProcessHours),
        can_share_same_day: false, pod_name: newRulePod,
        minimum_by_weekday: weekdays.map((_, day) => newRuleDays.includes(day) ? minimum : 0),
        maximum_by_weekday: Array(7).fill(null),
      }) });
      await refreshSummary(); setNotice("Process rule added to the brand. Add qualified employees to its eligible list before generating a roster.");
    });
  }

  async function addHoliday(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      await request("/api/v1/public-holidays", { method: "POST", body: JSON.stringify({ name: holidayName, date: holidayDate }) });
      setHolidayName(""); setHolidayDate(""); await refreshSummary(); setNotice("Public holiday added.");
    });
  }

  async function editAssignment(item: Assignment, employeeCode: string) {
    if (!roster || item.assignment_id === undefined) return;
    await runAction(async () => {
      await request(`/api/v1/rosters/${roster.roster_run_id}/assignments/${item.assignment_id}`, {
        method: "PATCH", body: JSON.stringify({ employee_code: employeeCode || null }),
      });
      await loadRoster(roster.roster_run_id);
      await refreshSavedRosters();
      setNotice("Assignment saved. The roster has been checked again; review the updated coverage panel.");
    });
  }

  async function publishRoster() {
    if (!roster) return;
    await runAction(async () => {
      await request(`/api/v1/rosters/${roster.roster_run_id}/publish`, { method: "POST" });
      await loadRoster(roster.roster_run_id);
      await refreshSavedRosters();
      setNotice("Roster published after a fresh validation check.");
    });
  }

  async function saveBrandRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ruleId) return;
    await runAction(async () => {
      const body: Record<string, unknown> = {
        operating_weekdays: selectedDays,
        operate_on_public_holidays: operateOnHolidays,
        can_share_same_day: canShareSameDay,
        pod_name: rulePod,
        effort_hours_per_employee: Number(ruleEffort),
      };
      if (minimumStaff !== "") {
        body.weekday = coverageWeekday;
        body.minimum_staff = Number(minimumStaff);
      }
      const result = await request<ChangeResponse>(`/api/v1/brand-process-rules/${ruleId}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
      const target = changeFromResponse(result, "brand rule");
      if (target) {
        const proposal = await loadProposal(target);
        await loadGapCandidates(target, proposal);
      }
      const updated = await request<Summary>("/api/v1/seed-summary");
      setSummary(updated);
      const updatedRule = updated.brands.flatMap((brand) => brand.processes).find((item) => item.rule_id === ruleId);
      if (updatedRule) { setSelectedDays(updatedRule.operating_weekdays); setRulePod(updatedRule.pod_name ?? ""); }
    });
  }

  async function confirmReplan() {
    if (!change || !changePreview) return;
    await runAction(async () => {
      const result = await request<{ roster_run_id: number; uncovered_slots: number; unaffected_assignments_preserved: boolean }>(
        `/api/v1/rosters/${change.roster_run_id}/replan`, {
          method: "POST",
          body: JSON.stringify({ affected_dates: change.affected_dates, replacement: selectedReplacement }),
        },
      );
      setComparison({ uncovered_slots: result.uncovered_slots, preserved: result.unaffected_assignments_preserved });
      await loadRoster(result.roster_run_id);
      await refreshSavedRosters();
      setChange(null);
      setChangePreview(null);
      setSelectedReplacement(null);
      setNotice(result.uncovered_slots === 0
        ? `Proposed roster applied as draft #${result.roster_run_id}. All required coverage is allocated.`
        : `Proposed roster applied as draft #${result.roster_run_id}. ${result.uncovered_slots} slot(s) still need a manager allocation.`);
      navigate("roster");
    });
  }

  function renderChangeImpact() {
    if (!change || !changeKind || (page === "employees" && changeKind !== "availability")
      || (page === "brands" && changeKind !== "brand rule")) return null;

    const availabilityBlocksScheduling = changeKind !== "availability" || change.approval_status === "approved" || change.availability_replan_required === true;
    const availabilityNotice = changeKind === "availability" && change.approval_status === "pending" && !change.availability_replan_required
      ? "This request is pending. The current roster stays in place while you review it; it does not block scheduling."
      : changeKind === "availability" && change.approval_status === "pending"
        ? "This request is now pending. Review the proposed allocation that removes its previous approved block."
      : changeKind === "availability" && change.approval_status === "declined"
        ? change.availability_replan_required
          ? "This request is declined. Review the proposed allocation now that its approved block has been removed."
          : "This request was declined, so no new allocation is needed."
        : "The current roster stays as the baseline. Review the proposed allocation, then apply it to create a new draft.";
    const recommendationLabel: Record<Recommendation["action"], string> = {
      retained: "Keep",
      reassigned: "Reassign",
      added: "Add coverage",
      unfilled: "Needs allocation",
      demand_removed: "No longer required",
    };
    const visibleRecommendations = changePreview?.recommendations.filter((item) =>
      showRetained || item.action !== "retained") ?? [];

    return <div className="change-impact">
      <div className="impact-heading">
        <div>
          <span className="impact-label">PROPOSED CHANGE · {changeKind.toUpperCase()}</span>
          <h3>{change.affected_dates.length} affected date(s)</h3>
          <p>{availabilityNotice}</p>
        </div>
        <button className="primary-button" disabled={busy || !availabilityBlocksScheduling || !changePreview} onClick={() => void confirmReplan()}>
          {change.approval_status === "pending" && !change.availability_replan_required ? "Waiting for approval"
            : change.approval_status === "declined" && !change.availability_replan_required ? "No replan needed"
            : !changePreview ? "Preparing proposal…"
              : selectedReplacement ? `Apply plan with ${selectedReplacement.employee_code} →` : "Apply proposed roster →"}
        </button>
      </div>
      {changePreview && <>
        <div className="comparison-strip impact-comparison">
          <strong>{change.brand_code ? `${change.brand_code} · ${change.process_code}` : change.employee_code ? `${change.employee_code}'s proposed roster` : "Proposed roster"}</strong>
          <span>{changePreview.required_slots} required</span>
          <span>{changePreview.filled_slots} allocated</span>
          <span>{changePreview.uncovered_slots} still need allocation</span>
          <span>Other dates stay as they are</span>
        </div>
        {changePreview.recommendations.length > 0 && <div className="recommendation-list">
          <div className="recommendation-heading"><strong>Recommended allocation changes</strong><button className="text-button" onClick={() => setShowRetained((value) => !value)}>{showRetained ? "Hide unchanged assignments" : `Show all ${changePreview.recommendations.length} assignments`}</button></div>
          {visibleRecommendations.map((item, index) => <div className={`recommendation-row ${item.action}`} key={`${item.date}-${item.brand_code}-${item.process_code}-${index}`}>
            <span className="recommendation-date">{new Date(`${item.date}T12:00:00`).toLocaleDateString("en", { month: "short", day: "numeric", weekday: "short" })}</span>
            <strong>{item.brand_code} · {item.process_code}</strong>
            <span>{item.current_employee_code ?? "—"}<span className="recommendation-arrow">→</span>{item.proposed_employee_code ?? (item.proposed_status === "not_required" ? "Not required" : "Unfilled")}</span>
            <span className="recommendation-action">{recommendationLabel[item.action]}</span>
            <small>{item.explanation}</small>
          </div>)}
          {visibleRecommendations.length === 0 && <p className="muted impact-empty">The solver can keep the current assignments and still meet demand.</p>}
        </div>}
      </>}
      {candidates.length > 0 ? <div className="candidate-grid">{candidates.map((result) => <div className="candidate-card" key={`${result.date}-${result.brand_code}-${result.process_code}`}>
        <strong>{result.brand_code} · {result.process_code}<span>{new Date(`${result.date}T12:00:00`).toLocaleDateString("en", { month: "short", day: "numeric" })}</span></strong>
        {result.candidates.length ? result.candidates.map((candidate) => {
          const selected = selectedReplacement?.date === result.date && selectedReplacement.brand_code === result.brand_code
            && selectedReplacement.process_code === result.process_code && selectedReplacement.employee_code === candidate.employee_code;
          return <button className={`candidate-row ${selected ? "candidate-selected" : ""}`} key={candidate.employee_code}
            disabled={busy} onClick={() => void chooseCandidate(result, candidate)}>
            <span>{candidate.employee_code} · {candidate.name}</span>
            <small>{candidate.eligibility_type} · {candidate.projected_weekly_hours}h this week {selected ? "· in proposal" : "· use in proposal"}</small>
          </button>;
        }) : <p className="no-candidate">{result.explanation}{result.common_blockers && Object.keys(result.common_blockers).length > 0 ? ` ${Object.entries(result.common_blockers).map(([reason, count]) => `${count} ${reason}`).join(" · ")}.` : ""}</p>}
      </div>)}</div> : changePreview?.uncovered_slots ? <p className="muted impact-empty">No qualified, available employee can take one or more of these slots. The recommendation list gives the reason and available alternatives.</p>
          : changePreview ? <p className="muted impact-empty">The proposed plan meets demand on the affected dates. Review the assignments above and apply the plan when ready.</p>
            : change.approval_status === "pending" ? <p className="muted impact-empty">No roster change is proposed unless this request is approved.</p>
              : change.approval_status === "declined" ? <p className="muted impact-empty">The declined request leaves the current roster unchanged.</p> : null}
    </div>;
  }

  const pageInfo: Record<Page, { title: string; description: string }> = {
    roster: { title: "Roster", description: "Generate a monthly plan and review coverage, assignments, and conflicts." },
    employees: { title: "Employees & leave", description: "Review team members and record availability changes for leave, comp-off, or unavailability." },
    brands: { title: "Brands", description: "Review brand processes and update operating calendars and coverage rules." },
  };

  if (role !== "manager") {
    return <ReadOnlyDashboard role={role} actorCode={actorCode} identities={identities}
      summary={summary} roster={roster} savedRosters={savedRosters} validation={validation}
      onRoleChange={changeDemoRole} onIdentityChange={changeDemoIdentity}
      onLoadRoster={(id) => void loadRoster(id)} />;
  }

  return <main className="page-shell">
    <header className="topbar">
      <div className="brand-lockup"><span className="brand-mark">DS</span><div><strong>DataStraw</strong><small>Smart Roster</small></div></div>
      <div className="topbar-right role-switcher"><span className="live-dot" /> Demo access
        <label>View as<select value={role} onChange={(event) => changeDemoRole(event.target.value as Role)}>
          <option value="manager">Manager</option><option value="pod_lead">Pod lead</option><option value="employee">Employee</option>
        </select></label>
      </div>
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
      <div className="hero-meta"><span className="assumption-tag">DEMO ASSUMPTIONS</span><span>Two pod windows · 6h allocatable · 40h weekly cap</span></div>
    </section>

    {error && <div className="alert error-alert"><strong>Couldn’t complete that action</strong><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss">×</button></div>}
    {notice && <div className="alert notice-alert"><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss">×</button></div>}
    {comparison && <div className="comparison-strip"><strong>Proposed roster applied</strong><span>{comparison.uncovered_slots === 0 ? "All required coverage is allocated." : `${comparison.uncovered_slots} slot(s) still need a manager allocation.`}</span><span>{comparison.preserved ? "Assignments on other dates stayed unchanged." : "Some assignments on other dates changed."}</span><button onClick={() => setComparison(null)} aria-label="Dismiss">×</button></div>}

    <section className="workspace-grid">
      <div className="main-column">
        {page === "roster" && <>
          <section className="card generate-card">
            <div className="section-heading"><div><span className="step-badge">01</span><div><h2>Generate a roster</h2><p>Choose the month to plan for the active team.</p></div></div><span className="small-label">DRAFT ONLY</span></div>
            <form className="generate-form" onSubmit={(event) => { event.preventDefault(); void runAction(async () => {
              const result = await request<{ roster_run_id: number; result_status: string }>("/api/v1/rosters/generate", { method: "POST", body: JSON.stringify({ year, month }) });
              await loadRoster(result.roster_run_id); setChange(null); setComparison(null); setChangePreview(null); setSelectedReplacement(null);
              await refreshSavedRosters();
              setNotice(`Roster ${result.roster_run_id} generated as a ${result.result_status} draft.`);
            }); }}>
              <label>Year<input type="number" min={today.getFullYear()} max={today.getFullYear() + 5} value={year} onChange={(event) => setYear(Number(event.target.value))} required /></label>
              <label>Month<select value={month} onChange={(event) => setMonth(Number(event.target.value))}>{Array.from({ length: 12 }, (_, index) => <option key={index + 1} value={index + 1}>{new Date(2026, index, 1).toLocaleString("en", { month: "long" })}</option>)}</select></label>
              <label>Open saved draft<select value={roster?.roster_run_id ?? ""} onChange={(event) => { const id = Number(event.target.value); if (id) void runAction(() => loadRoster(id)); }}><option value="">Choose a roster</option>{savedRosters.map((item) => <option key={item.roster_run_id} value={item.roster_run_id}>Draft #{item.roster_run_id} · {item.month}/{item.year} · {item.filled_slots}/{item.required_slots} covered</option>)}</select></label>
              <button className="primary-button" disabled={busy}>{busy ? "Working…" : roster ? "Generate new draft" : "Generate roster"}<span>→</span></button>
            </form>
          </section>

          {roster ? <>
            <section className="card roster-card">
              <div className="section-heading roster-heading"><div><span className="step-badge">02</span><div><h2>Roster review <span className="draft-chip">{roster.status === "published" ? "Published" : `Draft #${roster.roster_run_id}`}</span></h2><p>{new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })} · All configured brands and processes</p></div></div><span className={`status-pill ${validation?.status ?? ""}`}>{validation?.status?.replace(/_/g, " ") ?? "checking"}</span>{roster.status === "draft" && <button className="primary-button" disabled={busy || !validation || validation.error_count > 0} title={validation?.error_count ? "Resolve coverage gaps and hard-rule conflicts before publishing." : "Publish this validated roster."} onClick={() => void publishRoster()}>{busy ? "Checking…" : "Publish roster"}</button>}</div>
              <div className="metric-grid">
                <div className="metric"><span>Required slots</span><strong>{roster.generation?.required_slots ?? 0}</strong></div>
                <div className="metric"><span>Covered</span><strong className="good-text">{roster.generation?.filled_slots ?? 0}</strong></div>
                <div className="metric"><span>Uncovered</span><strong className="risk-text">{roster.generation?.uncovered_slots ?? 0}</strong></div>
                <div className="metric"><span>Coverage gaps & conflicts</span><strong className={validation?.error_count ? "risk-text" : "good-text"}>{validation?.error_count ?? 0}</strong></div>
              </div>
              <div className="coverage-panel">
                <div className="coverage-title"><div><h3>Coverage and conflicts</h3><span>{validation?.error_count ?? 0} coverage gaps or rule conflicts · {validation?.warning_count ?? 0} warnings</span></div><button className="text-button" disabled={busy} onClick={() => void runAction(async () => { await loadRoster(roster.roster_run_id); setNotice("Roster validation refreshed against the latest saved inputs."); })}>Refresh coverage ↻</button></div>
                <div className="issue-list">
                  {(validation?.issues ?? []).filter((issue) => issue.severity === "error").slice(0, 5).map((issue, index) => <div className="issue-row" key={`${issue.code}-${issue.date}-${index}`}><span className="issue-icon">!</span><span><strong>{issue.brand_code && `${issue.brand_code} ${issue.process_code} · `}{issue.date ?? "Roster"}</strong><small>{issue.message}</small></span></div>)}
                  {validation?.error_count === 0 && <div className="empty-success">No mandatory coverage gaps or hard-rule violations found.</div>}
                  {(validation?.issues ?? []).filter((issue) => issue.severity === "warning").slice(0, 2).map((issue, index) => <div className="issue-row warning-row" key={`${issue.code}-${index}`}><span className="issue-icon">i</span><span><strong>{issue.employee_code ?? "Preference"}</strong><small>{issue.message}</small></span></div>)}
                </div>
              </div>
              <div className="table-toolbar"><div><h3>Daily assignments</h3><span>{visibleAssignments.length} rows shown · edit a draft assignment and review validation immediately</span></div><div className="filters"><input aria-label="Filter date" type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /><select value={podFilter} onChange={(event) => setPodFilter(event.target.value)}><option value="all">All pods</option>{summary?.pods.map((pod) => <option key={pod.id} value={pod.name}>{pod.name}{pod.lead_employee_code ? ` · lead ${pod.lead_employee_code}` : " · no lead"}</option>)}</select><select value={brandFilter} onChange={(event) => { setBrandFilter(event.target.value); setProcessFilter("all"); }}><option value="all">All brands</option>{summary?.brands.map((brand) => <option key={brand.code} value={brand.code}>{brand.code}</option>)}</select><select value={processFilter} onChange={(event) => setProcessFilter(event.target.value)}><option value="all">All processes</option>{visibleProcesses.map((process) => <option key={process} value={process}>{process}</option>)}</select><select value={assignmentEmployeeFilter} onChange={(event) => setAssignmentEmployeeFilter(event.target.value)}><option value="all">All employees</option>{activeEmployees.map((employee) => <option key={employee.code} value={employee.code}>{employee.code}</option>)}</select></div></div>
              <div className="table-wrap"><table><thead><tr><th>Date</th><th>Brand</th><th>Process</th><th>Pod / work window</th><th>Employee</th><th>Coverage</th><th>Hours</th><th>Status</th><th>Allocation reason / manager edit</th></tr></thead><tbody>{visibleAssignments.map((item) => <tr key={item.assignment_id}><td>{new Date(`${item.date}T12:00:00`).toLocaleDateString("en", { day: "2-digit", month: "short", weekday: "short" })}</td><td>{item.brand_code}<small className="table-subtext">Priority {item.deadline_priority}/5</small></td><td>{item.process_code}</td><td>{item.pod_name ?? "Legacy day slot"}{item.shift && <small className="table-subtext">{item.shift.name} · {item.shift.start}–{item.shift.end} · lunch {item.shift.break_start}–{item.shift.break_end}</small>}</td><td>{item.employee_code ?? <span className="unassigned">Uncovered</span>}</td><td>{coverageLabel(item)}</td><td>{item.effort_hours}h</td><td><span className={`row-status ${item.status}`}>{item.status}</span></td><td><small className="assignment-explanation">{item.explanation}</small>{roster.status === "draft" && <div className="inline-assignment-edit"><select aria-label={`Assign ${item.brand_code} ${item.process_code} on ${item.date}`} value={item.employee_code ?? ""} onChange={(event) => void editAssignment(item, event.target.value)} disabled={busy}><option value="">Unassigned</option>{item.employee_code && !item.available_candidates.some((employee) => employee.employee_code === item.employee_code) && <option value={item.employee_code} disabled>{item.employee_code} · current (blocked by rule)</option>}{item.available_candidates.map((employee) => <option key={employee.employee_code} value={employee.employee_code}>{employee.employee_code} · {employee.name} · {employee.eligibility_type}</option>)}</select><small className="table-subtext">Only qualified, eligible, available employees with capacity in this pod window are listed.</small></div>}</td></tr>)}</tbody></table></div>
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
            {summary ? <div className="table-wrap page-table"><table><thead><tr><th>Employee</th><th>Role</th><th>Pods</th><th>Skills</th><th>Brand/process eligibility</th><th>Weekly offs</th><th>Status</th><th /></tr></thead><tbody>{visibleEmployees.map((employee) => <tr key={employee.code}><td><strong>{employee.code} · {employee.name}</strong>{employee.is_pod_lead && <small className="table-subtext">Pod lead</small>}</td><td>{employee.role}</td><td>{employee.pods.join(", ")}<small className="table-subtext">Primary: {employee.pod}</small></td><td><div className="tag-list">{employee.skills.map((skill) => <span className="soft-tag" key={skill}>{skill}</span>)}</div></td><td><div className="tag-list">{rules.filter((rule) => rule.eligible_employees.some((eligible) => eligible.employee_code === employee.code && eligible.active)).map((rule) => <span className="soft-tag" key={rule.rule_id}>{rule.brand_code} · {rule.code}</span>)}</div></td><td>{employee.weekly_off_weekdays.map((day) => weekdays[day]).join(", ")}</td><td><span className={`row-status ${employee.status}`}>{employee.status}</span></td><td><button className="text-button" onClick={() => startEditingEmployee(employee.code)}>Edit</button></td></tr>)}</tbody></table></div> : <p className="muted">Loading employees…</p>}
          </section>

          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">02</span><div><h2>{employeeEditCode ? "Update employee" : "Add employee"}</h2><p>Manage team details, pod membership, skills, weekly offs, and capacity.</p></div></div><select value={employeeEditCode} onChange={(event) => startEditingEmployee(event.target.value)}><option value="">New employee</option>{summary?.employees.map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></div>
            <form className="change-form page-form" onSubmit={(event) => void saveEmployee(event)}>
              <div className="field-row"><label>Employee code<input required maxLength={20} value={employeeForm.employee_code} onChange={(event) => setEmployeeForm({ ...employeeForm, employee_code: event.target.value })} /></label><label>Full name<input required value={employeeForm.full_name} onChange={(event) => setEmployeeForm({ ...employeeForm, full_name: event.target.value })} /></label></div>
              <div className="field-row"><label>Role/title<input required value={employeeForm.role_title} onChange={(event) => setEmployeeForm({ ...employeeForm, role_title: event.target.value })} /></label><label>Primary pod<select value={employeeForm.pod_name} onChange={(event) => setEmployeeForm({ ...employeeForm, pod_name: event.target.value, pod_names: [...new Set([...employeeForm.pod_names, event.target.value])] })}>{summary?.pods.map((pod) => <option key={pod.id} value={pod.name}>{pod.name}</option>)}</select></label></div>
              <fieldset><legend>Additional pod membership</legend><div className="weekday-pills">{summary?.pods.filter((pod) => pod.name !== employeeForm.pod_name).map((pod) => <label key={pod.id} className={employeeForm.pod_names.includes(pod.name) ? "selected" : ""}><input type="checkbox" checked={employeeForm.pod_names.includes(pod.name)} onChange={() => setEmployeeForm({ ...employeeForm, pod_names: employeeForm.pod_names.includes(pod.name) ? employeeForm.pod_names.filter((name) => name !== pod.name) : [...employeeForm.pod_names, pod.name] })} />{pod.name}</label>)}</div><small className="muted">Membership makes the employee available for that pod’s work window. Skills and brand eligibility are still required.</small></fieldset>
              <div className="field-row"><label>Skill codes<input placeholder="CS, ACC" value={employeeForm.skills} onChange={(event) => setEmployeeForm({ ...employeeForm, skills: event.target.value })} /></label><label>Weekly off weekdays (0=Mon … 6=Sun)<input value={employeeForm.weekly_off_weekdays} onChange={(event) => setEmployeeForm({ ...employeeForm, weekly_off_weekdays: event.target.value })} /></label></div>
              <div className="field-row"><label>Daily allocatable hours<input type="number" min="1" max="8" value={employeeForm.daily_capacity_hours} onChange={(event) => setEmployeeForm({ ...employeeForm, daily_capacity_hours: event.target.value })} /></label><label>Weekly hours<input type="number" min="1" max="40" value={employeeForm.max_hours_per_week} onChange={(event) => setEmployeeForm({ ...employeeForm, max_hours_per_week: event.target.value })} /></label></div>
              <div className="field-row"><label>Max work days per week<input type="number" min="1" max="5" value={employeeForm.max_days_per_week} onChange={(event) => setEmployeeForm({ ...employeeForm, max_days_per_week: event.target.value })} /></label><label>Status<select value={employeeForm.status} onChange={(event) => setEmployeeForm({ ...employeeForm, status: event.target.value })}><option value="active">Active</option><option value="inactive">Inactive</option></select></label></div>
              <label className="share-toggle"><input type="checkbox" checked={employeeForm.is_pod_lead} onChange={(event) => setEmployeeForm({ ...employeeForm, is_pod_lead: event.target.checked })} /> Employee is the pod lead</label>
              <button className="secondary-button" disabled={busy}>{busy ? "Saving…" : employeeEditCode ? "Save employee details" : "Add employee"}</button>
            </form>
          </section>

          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">03</span><div><h2>Pods and pod leads</h2><p>Move employees between teams and manage pod membership and leads.</p></div></div><button className="text-button" type="button" onClick={() => { setPodEditId(null); setPodName(""); setPodSpecialization(""); setPodLead(""); setPodMembers([]); setPodShift({ shift_name: "Day Window", shift_start: "09:00", shift_end: "18:00", break_start: "13:00", break_end: "14:00", allocation_capacity_hours: "6" }); }}>New pod</button></div>
            <div className="pod-grid">{(summary?.pods ?? []).map((pod) => <article className="pod-card" key={pod.id}><strong>{pod.name}</strong><span>{pod.specialization}</span><small>Lead: {pod.lead_employee_code ?? "Not assigned"}</small><small>{pod.members.length} member(s): {pod.members.join(", ")}</small><small>{pod.shift ? `${pod.shift.name} · ${pod.shift.start}–${pod.shift.end} · break ${pod.shift.break_start}–${pod.shift.break_end} · ${pod.shift.allocation_capacity_hours}h allocatable` : "No work window configured"}</small><button className="text-button" onClick={() => { setPodEditId(pod.id); setPodName(pod.name); setPodSpecialization(pod.specialization); setPodLead(pod.lead_employee_code ?? ""); setPodMembers(pod.members); setPodShift(pod.shift ? { shift_name: pod.shift.name, shift_start: pod.shift.start, shift_end: pod.shift.end, break_start: pod.shift.break_start, break_end: pod.shift.break_end, allocation_capacity_hours: String(pod.shift.allocation_capacity_hours) } : { shift_name: "Work Window", shift_start: "09:00", shift_end: "18:00", break_start: "13:00", break_end: "14:00", allocation_capacity_hours: "6" }); }}>Edit pod</button><button className="text-button" disabled={busy} onClick={() => void runAction(async () => { await request(`/api/v1/pods/${pod.id}`, { method: "DELETE" }); await refreshSummary(); setNotice("Empty pod removed."); })}>Remove empty pod</button></article>)}</div>
            <form className="change-form page-form" onSubmit={(event) => void savePod(event)}><div className="field-row"><label>Pod name<input required value={podName} onChange={(event) => setPodName(event.target.value)} /></label><label>Specialization<input value={podSpecialization} onChange={(event) => setPodSpecialization(event.target.value)} /></label></div><label>Pod lead<select value={podLead} onChange={(event) => setPodLead(event.target.value)}><option value="">No lead</option>{activeEmployees.filter((employee) => podEditId === null || employee.pods.includes(summary?.pods.find((pod) => pod.id === podEditId)?.name ?? "")).map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></label><fieldset className="member-picker"><legend>Members of this pod</legend><p className="muted form-hint">Select one or more people. A person can also belong to another pod; membership alone does not grant work eligibility.</p><div className="member-check-grid">{activeEmployees.map((employee) => <label key={employee.code} className={podMembers.includes(employee.code) ? "selected" : ""}><input type="checkbox" checked={podMembers.includes(employee.code)} onChange={() => setPodMembers((current) => current.includes(employee.code) ? current.filter((code) => code !== employee.code) : [...current, employee.code])} /><span>{employee.code} · {employee.name}{employee.code === podLead ? " · Lead" : ""}</span></label>)}</div></fieldset><p className="muted form-hint">Work window is 8 clock hours with a lunch break; no more than six hours are allocated to brand work.</p><div className="field-row"><label>Window name<input required value={podShift.shift_name} onChange={(event) => setPodShift({ ...podShift, shift_name: event.target.value })} /></label><label>Allocatable hours<input type="number" min="0.25" max="8" step="0.25" value={podShift.allocation_capacity_hours} onChange={(event) => setPodShift({ ...podShift, allocation_capacity_hours: event.target.value })} /></label></div><div className="field-row"><label>Window starts<input type="time" value={podShift.shift_start} onChange={(event) => setPodShift({ ...podShift, shift_start: event.target.value })} /></label><label>Window ends<input type="time" value={podShift.shift_end} onChange={(event) => setPodShift({ ...podShift, shift_end: event.target.value })} /></label></div><div className="field-row"><label>Lunch starts<input type="time" value={podShift.break_start} onChange={(event) => setPodShift({ ...podShift, break_start: event.target.value })} /></label><label>Lunch ends<input type="time" value={podShift.break_end} onChange={(event) => setPodShift({ ...podShift, break_end: event.target.value })} /></label></div><button className="secondary-button" disabled={busy}>{podEditId ? "Save pod and members" : "Add pod and members"}</button></form>
          </section>

          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">04</span><div><h2>Record employee leave or unavailability</h2><p>Choose the employee and dates. Pending requests are warnings and do not block scheduling.</p></div></div></div>
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
            <div className="table-wrap page-table"><table><thead><tr><th>Employee</th><th>Type</th><th>Status</th><th>From</th><th>Through</th><th>Update status</th></tr></thead><tbody>{(summary?.employees ?? []).flatMap((employee) => employee.availability_exceptions.map((item) => <tr key={item.id}><td>{employee.code} · {employee.name}</td><td className="capitalize">{item.type.replace(/_/g, " ")}</td><td><span className={`row-status ${item.approval_status}`}>{item.approval_status}</span></td><td>{item.start_date}</td><td>{item.end_date}</td><td><select value={item.approval_status} onChange={(event) => void changeAvailabilityStatus(item, employee, event.target.value)} disabled={busy}><option value="pending">Pending</option><option value="approved">Approved</option><option value="declined">Declined</option></select></td></tr>))}</tbody></table>{!(summary?.employees ?? []).some((employee) => employee.availability_exceptions.length) && <div className="table-footnote">No availability exceptions have been recorded.</div>}</div>
          </section>
        </>}

        {page === "brands" && <>
          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">01</span><div><h2>Brand and process directory</h2><p>{summary?.brands.length ?? 0} brands · {rules.length} configured process rules</p></div></div><span className="small-label">FICTIONAL DEMO DATA</span></div>
            <div className="brand-grid">{(summary?.brands ?? []).map((brand) => <article className="brand-card" key={brand.code}>
              <div className="brand-card-heading"><div><span className="brand-code">{brand.code}</span><h3>{brand.name}</h3><small>Deadline priority {brand.deadline_priority}/5 {brand.deadline_note ? `· ${brand.deadline_note}` : ""}</small></div><span className="brand-count">{brand.processes.length} process{brand.processes.length === 1 ? "" : "es"}</span><button className="text-button" onClick={() => { setEditingBrandCode(brand.code); setNewBrandCode(brand.code); setNewBrandName(brand.name); setBrandPriority(String(brand.deadline_priority)); setBrandDeadlineNote(brand.deadline_note); document.getElementById("brand-create-form")?.scrollIntoView({ behavior: "smooth" }); }}>Edit brand</button><button className="text-button" onClick={() => void toggleBrand(brand)}>{brand.active ? "Archive" : "Reactivate"}</button>{brand.processes.length === 0 && <button className="text-button" onClick={() => void runAction(async () => { await request(`/api/v1/brands/${encodeURIComponent(brand.code)}`, { method: "DELETE" }); await refreshSummary(); setNotice("Brand removed."); })}>Remove</button>}</div>
              <div className="brand-process-list">{brand.processes.map((rule) => {
                const coverage = rule.coverage_by_weekday.filter((item) => rule.operating_weekdays.includes(item.weekday));
                const minimums = [...new Set(coverage.map((item) => item.minimum_staff))].sort((a, b) => a - b);
                const staffText = minimums.length ? minimums.map((count) => `${count} staff`).join(" / ") : "No daily coverage";
                return <div className="brand-process-row" key={rule.rule_id}>
                  <div><strong>{rule.code}</strong><small>{rule.name}</small></div>
                <span>{rule.operating_weekdays.map((day) => weekdays[day]).join(", ") || "No operating days"}</span>
                <span>{staffText}</span>
                <span>{rule.pod_name ?? "No work window"}</span>
                <span>{rule.eligible_employees.filter((employee) => employee.active).map((eligible) => `${eligible.employee_code} (${eligible.eligibility_type})`).join(", ") || "No eligible employees"}</span>
                  <div><button className="text-button" onClick={() => { setRuleId(rule.rule_id); setSelectedDays(rule.operating_weekdays); setOperateOnHolidays(rule.operate_on_public_holidays); setCanShareSameDay(rule.can_share_same_day); setRulePod(rule.pod_name ?? ""); setRuleEffort(String(rule.effort_hours_per_employee)); setMinimumStaff(""); setCoverageWeekday(rule.operating_weekdays[0] ?? 0); document.getElementById("brand-rule-form")?.scrollIntoView({ behavior: "smooth" }); }}>Edit rules</button><button className="text-button" disabled={busy} onClick={() => void runAction(async () => { await request(`/api/v1/brand-process-rules/${rule.rule_id}`, { method: "DELETE" }); await refreshSummary(); setNotice("Brand/process rule removed."); })}>Remove rule</button></div>
                </div>;
              })}</div>
            </article>)}</div>
          </section>

          <section className="card page-card" id="brand-rule-form">
            <div className="section-heading"><div><span className="step-badge">02</span><div><h2>Update a brand/process rule</h2><p>Change its working calendar, public-holiday behavior, same-day sharing, or minimum staffing.</p></div></div></div>
            <form className="change-form page-form" onSubmit={(event) => void saveBrandRule(event)}>
              <label>Brand and process<select value={ruleId} onChange={(event) => { const nextId = Number(event.target.value); setRuleId(nextId); const match = rules.find((rule) => rule.rule_id === nextId); if (match) { setSelectedDays(match.operating_weekdays); setOperateOnHolidays(match.operate_on_public_holidays); setCanShareSameDay(match.can_share_same_day); setRulePod(match.pod_name ?? ""); setRuleEffort(String(match.effort_hours_per_employee)); setMinimumStaff(""); setCoverageWeekday(match.operating_weekdays[0] ?? 0); } }}><option value="">Choose a rule</option>{rules.map((rule) => <option key={rule.rule_id} value={rule.rule_id}>{rule.brand_code} · {rule.code} · {rule.name}</option>)}</select></label>
              {activeRule && <div className="rule-context"><span>Required skill: <strong>{activeRule.required_skill}</strong></span><span>Effort per person: <strong>{activeRule.effort_hours_per_employee}h</strong></span><span>Work window: <strong>{activeRule.pod_name ?? "Unassigned"}</strong></span><span>Deadline priority: <strong>{activeRule.deadline_priority}/5</strong></span><span>Currently eligible: <strong>{activeRule.eligible_employees.filter((employee) => employee.active).length}</strong></span></div>}
              <label>Pod work window<select value={rulePod} onChange={(event) => setRulePod(event.target.value)}><option value="">Choose a pod window</option>{summary?.pods.filter((pod) => pod.shift).map((pod) => <option key={pod.id} value={pod.name}>{pod.name} · {pod.shift?.name} ({pod.shift?.start}–{pod.shift?.end})</option>)}</select></label>
              <label>Effort hours per employee<input type="number" min="0.25" max="6" step="0.25" value={ruleEffort} onChange={(event) => setRuleEffort(event.target.value)} /></label>
              <p className="muted form-hint">Operating days and staffing are separate settings. When opening a new weekday, select that weekday below and set its minimum staff so the solver has demand to allocate.</p>
              {activeRule && <><div className="eligibility-list">{activeRule.eligible_employees.filter((item) => item.active).map((item) => <div className="eligibility-row" key={item.employee_code}><span className="soft-tag">{item.employee_code} · {summary?.employees.find((employee) => employee.code === item.employee_code)?.name} · {item.eligibility_type}</span><button type="button" className="text-button" disabled={busy} onClick={() => void removeEligibility(activeRule, item.employee_code)}>Remove eligibility</button></div>)}</div><div className="change-form page-form"><div className="field-row"><label>Add or update eligible employee<select value={eligibilityEmployee} onChange={(event) => setEligibilityEmployee(event.target.value)}><option value="">Choose an employee</option>{activeEmployees.filter((employee) => employee.skills.includes(activeRule.required_skill)).map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></label><label>Resource type<select value={eligibilityType} onChange={(event) => setEligibilityType(event.target.value)}><option value="primary">Primary owner</option><option value="backup">Backup</option></select></label></div><button type="button" className="secondary-button" disabled={busy || !eligibilityEmployee} onClick={() => void saveEligibility()}>Save eligibility</button></div></>}
              <fieldset><legend>Operating weekdays</legend><div className="weekday-pills">{weekdays.map((day, index) => <label key={day} className={selectedDays.includes(index) ? "selected" : ""}><input type="checkbox" checked={selectedDays.includes(index)} onChange={() => setSelectedDays((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index].sort())} />{day}</label>)}</div></fieldset>
              <label>Public holiday operation<select value={operateOnHolidays ? "yes" : "no"} onChange={(event) => setOperateOnHolidays(event.target.value === "yes")}><option value="no">Closed</option><option value="yes">Open</option></select></label>
              <label className="share-toggle"><input type="checkbox" checked={canShareSameDay} onChange={(event) => setCanShareSameDay(event.target.checked)} /> Multiple employees may cover this process on one day</label>
              <div className="field-row"><label>Coverage weekday<select value={coverageWeekday} onChange={(event) => setCoverageWeekday(Number(event.target.value))}>{weekdays.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label><label>New minimum staff<input type="number" min="0" max={activeEmployees.length} placeholder="Leave unchanged" value={minimumStaff} onChange={(event) => setMinimumStaff(event.target.value)} /></label></div>
              <button className="secondary-button" disabled={busy || !ruleId || selectedDays.length === 0}>{busy ? "Saving…" : "Save rule and check roster impact"}</button>
            </form>
            {changeKind === "brand rule" && renderChangeImpact()}
          </section>

          <section className="card page-card"><div className="section-heading"><div><span className="step-badge">03</span><div><h2>Add a brand or process</h2><p>Create new work types and connect them to brand calendars.</p></div></div><div><button className="text-button" type="button" onClick={() => { setEditingBrandCode(""); setNewBrandCode(""); setNewBrandName(""); setBrandPriority("3"); setBrandDeadlineNote(""); }}>New brand</button><button className="text-button" type="button" onClick={() => { setEditingProcessCode(""); setNewProcessCode(""); setNewProcessName(""); setNewProcessSkill(""); setNewProcessHours("6"); }}>New process</button></div></div>
            <form id="brand-create-form" className="change-form page-form" onSubmit={(event) => void saveNewBrand(event)}><div className="field-row"><label>Brand code<input required value={newBrandCode} onChange={(event) => setNewBrandCode(event.target.value)} /></label><label>Brand name<input required value={newBrandName} onChange={(event) => setNewBrandName(event.target.value)} /></label></div><div className="field-row"><label>Deadline priority (1–5)<select value={brandPriority} onChange={(event) => setBrandPriority(event.target.value)}><option value="1">1 · Routine</option><option value="2">2 · Normal</option><option value="3">3 · Elevated</option><option value="4">4 · Urgent</option><option value="5">5 · Critical</option></select></label><label>Deadline note<input maxLength={200} placeholder="Why it is urgent / due by" value={brandDeadlineNote} onChange={(event) => setBrandDeadlineNote(event.target.value)} /></label></div><p className="muted form-hint">Set this from the current deadline. It breaks ties only after maximizing total coverage; skill, eligibility, availability, and capacity stay mandatory.</p><button className="secondary-button" disabled={busy}>{editingBrandCode ? "Save brand" : "Add brand"}</button>{editingBrandCode && <button className="text-button" type="button" onClick={() => { setEditingBrandCode(""); setNewBrandCode(""); setNewBrandName(""); setBrandPriority("3"); setBrandDeadlineNote(""); }}>Cancel edit</button>}</form>
            <form className="change-form page-form" onSubmit={(event) => void saveNewProcess(event)}><div className="field-row"><label>Process code<input required value={newProcessCode} onChange={(event) => setNewProcessCode(event.target.value)} /></label><label>Process name<input required value={newProcessName} onChange={(event) => setNewProcessName(event.target.value)} /></label></div><div className="field-row"><label>Required skill code<input required value={newProcessSkill} onChange={(event) => setNewProcessSkill(event.target.value)} /></label><label>Default allocatable hours per person<input type="number" min="0.25" max="6" step="0.25" value={newProcessHours} onChange={(event) => setNewProcessHours(event.target.value)} /></label></div><button className="secondary-button" disabled={busy}>{editingProcessCode ? "Save process" : "Add process"}</button>{editingProcessCode && <button className="text-button" type="button" onClick={() => { setEditingProcessCode(""); setNewProcessCode(""); setNewProcessName(""); setNewProcessSkill(""); setNewProcessHours("6"); }}>Cancel edit</button>}</form>
            <div className="process-catalog">{(summary?.process_catalog ?? []).map((process) => <div className="eligibility-row" key={process.code}><span><strong>{process.code} · {process.name}</strong><small>Skill {process.required_skill} · {process.default_effort_hours}h per person</small></span><button className="text-button" onClick={() => { setEditingProcessCode(process.code); setNewProcessCode(process.code); setNewProcessName(process.name); setNewProcessSkill(process.required_skill); setNewProcessHours(String(process.default_effort_hours)); }}>Edit</button>{!rules.some((rule) => rule.code === process.code) && <button className="text-button" disabled={busy} onClick={() => void runAction(async () => { await request(`/api/v1/processes/${encodeURIComponent(process.code)}`, { method: "DELETE" }); await refreshSummary(); setNotice("Unused process removed."); })}>Remove</button>}</div>)}</div>
            <form className="change-form page-form" onSubmit={(event) => void saveNewRule(event)}><label>Connect process to brand<div className="field-row"><select required value={newRuleBrand} onChange={(event) => setNewRuleBrand(event.target.value)}><option value="">Brand</option>{summary?.brands.map((brand) => <option key={brand.code} value={brand.code}>{brand.code} · {brand.name}</option>)}</select><select required value={newRuleProcess} onChange={(event) => setNewRuleProcess(event.target.value)}><option value="">Process</option>{summary?.process_catalog.filter((process) => process.enabled).map((process) => <option key={process.code} value={process.code}>{process.code} · {process.name}</option>)}</select></div></label><label>Pod work window<select required value={newRulePod} onChange={(event) => setNewRulePod(event.target.value)}><option value="">Choose a pod window</option>{summary?.pods.filter((pod) => pod.shift).map((pod) => <option key={pod.id} value={pod.name}>{pod.name} · {pod.shift?.name} ({pod.shift?.start}–{pod.shift?.end})</option>)}</select></label><fieldset><legend>Operating weekdays</legend><div className="weekday-pills">{weekdays.map((day, index) => <label key={day} className={newRuleDays.includes(index) ? "selected" : ""}><input type="checkbox" checked={newRuleDays.includes(index)} onChange={() => setNewRuleDays((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index].sort())} />{day}</label>)}</div></fieldset><div className="field-row"><label>Minimum staff on operating days<input type="number" min="1" max={activeEmployees.length} value={newRuleMinimum} onChange={(event) => setNewRuleMinimum(event.target.value)} /></label><label>Staff effort per day<input type="number" min="0.25" max="6" step="0.25" value={newProcessHours} onChange={(event) => setNewProcessHours(event.target.value)} /></label></div><button className="secondary-button" disabled={busy || !newRuleBrand || !newRuleProcess || !newRulePod || !newRuleDays.length}>Add brand/process requirement</button></form>
          </section>

          <section className="card page-card">
            <div className="section-heading"><div><span className="step-badge">03</span><div><h2>Public holidays</h2><p>Configured demo dates used by each brand’s holiday rule.</p></div></div></div>
            <div className="holiday-list">{(summary?.public_holidays ?? []).map((holiday) => <div className="holiday-row" key={holiday.id}><span className="holiday-mark">✦</span><strong>{holiday.name}</strong><span>{holiday.date}</span><button className="text-button" onClick={() => void runAction(async () => { await request(`/api/v1/public-holidays/${holiday.id}`, { method: "DELETE" }); await refreshSummary(); setNotice("Public holiday removed."); })}>Remove</button></div>)}</div><form className="change-form page-form" onSubmit={(event) => void addHoliday(event)}><div className="field-row"><label>Holiday name<input required value={holidayName} onChange={(event) => setHolidayName(event.target.value)} /></label><label>Date<input type="date" required value={holidayDate} onChange={(event) => setHolidayDate(event.target.value)} /></label></div><button className="secondary-button" disabled={busy}>Add public holiday</button></form>
          </section>
        </>}
      </div>

      <aside className="side-column">
        <section className="card side-card">
          <div className="side-title"><span className="side-icon">✓</span><div><h3>{changePreview ? "Proposed roster" : "Coverage status"}</h3><p>{changePreview ? "Calculated without changing the current draft" : "Independent check of the selected draft"}</p></div></div>
          {changePreview ? <>
            <div className={`validation-banner ${changePreview.uncovered_slots ? "needs_attention" : "valid"}`}>
              <strong>{changePreview.uncovered_slots ? "More allocation needed" : "Demand covered"}</strong>
              <span>{changePreview.filled_slots} of {changePreview.required_slots} affected slots allocated</span>
            </div>
            <div className="mini-stat"><span>Unfilled coverage</span><strong>{changePreview.uncovered_slots}</strong></div>
            <div className="mini-stat"><span>Recommendation rows</span><strong>{changePreview.recommendations.length}</strong></div>
          </> : validation ? <>
            <div className={`validation-banner ${validation.status}`}><strong>{validation.status.replace(/_/g, " ")}</strong><span>{validation.error_count} coverage gaps or rule conflicts · {validation.warning_count} warnings</span></div>
            <div className="mini-stat"><span>Employee-day rows</span><strong>{roster?.employee_days.length ?? 0}</strong></div>
            <div className="mini-stat"><span>Assigned work rows</span><strong>{roster?.assignments.filter((item) => item.status === "working").length ?? 0}</strong></div>
          </> : <p className="muted">Generate a roster to see coverage results.</p>}
        </section>
        <section className="card side-card"><div className="side-title"><span className="side-icon purple">◷</span><div><h3>Planning rules</h3><p>Visible demo assumptions</p></div></div><div className="rule-list"><div><span>Allocatable work</span><strong>Up to 6h per employee/day</strong></div><div><span>Pod window</span><strong>8h including lunch</strong></div><div><span>Weekly cap</span><strong>40 hours · 5 days</strong></div><div><span>Deadline priority</span><strong>1–5 tie-breaker</strong></div><div><span>Horizon</span><strong>{today.getFullYear()}–{today.getFullYear() + 5}</strong></div></div><p className="side-note">Assignments are tied to pod work windows. Lunch is excluded from active overlap checks; individual tasks are not minute-by-minute scheduled.</p></section>
        {page === "roster" && <section className="card side-card help-card"><div className="side-title"><span className="side-icon orange">i</span><div><h3>How to review a change</h3><p>Manager-controlled workflow</p></div></div><ol><li>Open Employees & leave or Brands.</li><li>Choose the change and review affected dates.</li><li>Check qualified backup options.</li><li>Confirm a targeted replan to create a new draft.</li></ol></section>}
        <div className="footer-note">DataStraw Smart Roster <span>·</span> Time-aware allocation review</div>
      </aside>
    </section>
  </main>;
}

function ReadOnlyDashboard(props: {
  role: Exclude<Role, "manager">;
  actorCode: string;
  identities: DemoIdentity[];
  summary: Summary | null;
  roster: Roster | null;
  savedRosters: SavedRoster[];
  validation: Validation | null;
  onRoleChange: (role: Role) => void;
  onIdentityChange: (code: string) => void;
  onLoadRoster: (id: number) => void;
}) {
  const { role, actorCode, identities, summary, roster, savedRosters, validation } = props;
  const actor = summary?.employees.find((item) => item.code === actorCode) ?? summary?.employees[0];
  const roleLabel = role === "pod_lead" ? "Pod lead · read only" : "Employee · read only";
  const eligibleIdentities = identities.filter((item) => role !== "pod_lead" || item.is_pod_lead);
  const assignments = roster?.assignments ?? [];
  const employeeDays = roster?.employee_days ?? [];
  return <main className="page-shell">
    <header className="topbar">
      <div className="brand-lockup"><span className="brand-mark">DS</span><div><strong>DataStraw</strong><small>Smart Roster</small></div></div>
      <div className="role-toolbar">
        <label>Demo role<select value={role} onChange={(event) => props.onRoleChange(event.target.value as Role)}>
          <option value="manager">Manager</option><option value="pod_lead">Pod lead</option><option value="employee">Employee</option>
        </select></label>
        <label>{role === "pod_lead" ? "Pod lead identity" : "Employee identity"}<select value={actorCode}
          onChange={(event) => props.onIdentityChange(event.target.value)}>
          {eligibleIdentities.map((item) => <option key={item.employee_code} value={item.employee_code}>
            {item.employee_code} · {item.full_name}
          </option>)}
        </select></label>
      </div>
    </header>
    <section className="hero dashboard-hero">
      <div><div className="eyebrow">PERSONAL ROSTER VIEW</div>
        <h1>{role === "pod_lead" ? "Pod schedule" : "My schedule"}</h1>
        <p>{roleLabel}{actor ? ` · ${actor.code} ${actor.name}` : ""}</p>
      </div>
      <div className="hero-meta"><span className="assumption-tag">READ ONLY</span>
        <span>{role === "pod_lead" ? "View your pod members, assignments, and related validation issues." : "View your assignments, work days, and recorded availability."}</span>
      </div>
    </section>
    <section className="card readonly-card">
      <div className="section-heading"><div><span className="step-badge">01</span><div><h2>Roster</h2>
        <p>Select a saved month to review.</p></div></div>
        <select aria-label="Saved roster" value={roster?.roster_run_id ?? ""}
          onChange={(event) => event.target.value && props.onLoadRoster(Number(event.target.value))}>
          <option value="" disabled>Select a roster</option>
          {savedRosters.map((item) => <option key={item.roster_run_id} value={item.roster_run_id}>
            {new Date(item.year, item.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })} · #{item.roster_run_id}
          </option>)}
        </select>
      </div>
      {roster ? <>
        <div className="readonly-summary">
          <span>{new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })}</span>
          <span className="row-status">{roster.status}</span>
          {role === "pod_lead" && roster.generation && <span>{roster.generation.filled_slots}/{roster.generation.required_slots} pod assignments covered</span>}
        </div>
        <div className="table-wrap page-table readonly-table"><table><thead><tr>
        <th>Date</th><th>Brand</th><th>Process</th><th>Pod</th><th>Employee</th><th>Status</th><th>Reason</th>
        </tr></thead><tbody>{assignments.map((item) => <tr key={item.assignment_id}>
          <td>{item.date}</td><td>{item.brand_code}</td>
          <td>{item.process_code}</td><td>{item.pod_name ?? "—"}</td><td>{item.employee_code ?? "Uncovered"}</td>
          <td><span className={`row-status ${item.status}`}>{item.status}</span></td><td className="readonly-reason">{item.explanation}</td>
        </tr>)}</tbody></table>{assignments.length === 0 && <div className="table-footnote">No assignments are available for this view.</div>}</div>
        {role === "employee" && <>
          <div className="section-heading readonly-subheading"><div><span className="step-badge">02</span><div><h2>My work days</h2><p>Scheduled work, weekly offs, and availability status.</p></div></div></div>
          <div className="table-wrap page-table readonly-table"><table><thead><tr><th>Date</th><th>Status</th><th>Allocated hours</th><th>Details</th></tr></thead>
            <tbody>{employeeDays.map((item) => <tr key={`${item.date}-${item.employee_code}`}><td>{item.date}</td>
              <td><span className={`row-status ${item.status}`}>{item.status.replace(/_/g, " ")}</span></td>
              <td>{item.assigned_effort_hours}h</td><td>{item.explanation}</td></tr>)}</tbody>
          </table>{employeeDays.length === 0 && <div className="table-footnote">No work-day details are available.</div>}</div>
          <div className="section-heading readonly-subheading"><div><span className="step-badge">03</span><div><h2>My availability</h2><p>Leave, comp-off, and unavailable dates recorded for you.</p></div></div></div>
          <div className="table-wrap page-table readonly-table"><table><thead><tr><th>Type</th><th>Approval</th><th>From</th><th>Through</th><th>Note</th></tr></thead>
            <tbody>{(actor?.availability_exceptions ?? []).map((item) => <tr key={item.id}><td>{item.type.replace(/_/g, " ")}</td>
              <td>{item.approval_status}</td><td>{item.start_date}</td><td>{item.end_date}</td><td>{item.note || "—"}</td></tr>)}</tbody>
          </table>{!(actor?.availability_exceptions ?? []).length && <div className="table-footnote">No availability records have been entered.</div>}</div>
        </>}
      </> : <div className="readonly-empty">{savedRosters.length ? "Choose a saved roster to continue." : "No roster has been generated yet."}</div>}
    </section>
    {validation && <section className="card readonly-card">
      <div className="section-heading"><div><span className="step-badge">!</span><div><h2>Related conflicts and validation</h2>
        <p>Issues shown here are limited to your schedule or pod.</p></div></div>
        <span className={`status-pill ${validation.status}`}>{validation.status.replace(/_/g, " ")}</span>
      </div>
      {validation.issues.length ? <div className="issue-list">{validation.issues.map((issue, index) => <div className={`issue-row ${issue.severity === "warning" ? "warning-row" : ""}`} key={`${issue.code}-${index}`}>
        <span className="issue-icon">{issue.severity === "warning" ? "!" : "×"}</span><div><strong>{issue.date ?? "Roster"} · {issue.brand_code ?? "Schedule"}{issue.process_code ? ` · ${issue.process_code}` : ""}</strong><small>{issue.message}</small></div>
      </div>)}</div> : <div className="empty-success">No related validation issues.</div>}
    </section>}
    <p className="footer-note">Demo role selection is for workflow review. It is not sign-in and does not verify who is using the app.</p>
  </main>;
}

export default App;
