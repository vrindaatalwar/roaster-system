import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  IconArrowBackUp,
  IconArrowForwardUp,
  IconBriefcaseFilled,
  IconCalendarFilled,
  IconLayoutDashboardFilled,
  IconListCheckFilled,
  IconPencil,
  IconPlus,
  IconUserFilled,
} from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const API = import.meta.env.VITE_API_BASE_URL ?? "";
const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const today = new Date();

type Page = "roster" | "daily_assignments" | "employee_calendar" | "employees" | "brands";
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
  demand_units?: number | null;
  pod_name: string | null;
  deadline_priority: number;
  deadline_note: string;
  required_skill: string;
  coverage_by_weekday: { weekday: number; minimum_staff: number; maximum_staff: number | null }[];
  eligible_employees: { employee_code: string; eligibility_type: string; active: boolean }[];
};
type Brand = { code: string; name: string; active: boolean; deadline_priority: number; deadline_note: string; client_tier: number; deadline_date: string | null; processes: Rule[] };
type Pod = { id: number; name: string; specialization: string; lead_employee_code: string | null; members: string[]; shift: { name: string; start: string; end: string; break_start: string; break_end: string; allocation_capacity_hours: number } | null };
type Holiday = { id: number; name: string; date: string };
type AllocationSettings = { capacity_units: number; min_chunk_units: number; max_brands_per_day: number; hours_layer_enabled: boolean; solver_time_limit_seconds: number };
type PriorityOverride = { id: number; brand_code: string; start_date: string; end_date: string; multiplier: number; note: string };
type Summary = { employees: Employee[]; brands: Brand[]; pods: Pod[]; process_catalog: { code: string; name: string; required_skill: string; default_effort_hours: number; enabled: boolean }[]; public_holidays: Holiday[]; allocation_settings: AllocationSettings; priority_overrides: PriorityOverride[] };
type Assignment = { assignment_id: number; date: string; employee_code: string | null; brand_code: string; process_code: string; effort_hours: number; hours_units: number; priority_score?: { score: number; client_tier: number; urgency: number; days_to_deadline: number | null; backlog_factor: number; manager_override: number } | null; pod_name: string | null; shift: Pod["shift"]; deadline_priority: number; available_candidates: { employee_code: string; name: string; eligibility_type: string; reason: string }[]; status: string; explanation: string };
type ValidationIssue = { severity: string; code: string; message: string; date?: string; brand_code?: string; process_code?: string; employee_code?: string };
type Validation = { roster_run_id: number; status: string; issue_count: number; error_count: number; gap_count: number; warning_count: number; issues: ValidationIssue[] };
type Roster = {
  roster_run_id: number;
  year: number;
  month: number;
  status: string;
  generation: { required_slots: number; filled_slots: number; uncovered_slots: number; result_status: string; hours_layer_status?: string; solve_time_seconds?: number } | null;
  assignments: Assignment[];
  process_day_allocation?: { brand_code: string; process_code: string; date: string; served_units: number; demand_units: number; priority_score: { score: number; client_tier: number; urgency: number; days_to_deadline: number | null; backlog_factor: number; manager_override: number } | null }[];
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
type RosterHistoryEntry =
  | { kind: "assignment"; label: string; run_id: number; assignment_id: number; before_employee_code: string | null; after_employee_code: string | null; before_uncovered: number; after_uncovered: number }
  | { kind: "roster"; label: string; before_run_id: number | null; after_run_id: number; before_year_month: string | null; after_year_month: string; before_uncovered: number | null; after_uncovered: number };

function pageFromHash(): Page {
  const path = window.location.hash.replace(/^#\/?/, "");
  return path === "daily_assignments" || path === "employee_calendar" || path === "employees" || path === "brands" ? path : "roster";
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
    const detail = typeof data.detail === "string" ? data.detail
      : typeof data.detail?.message === "string" ? data.detail.message
        : JSON.stringify(data.detail ?? data);
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
  const [undoHistory, setUndoHistory] = useState<RosterHistoryEntry[]>([]);
  const [redoHistory, setRedoHistory] = useState<RosterHistoryEntry[]>([]);
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
  const [brandTier, setBrandTier] = useState("3");
  const [brandDeadlineDate, setBrandDeadlineDate] = useState("");
  const [editingBrandActive, setEditingBrandActive] = useState(true);
  const [overrideForm, setOverrideForm] = useState({ brand_code: "", start_date: "", end_date: "", multiplier: "1", note: "" });
  const [editingOverrideId, setEditingOverrideId] = useState<number | null>(null);
  const [allocationForm, setAllocationForm] = useState<AllocationSettings>({ capacity_units: 12, min_chunk_units: 1, max_brands_per_day: 2, hours_layer_enabled: false, solver_time_limit_seconds: 60 });
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
  const [ruleDemandUnits, setRuleDemandUnits] = useState("");
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
    if (podFilter !== "all" && podFilter !== "__unassigned__"
      && summary && !summary.pods.some((pod) => pod.name === podFilter)) {
      setPodFilter("all");
    }
  }, [summary, podFilter]);


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
      setAllocationForm(data.allocation_settings);
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
  const assignmentPodName = (item: Assignment) => item.pod_name
    ?? rules.find((rule) => rule.brand_code === item.brand_code && rule.code === item.process_code)?.pod_name
    ?? "__unassigned__";
  const activeRule = rules.find((rule) => rule.rule_id === ruleId);
  const activeEmployees = summary?.employees.filter((employee) => employee.status === "active") ?? [];
  const visibleProcesses = [...new Set(rules.filter((rule) => brandFilter === "all" || rule.brand_code === brandFilter).map((rule) => rule.code))];
  const coverageCounts = useMemo(() => {
    const counts = new Map<string, { filled: number; required: number }>();
    for (const item of roster?.assignments ?? []) {
      if ((brandFilter !== "all" && item.brand_code !== brandFilter)
        || (processFilter !== "all" && item.process_code !== processFilter)
        || (podFilter !== "all" && assignmentPodName(item) !== podFilter)
        || (dateFilter && item.date !== dateFilter)) continue;
      const key = `${item.date}|${item.brand_code}|${item.process_code}`;
      const count = counts.get(key) ?? { filled: 0, required: 0 };
      count.required += 1;
      if (item.status === "working") count.filled += 1;
      counts.set(key, count);
    }
    return counts;
  }, [roster, rules, brandFilter, processFilter, podFilter, dateFilter]);
  const visibleAssignments = useMemo(() => (roster?.assignments ?? []).filter((item) =>
    (brandFilter === "all" || item.brand_code === brandFilter)
    && (processFilter === "all" || item.process_code === processFilter)
    && (assignmentEmployeeFilter === "all" || item.employee_code === assignmentEmployeeFilter)
    && (podFilter === "all" || assignmentPodName(item) === podFilter)
    && (!dateFilter || item.date === dateFilter)), [roster, rules, brandFilter, processFilter, dateFilter, podFilter, assignmentEmployeeFilter]);
  const visibleValidationIssues = useMemo(() => (validation?.issues ?? []).filter((issue) => {
    if (dateFilter && issue.date && issue.date !== dateFilter) return false;
    if (assignmentEmployeeFilter !== "all" && issue.employee_code
      && issue.employee_code !== assignmentEmployeeFilter) return false;
    if (brandFilter !== "all" && issue.brand_code && issue.brand_code !== brandFilter) return false;
    if (processFilter !== "all" && issue.process_code && issue.process_code !== processFilter) return false;
    if (podFilter !== "all") {
      if (issue.brand_code && issue.process_code) {
        const issueRule = rules.find((rule) => rule.brand_code === issue.brand_code && rule.code === issue.process_code);
        if ((issueRule?.pod_name ?? "__unassigned__") !== podFilter) return false;
      } else if (issue.employee_code) {
        const employee = summary?.employees.find((item) => item.code === issue.employee_code);
        if (podFilter === "__unassigned__" || !employee?.pods.includes(podFilter)) return false;
      }
    }
    return true;
  }), [validation, dateFilter, assignmentEmployeeFilter, brandFilter, processFilter, podFilter, rules, summary]);
  const filteredRequiredSlots = visibleAssignments.length;
  const filteredFilledSlots = visibleAssignments.filter((item) => item.status === "working").length;
  const filteredUncoveredSlots = filteredRequiredSlots - filteredFilledSlots;
  const filteredErrorCount = visibleValidationIssues.filter((issue) => issue.severity === "error").length;
  const filteredGapCount = visibleValidationIssues.filter((issue) => issue.severity === "gap").length;
  const filteredWarningCount = visibleValidationIssues.filter((issue) => issue.severity === "warning").length;
  const coverageIssues = visibleValidationIssues.filter((issue) => issue.severity === "error" || issue.severity === "gap");
  const warningIssues = visibleValidationIssues.filter((issue) => issue.severity === "warning");
  const renderValidationIssue = (issue: ValidationIssue, index: number) => <div className="flex items-start gap-3 border-b border-[#f0f2f3] px-3.5 py-2.5 last:border-b-0" key={`${issue.code}-${issue.date}-${index}`}>
    <div className={cn("min-w-0 flex-1")}><div className={cn("flex items-center justify-between gap-3 [&_strong]:overflow-hidden [&_strong]:text-ellipsis [&_strong]:whitespace-nowrap [&_strong]:text-[13px] [&_strong]:font-semibold [&_strong]:text-[#1d1d1f]")}><strong>{issue.brand_code && issue.process_code ? `${issue.brand_code} · ${issue.process_code}` : issue.employee_code ?? "Roster"}</strong><span className={cn("shrink-0 rounded-md bg-[#f1f3f9] px-2 py-1 text-[10px] font-medium text-[#003459]")}>{issue.severity === "gap" ? "Coverage gap" : issue.severity === "warning" ? "Warning" : "Rule conflict"}</span></div>
      <small className={cn("mt-0.5 block text-xs text-[#6e6e73]")}>{issue.date ?? "Roster-wide"}</small>
      <details className={cn("mt-1 text-xs text-[#424245] [&_summary]:w-fit [&_summary]:cursor-pointer [&_summary]:font-semibold [&_summary]:text-[#003459] [&_p]:mt-1 [&_p]:max-w-[72ch] [&_p]:text-[13px] [&_p]:leading-[1.45] [&_p]:text-[#424245]")}><summary>Details</summary><p>{issue.message}</p></details>
    </div>
  </div>;
  const visibleEmployeeDayCode = activeEmployees.some((employee) => employee.code === employeeDayFilter)
    ? employeeDayFilter : activeEmployees[0]?.code ?? "";
  const visibleEmployee = activeEmployees.find((employee) => employee.code === visibleEmployeeDayCode);
  const visibleEmployeeDays = (roster?.employee_days ?? []).filter((item) => item.employee_code === visibleEmployeeDayCode);
  const employeeCalendarDayMap = new Map(visibleEmployeeDays.map((item) => [item.date, item]));
  const calendarOffset = roster ? (new Date(roster.year, roster.month - 1, 1).getDay() + 6) % 7 : 0;
  const calendarDayCount = roster ? new Date(roster.year, roster.month, 0).getDate() : 0;
  const calendarCellCount = Math.ceil((calendarOffset + calendarDayCount) / 7) * 7;
  const employeeCalendarCells = Array.from({ length: calendarCellCount }, (_, index) => {
    const day = index - calendarOffset + 1;
    if (day < 1 || day > calendarDayCount || !roster) return null;
    const date = `${roster.year}-${String(roster.month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    return { day, date, item: employeeCalendarDayMap.get(date) };
  });
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

  async function loadRoster(id: number): Promise<Roster> {
    const [data, checks] = await Promise.all([
      request<Roster>(`/api/v1/rosters/${id}`),
      request<Validation>(`/api/v1/rosters/${id}/validation`),
    ]);
    setRoster(data);
    setValidation(checks);
    setBrandFilter("all");
    setProcessFilter("all");
    setDateFilter("");
    return data;
  }

  async function refreshSummary() {
    const data = await request<Summary>("/api/v1/seed-summary");
    setSummary(data);
    setAllocationForm(data.allocation_settings);
  }

  async function refreshSavedRosters() {
    setSavedRosters(await request<SavedRoster[]>("/api/v1/rosters"));
  }

  function rememberRosterChange(entry: RosterHistoryEntry) {
    setUndoHistory((current) => [...current, entry].slice(-50));
    setRedoHistory([]);
  }

  async function replayRosterHistory(entry: RosterHistoryEntry, direction: "undo" | "redo") {
    if (entry.kind === "assignment") {
      await request(`/api/v1/rosters/${entry.run_id}/assignments/${entry.assignment_id}`, {
        method: "PATCH",
        body: JSON.stringify({ employee_code: direction === "undo" ? entry.before_employee_code : entry.after_employee_code }),
      });
      await loadRoster(entry.run_id);
      return;
    }
    const targetRunId = direction === "undo" ? entry.before_run_id : entry.after_run_id;
    if (targetRunId === null) {
      setRoster(null);
      setValidation(null);
      return;
    }
    await loadRoster(targetRunId);
  }

  async function undoRosterChange() {
    const entry = undoHistory[undoHistory.length - 1];
    if (!entry) return;
    await runAction(async () => {
      await replayRosterHistory(entry, "undo");
      setUndoHistory((current) => current.slice(0, -1));
      setRedoHistory((current) => [...current, entry].slice(-50));
      const restoredUncovered = entry.before_uncovered;
      setNotice(`Undid: ${entry.label}.${restoredUncovered === null ? "" : ` Uncovered coverage is now ${restoredUncovered}.`}`);
    });
  }

  async function redoRosterChange() {
    const entry = redoHistory[redoHistory.length - 1];
    if (!entry) return;
    await runAction(async () => {
      await replayRosterHistory(entry, "redo");
      setRedoHistory((current) => current.slice(0, -1));
      setUndoHistory((current) => [...current, entry].slice(-50));
      setNotice(`Redid: ${entry.label}. Uncovered coverage is now ${entry.after_uncovered}.`);
    });
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
      await request(`/api/v1/brands/${encodeURIComponent(brand.code)}`, { method: "PATCH", body: JSON.stringify({ code: brand.code, name: brand.name, active: !brand.active, deadline_priority: brand.deadline_priority, deadline_note: brand.deadline_note, client_tier: brand.client_tier, deadline_date: brand.deadline_date }) });
      await refreshSummary(); setNotice(`${brand.code} ${brand.active ? "archived" : "reactivated"}.`);
    });
  }

  async function saveNewBrand(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const existing = Boolean(editingBrandCode);
      await request(existing ? `/api/v1/brands/${encodeURIComponent(editingBrandCode)}` : "/api/v1/brands", { method: existing ? "PATCH" : "POST", body: JSON.stringify({ code: newBrandCode, name: newBrandName, active: existing ? editingBrandActive : true, deadline_priority: Number(brandPriority), deadline_note: brandDeadlineNote, client_tier: Number(brandTier), deadline_date: brandDeadlineDate || null }) });
      setNewBrandCode(""); setNewBrandName(""); setEditingBrandCode(""); setEditingBrandActive(true); setBrandPriority("3"); setBrandDeadlineNote(""); setBrandTier("3"); setBrandDeadlineDate(""); await refreshSummary(); setNotice(existing ? "Brand details saved." : "Brand added.");
    });
  }

  async function saveAllocationSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      const saved = await request<AllocationSettings>("/api/v1/allocation-settings", { method: "PUT", body: JSON.stringify(allocationForm) });
      setAllocationForm(saved); await refreshSummary(); setNotice("Hours allocation settings saved.");
    });
  }

  async function savePriorityOverride(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await runAction(async () => {
      await request(editingOverrideId ? `/api/v1/priority-overrides/${editingOverrideId}` : "/api/v1/priority-overrides", { method: editingOverrideId ? "PATCH" : "POST", body: JSON.stringify({ ...overrideForm, multiplier: Number(overrideForm.multiplier) }) });
      setOverrideForm({ brand_code: "", start_date: "", end_date: "", multiplier: "1", note: "" });
      setEditingOverrideId(null);
      await refreshSummary(); setNotice("Priority override saved for the selected dates.");
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
    const beforeEmployeeCode = item.employee_code;
    const nextEmployeeCode = employeeCode || null;
    if (beforeEmployeeCode === nextEmployeeCode) return;
    const currentRunId = roster.roster_run_id;
    const beforeUncovered = roster.generation?.uncovered_slots ?? 0;
    await runAction(async () => {
      await request(`/api/v1/rosters/${currentRunId}/assignments/${item.assignment_id}`, {
        method: "PATCH", body: JSON.stringify({ employee_code: nextEmployeeCode }),
      });
      const updated = await loadRoster(currentRunId);
      await refreshSavedRosters();
      const afterUncovered = updated.generation?.uncovered_slots ?? beforeUncovered;
      rememberRosterChange({ kind: "assignment", label: `${item.brand_code} · ${item.process_code} on ${item.date}`,
        run_id: currentRunId, assignment_id: item.assignment_id, before_employee_code: beforeEmployeeCode,
        after_employee_code: nextEmployeeCode, before_uncovered: beforeUncovered, after_uncovered: afterUncovered });
      setNotice(afterUncovered > beforeUncovered
        ? `This edit increased uncovered coverage from ${beforeUncovered} to ${afterUncovered}. Use Undo to restore the prior assignment.`
        : `Assignment saved and rechecked. Uncovered coverage: ${afterUncovered}. Use Undo or Redo to review this change.`);
    });
  }

  async function publishRoster() {
    if (!roster) return;
    await runAction(async () => {
      await request(`/api/v1/rosters/${roster.roster_run_id}/publish`, { method: "POST" });
      await loadRoster(roster.roster_run_id);
      await refreshSavedRosters();
      setUndoHistory([]);
      setRedoHistory([]);
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
        demand_units: ruleDemandUnits === "" ? null : Number(ruleDemandUnits),
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
    const previousRoster = roster;
    await runAction(async () => {
      const result = await request<{ roster_run_id: number; uncovered_slots: number; unaffected_assignments_preserved: boolean }>(
        `/api/v1/rosters/${change.roster_run_id}/replan`, {
          method: "POST",
          body: JSON.stringify({ affected_dates: change.affected_dates, replacement: selectedReplacement }),
        },
      );
      setComparison({ uncovered_slots: result.uncovered_slots, preserved: result.unaffected_assignments_preserved });
      const updated = await loadRoster(result.roster_run_id);
      rememberRosterChange({ kind: "roster", label: `apply replan for ${updated.month}/${updated.year}`,
        before_run_id: previousRoster?.roster_run_id ?? null, after_run_id: updated.roster_run_id,
        before_year_month: previousRoster ? `${previousRoster.year}-${previousRoster.month}` : null,
        after_year_month: `${updated.year}-${updated.month}`,
        before_uncovered: previousRoster?.generation?.uncovered_slots ?? null,
        after_uncovered: updated.generation?.uncovered_slots ?? result.uncovered_slots });
      await refreshSavedRosters();
      setChange(null);
      setChangePreview(null);
      setSelectedReplacement(null);
      const previousUncovered = previousRoster?.generation?.uncovered_slots;
      const nextUncovered = updated.generation?.uncovered_slots ?? result.uncovered_slots;
      setNotice(previousUncovered !== undefined && nextUncovered > previousUncovered
        ? `This replan increased uncovered coverage from ${previousUncovered} to ${nextUncovered}. Use Undo to restore the previous roster.`
        : result.uncovered_slots === 0
          ? `Proposed roster applied as draft #${result.roster_run_id}. All affected coverage is allocated. Use Undo or Redo to review this change.`
          : `Proposed roster applied as draft #${result.roster_run_id}. ${result.uncovered_slots} affected slot(s) still need allocation. Use Undo or Redo to review this change.`);
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

    return <div className={cn("rounded-xl border border-[#e5e5ea] bg-white p-4")}>
      <div className={cn("mb-3 flex items-center justify-between gap-3 [&_h3]:m-0 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-[#1d1d1f] [&_p]:mt-1 [&_p]:text-xs [&_p]:text-[#6e6e73]")}>
        <div>
          <span className={cn("text-[10px] font-semibold uppercase tracking-wide text-[#6e6e73]")}>PROPOSED CHANGE · {changeKind.toUpperCase()}</span>
          <h3>{change.affected_dates.length} affected date(s)</h3>
          <p>{availabilityNotice}</p>
        </div>
        <button className={cn("inline-flex min-h-10 items-center justify-center rounded-lg bg-[#003459] px-4 text-[13px] font-semibold text-white shadow-sm hover:bg-[#002c4b] disabled:cursor-wait disabled:opacity-50")} disabled={busy || !availabilityBlocksScheduling || !changePreview} onClick={() => void confirmReplan()}>
          {change.approval_status === "pending" && !change.availability_replan_required ? "Waiting for approval"
            : change.approval_status === "declined" && !change.availability_replan_required ? "No replan needed"
            : !changePreview ? "Preparing proposal…"
              : selectedReplacement ? `Apply plan with ${selectedReplacement.employee_code} →` : "Apply proposed roster →"}
        </button>
      </div>
      {changePreview && <>
        <div className={cn("mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-[#dbe8df] bg-[#eaf5ee] p-3 text-xs text-[#376c51] [&_strong]:font-semibold [&_b]:font-bold [&_button]:ml-auto [&_button]:border-0 [&_button]:bg-transparent [&_button]:text-lg [&_button]:text-current my-3 flex flex-wrap gap-x-4 gap-y-2")}>
          <strong>{change.brand_code ? `${change.brand_code} · ${change.process_code}` : change.employee_code ? `${change.employee_code}'s proposed roster` : "Proposed roster"}</strong>
          <span>{changePreview.required_slots} required</span>
          <span>{changePreview.filled_slots} allocated</span>
          <span>{changePreview.uncovered_slots} still need allocation</span>
          <span>Other dates stay as they are</span>
        </div>
        {changePreview.recommendations.length > 0 && <div className={cn("flex flex-col divide-y divide-[#f0f2f3]")}>
          <div className={cn("flex items-center justify-between gap-3 [&_span]:text-[10px] [&_span]:text-[#6e6e73]")}><strong>Recommended allocation changes</strong><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => setShowRetained((value) => !value)}>{showRetained ? "Hide unchanged assignments" : `Show all ${changePreview.recommendations.length} assignments`}</button></div>
          {visibleRecommendations.map((item, index) => <div className="grid grid-cols-[1fr_auto_auto] items-center gap-3 py-3 text-xs max-[760px]:grid-cols-2 [&>strong]:text-[#1d1d1f] [&>small]:text-[#6e6e73] [&>span:nth-child(3)]:text-[#6e6e73] [&>small]:max-[760px]:col-span-full" key={`${item.date}-${item.brand_code}-${item.process_code}-${index}`}>
            <span className={cn("text-[10px] text-[#6e6e73]")}>{new Date(`${item.date}T12:00:00`).toLocaleDateString("en", { month: "short", day: "numeric", weekday: "short" })}</span>
            <strong>{item.brand_code} · {item.process_code}</strong>
            <span>{item.current_employee_code ?? "—"}<span className={cn("text-[#7d8cc4]")}>→</span>{item.proposed_employee_code ?? (item.proposed_status === "not_required" ? "Not required" : "Unfilled")}</span>
            <span className={cn("rounded-full bg-[#e9f5ed] px-2 py-1 text-[10px] font-semibold text-[#397353]")}>{recommendationLabel[item.action]}</span>
            <small>{item.explanation}</small>
          </div>)}
          {visibleRecommendations.length === 0 && <p className={cn("text-xs leading-relaxed text-[#6e6e73] mt-3")}>The solver can keep the current assignments and still meet demand.</p>}
        </div>}
      </>}
      {candidates.length > 0 ? <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}>{candidates.map((result) => <div className={cn("rounded-lg border border-[#e5e5ea] bg-white p-3 [&>strong]:flex [&>strong]:items-center [&>strong]:justify-between [&>strong]:text-sm [&>strong]:font-semibold [&>strong]:text-[#1d1d1f] [&>strong_span]:text-[10px] [&>strong_span]:font-medium [&>strong_span]:text-[#6e6e73]")} key={`${result.date}-${result.brand_code}-${result.process_code}`}>
        <strong>{result.brand_code} · {result.process_code}<span>{new Date(`${result.date}T12:00:00`).toLocaleDateString("en", { month: "short", day: "numeric" })}</span></strong>
        {result.candidates.length ? result.candidates.map((candidate) => {
          const selected = selectedReplacement?.date === result.date && selectedReplacement.brand_code === result.brand_code
            && selectedReplacement.process_code === result.process_code && selectedReplacement.employee_code === candidate.employee_code;
          return <button className={cn(`flex cursor-pointer items-start gap-3 rounded-lg border border-[#e5e5ea] p-3 text-left hover:bg-[#f5f5f7] [&_small]:block [&_small]:text-[11px] [&_small]:text-[#6e6e73] ${selected ? "border-[#7d8cc4] bg-[#e9ecf6]" : ""}`)} key={candidate.employee_code}
            disabled={busy} onClick={() => void chooseCandidate(result, candidate)}>
            <span>{candidate.employee_code} · {candidate.name}</span>
            <small>{candidate.eligibility_type} · {candidate.projected_weekly_hours}h this week {selected ? "· in proposal" : "· use in proposal"}</small>
          </button>;
        }) : <p className={cn("rounded-lg bg-[#f5f5f7] p-4 text-xs text-[#6e6e73]")}>{result.explanation}{result.common_blockers && Object.keys(result.common_blockers).length > 0 ? ` ${Object.entries(result.common_blockers).map(([reason, count]) => `${count} ${reason}`).join(" · ")}.` : ""}</p>}
      </div>)}</div> : changePreview?.uncovered_slots ? <p className={cn("text-xs leading-relaxed text-[#6e6e73] mt-3")}>No qualified, available employee can take one or more of these slots. The recommendation list gives the reason and available alternatives.</p>
          : changePreview ? <p className={cn("text-xs leading-relaxed text-[#6e6e73] mt-3")}>The proposed plan meets demand on the affected dates. Review the assignments above and apply the plan when ready.</p>
            : change.approval_status === "pending" ? <p className={cn("text-xs leading-relaxed text-[#6e6e73] mt-3")}>No roster change is proposed unless this request is approved.</p>
              : change.approval_status === "declined" ? <p className={cn("text-xs leading-relaxed text-[#6e6e73] mt-3")}>The declined request leaves the current roster unchanged.</p> : null}
    </div>;
  }

  const pageInfo: Record<Page, { title: string; description: string }> = {
    roster: { title: "Roster", description: "Monthly staffing plan" },
    daily_assignments: { title: "Daily assignments", description: "Assignments and coverage" },
    employee_calendar: { title: "Employee calendar", description: "Daily status by employee" },
    employees: { title: "Employees & leave", description: "Team and availability" },
    brands: { title: "Brands", description: "Clients and demand rules" },
  };

  if (role !== "manager") {
    return <ReadOnlyDashboard role={role} actorCode={actorCode} identities={identities}
      summary={summary} roster={roster} savedRosters={savedRosters} validation={validation}
      onRoleChange={changeDemoRole} onIdentityChange={changeDemoIdentity}
      onLoadRoster={(id) => void loadRoster(id)} />;
  }

  return <div className={cn("grid min-h-screen grid-cols-[248px_minmax(0,1fr)] bg-white max-[760px]:flex max-[760px]:flex-col")}>
    <aside className={cn("sticky top-0 flex min-h-screen flex-col self-start border-r border-[#e5e5ea] bg-white px-4 py-6 max-[760px]:static max-[760px]:min-h-0 max-[760px]:gap-2 max-[760px]:border-r-0 max-[760px]:border-b max-[760px]:px-4 max-[760px]:py-3")}>
      <div className={cn("flex items-center gap-2.5 px-2 pb-7 max-[760px]:px-0 max-[760px]:pb-2")}><span className={cn("grid size-[34px] shrink-0 place-items-center rounded-[10px] bg-[#003459] text-[12px] font-extrabold tracking-[-1px] text-white shadow-sm")}>DS</span><div><strong>DataStraw</strong><small>Smart Roster</small></div></div>
      <div className={cn("px-3 pb-2 text-[10px] font-semibold tracking-[0.08em] text-[#68717c] max-[760px]:hidden")}>WORKSPACE</div>
      <nav className={cn("flex flex-col gap-[5px] p-0 max-[760px]:flex-row max-[760px]:gap-1 max-[760px]:overflow-x-auto max-[760px]:overscroll-x-contain")} aria-label="Dashboard pages">
        {(["roster", "daily_assignments", "employee_calendar", "employees", "brands"] as Page[]).map((item) => <button key={item}
          className={cn(`flex min-h-12 w-full items-center gap-3 rounded-[11px] px-2.5 text-left text-[14px] font-medium text-[#424245] transition-colors hover:bg-[#f5f5f7] max-[760px]:min-h-[42px] max-[760px]:w-auto max-[760px]:min-w-max max-[760px]:px-[9px] max-[760px]:text-xs [&_small]:ml-auto [&_small]:rounded-md [&_small]:bg-white [&_small]:px-1.5 [&_small]:py-0.5 [&_small]:text-[11px] [&_small]:font-semibold [&_small]:text-[#003459] ${page === item ? "bg-[#e9ecf6] text-[#003459] hover:bg-[#e9ecf6]" : ""}`)} aria-current={page === item ? "page" : undefined}
          onClick={() => navigate(item)}>
          <span className={cn("grid size-6 shrink-0 place-items-center bg-transparent text-[#003459] [&_svg]:size-[23px] [&_svg]:stroke-[1.8] max-[760px]:[&_svg]:size-[21px]")} aria-hidden="true">{item === "roster" ? <IconLayoutDashboardFilled /> : item === "daily_assignments" ? <IconListCheckFilled /> : item === "employee_calendar" ? <IconCalendarFilled /> : item === "employees" ? <IconUserFilled /> : <IconBriefcaseFilled />}</span>
          <span>{item === "roster" ? "Roster" : item === "daily_assignments" ? "Daily assignments" : item === "employee_calendar" ? "Employee calendar" : item === "employees" ? "People & leave" : "Brands"}</span>
          {item === "roster" && roster && <small className={cn("ml-auto rounded-md bg-white px-1.5 py-0.5 text-[11px] font-semibold text-[#003459]")}>#{roster.roster_run_id}</small>}
        </button>)}
      </nav>
      <div className={cn("mt-auto flex flex-col gap-2 border-t border-[#e5e5ea] px-2 pt-4 max-[760px]:mt-1 max-[760px]:flex-row max-[760px]:items-center max-[760px]:justify-between max-[760px]:gap-3 max-[760px]:px-0 max-[760px]:pt-2.5 [&_label]:text-xs [&_label]:font-semibold [&_label]:text-[#68717c] [&_select]:h-10 [&_select]:w-full [&_select]:rounded-[9px] [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:font-medium [&_select]:text-[#003459] [&_select]:outline-none [&_select]:focus-visible:outline-2 [&_select]:focus-visible:outline-offset-2 [&_select]:focus-visible:outline-[#7d8cc4] [&_select]:max-[760px]:h-9 [&_select]:max-[760px]:w-[min(220px,65%)]")}>
        <label htmlFor="manager-role-select">View as</label>
        <select id="manager-role-select" value={role} onChange={(event) => changeDemoRole(event.target.value as Role)}>
          <option value="manager">Manager</option><option value="pod_lead">Pod lead</option><option value="employee">Employee</option>
        </select>
      </div>
    </aside>
    <div className={cn("min-w-0")}>
    <main className={cn("mx-auto w-full max-w-[1600px] px-[34px] pb-14 pt-[30px] max-[760px]:px-[18px] max-[760px]:pb-9 max-[760px]:pt-[22px]")}>
    <section className={cn("flex justify-between items-end [&_h1]:m-0 [&_h1]:text-[32px] [&_h1]:font-semibold [&_h1]:leading-[1.12] [&_h1]:tracking-[-0.8px] [&_h1]:text-[#1d1d1f] [&_h1]:max-[760px]:text-[29px] [&_p]:mt-[7px] [&_p]:text-[13px] [&_p]:leading-[1.35] [&_p]:text-[#6e6e73] mb-0 min-h-[93px] items-center pb-[25px] max-[760px]:min-h-0 max-[760px]:flex-col max-[760px]:items-start max-[760px]:gap-3 max-[760px]:pb-5")}>
      <div><h1>{pageInfo[page].title}</h1><p>{pageInfo[page].description}</p></div>
    </section>

    {error && <div className={cn("mb-3 flex items-center gap-3 rounded-lg border p-3 text-xs [&_button]:ml-auto [&_button]:border-0 [&_button]:bg-transparent [&_button]:text-lg [&_button]:text-current border-[#f3d7d0] bg-[#fff0ec] text-[#9f5445] [&_strong]:font-semibold")}><strong>Couldn’t complete that action</strong><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss">×</button></div>}
    {notice && <div className={cn("mb-3 flex items-center gap-3 rounded-lg border p-3 text-xs [&_button]:ml-auto [&_button]:border-0 [&_button]:bg-transparent [&_button]:text-lg [&_button]:text-current border-[#dbe8df] bg-[#eaf5ee] text-[#376c51]")}><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss">×</button></div>}
    {comparison && <div className={cn("mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-[#dbe8df] bg-[#eaf5ee] p-3 text-xs text-[#376c51] [&_strong]:font-semibold [&_b]:font-bold [&_button]:ml-auto [&_button]:border-0 [&_button]:bg-transparent [&_button]:text-lg [&_button]:text-current")}><strong>Proposed roster applied</strong><span>{comparison.uncovered_slots === 0 ? "All required coverage is allocated." : `${comparison.uncovered_slots} slot(s) still need a manager allocation.`}</span><span>{comparison.preserved ? "Assignments on other dates stayed unchanged." : "Some assignments on other dates changed."}</span><button onClick={() => setComparison(null)} aria-label="Dismiss">×</button></div>}

    <section className={cn("grid grid-cols-1 items-start gap-[22px]")}>
      <div className={cn("flex min-w-0 flex-col gap-5")}>
        {page === "roster" && <>
          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[22px_24px] max-[760px]:p-[17px]")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><h2>Roster generator</h2><p>Choose a planning month</p></div></div>
            <form className={cn("grid grid-cols-[130px_145px_minmax(190px,1fr)_auto] items-end gap-3.5 max-[760px]:grid-cols-2 [&_label]:flex [&_label]:flex-col [&_label]:gap-[7px] [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:min-h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:min-h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20")} onSubmit={(event) => { event.preventDefault(); void runAction(async () => {
              const previousRoster = roster;
              const result = await request<{ roster_run_id: number; result_status: string }>("/api/v1/rosters/generate", { method: "POST", body: JSON.stringify({ year, month }) });
              const updated = await loadRoster(result.roster_run_id); setChange(null); setComparison(null); setChangePreview(null); setSelectedReplacement(null);
              const sameMonth = previousRoster?.year === updated.year && previousRoster.month === updated.month;
              const beforeUncovered = previousRoster?.generation?.uncovered_slots;
              const afterUncovered = updated.generation?.uncovered_slots ?? 0;
              rememberRosterChange({ kind: "roster", label: `generate roster for ${updated.month}/${updated.year}`,
                before_run_id: previousRoster?.roster_run_id ?? null, after_run_id: updated.roster_run_id,
                before_year_month: previousRoster ? `${previousRoster.year}-${previousRoster.month}` : null,
                after_year_month: `${updated.year}-${updated.month}`,
                before_uncovered: sameMonth ? beforeUncovered ?? null : null,
                after_uncovered: afterUncovered });
              await refreshSavedRosters();
              setNotice(sameMonth && beforeUncovered !== undefined && afterUncovered > beforeUncovered
                ? `The new draft increased uncovered coverage from ${beforeUncovered} to ${afterUncovered}. Use Undo to restore the previous draft.`
                : `Roster ${result.roster_run_id} generated as a ${result.result_status} draft. Use Undo or Redo to review this change.`);
            }); }}>
              <label>Year<input type="number" min={today.getFullYear()} max={today.getFullYear() + 5} value={year} onChange={(event) => setYear(Number(event.target.value))} required /></label>
              <label>Month<select value={month} onChange={(event) => setMonth(Number(event.target.value))}>{Array.from({ length: 12 }, (_, index) => <option key={index + 1} value={index + 1}>{new Date(2026, index, 1).toLocaleString("en", { month: "long" })}</option>)}</select></label>
              <div className={cn("inline-flex items-center gap-2 max-[760px]:col-span-full")} aria-label="Roster change history"><Button type="button" variant="outline" className={cn("h-10 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-3 text-xs font-medium text-[#424245] hover:bg-[#e9ecf6] disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-4")} disabled={busy || undoHistory.length === 0} onClick={() => void undoRosterChange()}><IconArrowBackUp aria-hidden="true" /> Undo</Button><Button type="button" variant="outline" className={cn("h-10 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-3 text-xs font-medium text-[#424245] hover:bg-[#e9ecf6] disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-4")} disabled={busy || redoHistory.length === 0} onClick={() => void redoRosterChange()}><IconArrowForwardUp aria-hidden="true" /> Redo</Button></div>
              <Button className={cn("min-h-10 rounded-lg bg-[#003459] px-4 text-[13px] font-semibold text-white shadow-none transition-colors hover:bg-[#002c4b] max-[760px]:col-span-full max-[760px]:w-full")} disabled={busy}>{busy ? "Working…" : roster ? "Generate new draft" : "Generate roster"}</Button>
            </form>
          </section>

          {roster ? <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[22px_24px] pb-0 max-[760px]:p-[17px]")}>
            <div className="mb-4 flex items-center justify-between gap-3 max-[760px]:flex-wrap">
              <div><h2>Roster review</h2><p>{new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })} plan</p></div>
              {roster.status === "draft" && <button className="inline-flex min-h-10 items-center justify-center rounded-lg bg-[#003459] px-4 text-[13px] font-semibold text-white shadow-sm hover:bg-[#002c4b] disabled:cursor-wait disabled:opacity-50" disabled={busy} title="Recheck the roster and publish if it meets the required rules." onClick={() => void publishRoster()}>{busy ? "Checking…" : "Publish roster"}</button>}
            </div>
            <div className={cn("mb-5 grid grid-cols-4 gap-2.5 max-[760px]:grid-cols-2")}>
              <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Required slots</span><strong>{roster.generation?.required_slots ?? 0}</strong></div>
              <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Covered</span><strong>{roster.generation?.filled_slots ?? 0}</strong></div>
              <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Uncovered</span><strong>{roster.generation?.uncovered_slots ?? 0}</strong></div>
              <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Conflicts / gaps</span><strong>{validation?.error_count ?? 0} / {validation?.gap_count ?? 0}</strong></div>
            </div>
            <div className={cn("overflow-hidden rounded-[10px] border border-[#e5e5ea]")}>
              <div className={cn("flex items-center justify-between border-b border-[#edf0f1] bg-[#fbfcfc] px-3.5 py-3 [&_h3]:m-0 [&_h3]:text-[11px] [&_h3]:font-bold [&_h3]:text-[#1d1d1f]")}><div><h3>Coverage</h3><div className={cn("mt-1 flex flex-wrap gap-2.5 [&_span]:text-[10px] [&_span]:text-[#6e6e73]")} aria-label={`${filteredErrorCount} conflicts, ${filteredGapCount} gaps, ${filteredWarningCount} warnings`}><span>{filteredErrorCount} conflicts</span><span>{filteredGapCount} gaps</span><span>{filteredWarningCount} warnings</span></div></div><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} disabled={busy} onClick={() => void runAction(async () => { await loadRoster(roster.roster_run_id); setNotice("Roster validation refreshed against the latest saved inputs."); })}>Refresh</button></div>
              <div className={cn("max-h-[240px] overflow-auto overscroll-contain")}>
                {coverageIssues.slice(0, 3).map(renderValidationIssue)}
                {coverageIssues.length > 3 && <details className={cn("border-t border-[#f0f2f3] py-2 pl-[42px] pr-3.5")}><summary>Show {coverageIssues.length - 3} more issues</summary><div className={cn("mt-2 -ml-[42px] [&>.issue-row]:pl-0 [&>.issue-row]:pr-0")}>{coverageIssues.slice(3).map((issue, index) => renderValidationIssue(issue, index + 3))}</div></details>}
                {filteredErrorCount === 0 && filteredGapCount === 0 && <div className={cn("p-4 text-[13px] text-[#328360]")}>No mandatory coverage gaps or hard-rule violations found.</div>}
                {warningIssues.slice(0, 1).map(renderValidationIssue)}
                {warningIssues.length > 1 && <details className={cn("border-t border-[#f0f2f3] py-2 pl-[42px] pr-3.5")}><summary>Show {warningIssues.length - 1} more warnings</summary><div className={cn("mt-2 -ml-[42px] [&>.issue-row]:pl-0 [&>.issue-row]:pr-0")}>{warningIssues.slice(1).map((issue, index) => renderValidationIssue(issue, index + 1))}</div></details>}
              </div>
            </div>
          </section> : <div className={cn("flex min-h-[220px] flex-col items-center justify-center rounded-[14px] border border-[#e5e5ea] bg-white p-8 text-center shadow-[0_4px_16px_rgb(30_55_43_/_4%)] [&_h2]:m-0 [&_h2]:text-[18px] [&_h2]:font-semibold [&_h2]:text-[#1d1d1f] [&_p]:mt-2 [&_p]:text-[13px] [&_p]:text-[#6e6e73]")}><h2>Your roster starts here</h2><p>Create a monthly draft</p></div>}
        </>}

        {page === "daily_assignments" && (roster ? <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4 min-w-0")}>
          <div className="mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start"><div><div><h2>Daily assignments</h2><p>{new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })} · {visibleAssignments.length} rows</p></div></div></div>
          <div className={cn("mb-5 grid grid-cols-4 gap-2.5 max-[760px]:grid-cols-2")}>
            <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Required slots · filtered</span><strong>{filteredRequiredSlots}</strong></div>
            <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Covered · filtered</span><strong>{filteredFilledSlots}</strong></div>
            <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Uncovered · filtered</span><strong>{filteredUncoveredSlots}</strong></div>
            <div className={cn("rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 [&_span]:block [&_span]:text-base [&_span]:text-[#1d1d1f] [&_strong]:mt-1 [&_strong]:block [&_strong]:text-base [&_strong]:font-bold [&_strong]:text-[#1d1d1f]")}><span>Conflicts / gaps · filtered</span><strong>{filteredErrorCount} / {filteredGapCount}</strong></div>
          </div>
          <div className={cn("flex items-center justify-between gap-3.5 py-5 pb-3 max-[760px]:flex-col max-[760px]:items-start [&_h3]:m-0 [&_h3]:text-[12px] [&_h3]:font-bold [&_h3]:text-[#1d1d1f] [&_span]:mt-1 [&_span]:block [&_span]:text-[10px] [&_span]:text-[#6e6e73]")}><div><h3>Assignments</h3><span>{visibleAssignments.length} rows · {roster.status === "draft" ? "editable draft" : "published roster"}</span></div><div className={cn("flex flex-wrap gap-2 max-[760px]:w-full [&_input]:h-8 [&_input]:min-h-8 [&_input]:rounded-md [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-2 [&_input]:text-[11px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_input]:max-[760px]:min-w-0 [&_select]:h-8 [&_select]:min-h-8 [&_select]:rounded-md [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-2 [&_select]:text-[11px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 [&_select]:max-[760px]:min-w-0")}><input aria-label="Filter date" type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /><select aria-label="Filter by pod" value={podFilter} onChange={(event) => setPodFilter(event.target.value)}><option value="all">All pods</option>{summary?.pods.map((pod) => <option key={pod.id} value={pod.name}>{pod.name}{pod.lead_employee_code ? ` · lead ${pod.lead_employee_code}` : " · no lead"}</option>)}<option value="__unassigned__">Unassigned / legacy</option></select><select aria-label="Filter by brand" value={brandFilter} onChange={(event) => { setBrandFilter(event.target.value); setProcessFilter("all"); }}><option value="all">All brands</option>{summary?.brands.map((brand) => <option key={brand.code} value={brand.code}>{brand.code}</option>)}</select><select aria-label="Filter by process" value={processFilter} onChange={(event) => setProcessFilter(event.target.value)}><option value="all">All processes</option>{visibleProcesses.map((process) => <option key={process} value={process}>{process}</option>)}</select><select aria-label="Filter by employee" value={assignmentEmployeeFilter} onChange={(event) => setAssignmentEmployeeFilter(event.target.value)}><option value="all">All employees</option>{activeEmployees.map((employee) => <option key={employee.code} value={employee.code}>{employee.code}</option>)}</select></div></div>
          <div className={cn("max-h-[480px] overflow-auto [&_table]:w-full [&_table]:border-collapse [&_table]:whitespace-nowrap [&_table]:text-left [&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:border-y [&_th]:border-[#edf0f1] [&_th]:bg-[#f8f9f9] [&_th]:px-3.5 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-[10px] [&_th]:font-bold [&_th]:tracking-[0.03em] [&_th]:text-[#6e6e73] [&_td]:border-b [&_td]:border-[#f0f2f3] [&_td]:px-3.5 [&_td]:py-3 [&_td]:align-top [&_td]:text-[13px] [&_td]:text-[#424245] mx-[-23px] mb-[-4px] max-h-[560px] max-[760px]:mx-[-16px] [&_table]:min-w-[700px] max-h-[560px]")}><table><thead><tr><th>Date</th><th>Brand</th><th>Process</th><th>Pod / work window</th><th>Employee</th><th>Coverage</th><th>Hours</th><th>Status</th><th>Allocation reason / manager edit</th></tr></thead><tbody>{visibleAssignments.map((item) => <tr key={item.assignment_id}><td>{new Date(`${item.date}T12:00:00`).toLocaleDateString("en", { day: "2-digit", month: "short", weekday: "short" })}</td><td>{item.brand_code}<small className={cn("mt-1 block text-[10px] text-[#8b9499]")}>Priority {item.deadline_priority}/5</small>{item.priority_score && <small className={cn("mt-1 block text-[10px] text-[#8b9499]")}>Score {item.priority_score.score} · tier {item.priority_score.client_tier} × urgency {item.priority_score.urgency} × backlog {item.priority_score.backlog_factor} × override {item.priority_score.manager_override}</small>}</td><td>{item.process_code}</td><td>{item.pod_name ?? "Legacy day slot"}{item.shift && <small className={cn("mt-1 block text-[10px] text-[#8b9499]")}>{item.shift.name} · {item.shift.start}–{item.shift.end} · lunch {item.shift.break_start}–{item.shift.break_end}</small>}</td><td>{item.employee_code ?? <span className={cn("font-medium text-[#bc6550]")}>Uncovered</span>}</td><td>{coverageLabel(item)}</td><td>{item.effort_hours}h</td><td><span className={cn(`inline-flex rounded-full bg-[#f1f3f9] px-2 py-1 text-[10px] font-semibold capitalize text-[#003459] ${item.status === "working" ? "bg-[#e6f2e9] text-[#276342]" : item.status === "uncovered" ? "bg-[#fff0ec] text-[#b25242]" : item.status === "leave" || item.status === "unavailable" ? "bg-[#f0eafa] text-[#634b8a]" : item.status === "active" || item.status === "approved" ? "bg-[#e6f2e9] text-[#276342]" : item.status === "declined" ? "bg-[#fff0ec] text-[#b25242]" : item.status === "pending" ? "bg-[#fff5e6] text-[#a57020]" : "bg-[#f1f3f9] text-[#6e6e73]"}`)}>{item.status}</span></td><td><small className={cn("text-[11px] leading-[1.45] text-[#6e6e73]")}>{item.explanation}</small>{roster.status === "draft" && <div className={cn("mt-1.5 flex flex-col gap-1 [&_select]:h-[30px] [&_select]:max-w-[230px] [&_select]:rounded-md [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-2 [&_select]:text-[10px] [&_select]:text-[#34413b]")}><select aria-label={`Assign ${item.brand_code} ${item.process_code} on ${item.date}`} value={item.employee_code ?? ""} onChange={(event) => void editAssignment(item, event.target.value)} disabled={busy}><option value="">Unassigned</option>{item.employee_code && !item.available_candidates.some((employee) => employee.employee_code === item.employee_code) && <option value={item.employee_code} disabled>{item.employee_code} · current (blocked by rule)</option>}{item.available_candidates.map((employee) => <option key={employee.employee_code} value={employee.employee_code}>{employee.employee_code} · {employee.name} · {employee.eligibility_type}</option>)}</select></div>}</td></tr>)}</tbody></table>{visibleAssignments.length === 0 && <div className={cn("p-4 text-center text-xs text-[#6e6e73]")}>No assignments match the selected filters.</div>}</div>
        </section> : <div className={cn("flex min-h-[220px] flex-col items-center justify-center rounded-[14px] border border-[#e5e5ea] bg-white p-8 text-center shadow-[0_4px_16px_rgb(30_55_43_/_4%)] [&_h2]:m-0 [&_h2]:text-[18px] [&_h2]:font-semibold [&_h2]:text-[#1d1d1f] [&_p]:mt-2 [&_p]:text-[13px] [&_p]:text-[#6e6e73]")}><h2>Generate a roster first</h2><p>Daily assignments will appear here.</p></div>)}

        {page === "employee_calendar" && (roster ? <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
          <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Employee daily status</h2><p>{new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })} · availability by day</p></div></div></div>
          <div className={cn("my-2 flex items-end justify-between gap-4 max-[760px]:flex-col max-[760px]:items-stretch max-[760px]:gap-3 [&_label]:flex [&_label]:min-w-[min(300px,45%)] [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_label]:max-[760px]:w-full [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20")}><label>Employee<select aria-label="Filter calendar by employee" value={visibleEmployeeDayCode} onChange={(event) => setEmployeeDayFilter(event.target.value)}>{activeEmployees.map((employee) => <option key={employee.code} value={employee.code}>{employee.name} · {employee.code}</option>)}</select></label></div>
          <div className={cn("mb-3 flex items-baseline gap-2 text-xs text-[#6e6e73] [&_strong]:text-sm [&_strong]:font-semibold [&_strong]:text-[#1d1d1f]")}><strong>{visibleEmployee?.name ?? "Employee"}</strong><span>{visibleEmployee?.code ?? ""}</span><span>{roster.assignments.filter((item) => item.employee_code === visibleEmployeeDayCode && item.status === "working").length} assignments · {visibleEmployeeDays.reduce((total, item) => total + item.assigned_effort_hours, 0)}h scheduled</span></div>
          <div className={cn("grid grid-cols-7 gap-2 max-[760px]:gap-1")} role="grid" aria-label={`${visibleEmployee?.name ?? "Employee"} daily status for ${new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })}`}>
            {weekdays.map((day) => <div className={cn("px-2 pb-1 text-left text-[11px] font-semibold text-[#6e6e73] max-[760px]:px-1 max-[760px]:text-[10px]")} role="columnheader" key={day}>{day}</div>)}
            {employeeCalendarCells.map((cell, index) => cell ? <article className={cn(`flex min-h-[118px] min-w-0 flex-col items-start gap-2 rounded-[10px] border border-[#e5e5ea] bg-white p-2.5 max-[760px]:min-h-[102px] max-[760px]:gap-1.5 max-[760px]:p-1.5 max-[420px]:min-h-[92px] max-[420px]:p-1 ${cell.item ? (cell.item.status === "working" ? "border-[#d9e6dc] bg-[#fbfdfb]" : cell.item.status === "leave" || cell.item.status === "unavailable" ? "border-[#f3d7d0] bg-[#fffafa]" : "bg-[#f5f5f7]") : "bg-[#f8f8fa]"}`)} role="gridcell" key={cell.date} aria-label={`${cell.date}: ${cell.item?.status ?? "No record"}, ${cell.item?.assigned_effort_hours ?? 0} hours`}>
              <span className={cn("text-[12px] font-semibold text-[#1d1d1f]")}>{cell.day}</span>
              {cell.item ? <><span className={cn(`rounded-md bg-[#f1f3f9] px-1.5 py-1 text-[11px] font-medium capitalize text-[#003459] max-[760px]:px-1 max-[760px]:text-[10px] max-[420px]:text-[9px] ${cell.item.status === "working" ? "bg-[#e6f2e9] text-[#276342]" : cell.item.status === "leave" || cell.item.status === "unavailable" ? "bg-[#f0eafa] text-[#634b8a]" : "bg-[#f1f3f9] text-[#6e6e73]"}`)}>{cell.item.status.replace(/_/g, " ")}</span><span className={cn("text-[11px] text-[#6e6e73] max-[760px]:text-[10px] max-[420px]:text-[9px]")}>{cell.item.assigned_effort_hours}h assigned</span><span className={cn("truncate text-[10px] text-[#6e6e73] max-[760px]:hidden")}>{visibleEmployee?.name}</span></> : <span className={cn("text-[11px] text-[#8b9499] max-[760px]:text-[10px]")}>No record</span>}
            </article> : <div className={cn("flex min-h-[118px] min-w-0 flex-col items-start gap-2 rounded-[10px] border border-[#e5e5ea] bg-white p-2.5 max-[760px]:min-h-[102px] max-[760px]:gap-1.5 max-[760px]:p-1.5 max-[420px]:min-h-[92px] max-[420px]:p-1 border-transparent bg-transparent")} aria-hidden="true" key={`empty-${index}`} />)}
          </div>
          <div className={cn("mt-4 flex flex-wrap gap-4 text-xs text-[#6e6e73]")}><span><i className={cn("mr-1.5 inline-block size-2 rounded-full bg-[#4e9464]")} />Working</span><span><i className={cn("mr-1.5 inline-block size-2 rounded-full bg-[#d2d2d7]")} />Off / unscheduled</span><span><i className={cn("mr-1.5 inline-block size-2 rounded-full bg-[#886ab4]")} />Leave</span></div>
        </section> : <div className={cn("flex min-h-[220px] flex-col items-center justify-center rounded-[14px] border border-[#e5e5ea] bg-white p-8 text-center shadow-[0_4px_16px_rgb(30_55_43_/_4%)] [&_h2]:m-0 [&_h2]:text-[18px] [&_h2]:font-semibold [&_h2]:text-[#1d1d1f] [&_p]:mt-2 [&_p]:text-[13px] [&_p]:text-[#6e6e73]")}><h2>Generate a roster first</h2><p>Employee schedules will appear here.</p></div>)}

        {page === "employees" && <>
          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Employee directory</h2><p>Search and edit people</p></div></div><span className={cn("ml-auto whitespace-nowrap text-[11px] text-[#6e6e73] max-[760px]:ml-0")}>{activeEmployees.length} active · {summary?.brands.length ?? 0} brands</span><input className={cn("h-9 w-[230px] rounded-lg border border-[#d2d2d7] bg-white px-2.5 text-[13px] text-[#1d1d1f] outline-none placeholder:text-[#8b9499] focus:border-[#7d8cc4] focus:ring-2 focus:ring-[#7d8cc4]/20 max-[760px]:w-full")} aria-label="Search employees" placeholder="Search people" value={employeeSearch} onChange={(event) => setEmployeeSearch(event.target.value)} /></div>
            {summary ? <div className={cn("max-h-[480px] overflow-auto [&_table]:w-full [&_table]:border-collapse [&_table]:whitespace-nowrap [&_table]:text-left [&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:border-y [&_th]:border-[#edf0f1] [&_th]:bg-[#f8f9f9] [&_th]:px-3.5 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-[10px] [&_th]:font-bold [&_th]:tracking-[0.03em] [&_th]:text-[#6e6e73] [&_td]:border-b [&_td]:border-[#f0f2f3] [&_td]:px-3.5 [&_td]:py-3 [&_td]:align-top [&_td]:text-[13px] [&_td]:text-[#424245] mx-[-23px] mb-[-4px] max-h-[560px] max-[760px]:mx-[-16px] [&_table]:min-w-[700px]")}><table><thead><tr><th>Employee</th><th>Role</th><th>Pods</th><th>Skills</th><th>Brand/process eligibility</th><th>Weekly offs</th><th>Status</th><th /></tr></thead><tbody>{visibleEmployees.map((employee) => <tr key={employee.code}><td><strong>{employee.code} · {employee.name}</strong>{employee.is_pod_lead && <small className={cn("mt-1 block text-[10px] text-[#8b9499]")}>Pod lead</small>}</td><td>{employee.role}</td><td>{employee.pods.join(", ")}<small className={cn("mt-1 block text-[10px] text-[#8b9499]")}>Primary: {employee.pod}</small></td><td><div className={cn("flex flex-wrap gap-1")}>{employee.skills.map((skill) => <span className={cn("rounded-md bg-[#f1f3f9] px-1.5 py-1 text-[10px] font-semibold text-[#003459]")} key={skill}>{skill}</span>)}</div></td><td><div className={cn("flex flex-wrap gap-1")}>{rules.filter((rule) => rule.eligible_employees.some((eligible) => eligible.employee_code === employee.code && eligible.active)).map((rule) => <span className={cn("rounded-md bg-[#f1f3f9] px-1.5 py-1 text-[10px] font-semibold text-[#003459]")} key={rule.rule_id}>{rule.brand_code} · {rule.code}</span>)}</div></td><td>{employee.weekly_off_weekdays.map((day) => weekdays[day]).join(", ")}</td><td><span className={cn(`inline-flex rounded-full bg-[#f1f3f9] px-2 py-1 text-[10px] font-semibold capitalize text-[#003459] ${employee.status === "active" ? "bg-[#e6f2e9] text-[#276342]" : "bg-[#f1f3f9] text-[#6e6e73]"}`)}>{employee.status}</span></td><td><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45 inline-flex items-center gap-1.5 [&_svg]:size-4")} onClick={() => startEditingEmployee(employee.code)}><IconPencil aria-hidden="true" /> Edit</button></td></tr>)}</tbody></table></div> : <p className={cn("text-xs leading-relaxed text-[#6e6e73]")}>Loading employees…</p>}
          </section>

          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>{employeeEditCode ? "Update employee" : "Add employee"}</h2><p>Details, skills, capacity</p></div></div><select value={employeeEditCode} onChange={(event) => startEditingEmployee(event.target.value)}><option value="">New employee</option>{summary?.employees.map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></div>
            <form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void saveEmployee(event)}>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Employee code<input required maxLength={20} value={employeeForm.employee_code} onChange={(event) => setEmployeeForm({ ...employeeForm, employee_code: event.target.value })} /></label><label>Full name<input required value={employeeForm.full_name} onChange={(event) => setEmployeeForm({ ...employeeForm, full_name: event.target.value })} /></label></div>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Role/title<input required value={employeeForm.role_title} onChange={(event) => setEmployeeForm({ ...employeeForm, role_title: event.target.value })} /></label><label>Primary pod<select value={employeeForm.pod_name} onChange={(event) => setEmployeeForm({ ...employeeForm, pod_name: event.target.value, pod_names: [...new Set([...employeeForm.pod_names, event.target.value])] })}>{summary?.pods.map((pod) => <option key={pod.id} value={pod.name}>{pod.name}</option>)}</select></label></div>
              <fieldset><legend>Additional pod membership</legend><div className={cn("flex flex-wrap gap-2 [&_label]:inline-flex [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2 [&_label]:rounded-full [&_label]:border [&_label]:border-[#d2d2d7] [&_label]:px-3 [&_label]:py-2 [&_label]:text-xs [&_label]:text-[#424245] [&_label.selected]:border-[#7d8cc4] [&_label.selected]:bg-[#e9ecf6] [&_label.selected]:text-[#003459] [&_input]:accent-[#003459]")}>{summary?.pods.filter((pod) => pod.name !== employeeForm.pod_name).map((pod) => <label key={pod.id} className={cn((employeeForm.pod_names.includes(pod.name) ? "border-[#7d8cc4] bg-[#e9ecf6] text-[#003459]" : ""))}><input type="checkbox" checked={employeeForm.pod_names.includes(pod.name)} onChange={() => setEmployeeForm({ ...employeeForm, pod_names: employeeForm.pod_names.includes(pod.name) ? employeeForm.pod_names.filter((name) => name !== pod.name) : [...employeeForm.pod_names, pod.name] })} />{pod.name}</label>)}</div><small className={cn("text-xs leading-relaxed text-[#6e6e73]")}>Membership makes the employee available for that pod’s work window. Skills and brand eligibility are still required.</small></fieldset>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Skill codes<input placeholder="CS, ACC" value={employeeForm.skills} onChange={(event) => { const skills = event.target.value; const hasCsSkill = skills.split(",").some((skill) => skill.trim().toUpperCase() === "CS"); setEmployeeForm((current) => ({ ...current, skills, max_hours_per_week: hasCsSkill && current.max_hours_per_week === "40" ? "45" : !hasCsSkill && Number(current.max_hours_per_week) > 40 ? "40" : current.max_hours_per_week })); }} /></label><label>Weekly off weekdays (0=Mon … 6=Sun)<input value={employeeForm.weekly_off_weekdays} onChange={(event) => setEmployeeForm({ ...employeeForm, weekly_off_weekdays: event.target.value })} /></label></div>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Daily allocatable hours<input type="number" min="1" max="8" value={employeeForm.daily_capacity_hours} onChange={(event) => setEmployeeForm({ ...employeeForm, daily_capacity_hours: event.target.value })} /></label><label>Weekly hours<input type="number" min="1" max={employeeForm.skills.split(",").some((skill) => skill.trim().toUpperCase() === "CS") ? 45 : 40} value={employeeForm.max_hours_per_week} onChange={(event) => setEmployeeForm({ ...employeeForm, max_hours_per_week: event.target.value })} /></label></div>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Max work days per week<input type="number" min="1" max="5" value={employeeForm.max_days_per_week} onChange={(event) => setEmployeeForm({ ...employeeForm, max_days_per_week: event.target.value })} /></label><label>Status<select value={employeeForm.status} onChange={(event) => setEmployeeForm({ ...employeeForm, status: event.target.value })}><option value="active">Active</option><option value="inactive">Inactive</option></select></label></div>
              <label className={cn("flex flex-row items-center gap-2 text-xs text-[#424245] [&_input]:accent-[#003459]")}><input type="checkbox" checked={employeeForm.is_pod_lead} onChange={(event) => setEmployeeForm({ ...employeeForm, is_pod_lead: event.target.checked })} /> Employee is the pod lead</label>
              <button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy}>{busy ? "Saving…" : employeeEditCode ? "Save employee details" : "Add employee"}</button>
            </form>
          </section>

          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Pods and pod leads</h2><p>Members and work windows</p></div></div><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} type="button" onClick={() => { setPodEditId(null); setPodName(""); setPodSpecialization(""); setPodLead(""); setPodMembers([]); setPodShift({ shift_name: "Day Window", shift_start: "09:00", shift_end: "18:00", break_start: "13:00", break_end: "14:00", allocation_capacity_hours: "6" }); document.getElementById("pod-edit-form")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>New pod</button></div>
            <div className={cn("mb-4 grid grid-cols-2 gap-2.5 max-[760px]:grid-cols-1")}>{(summary?.pods ?? []).map((pod) => <article className={cn("flex min-w-0 flex-col items-start gap-1 rounded-lg border border-[#e7ece8] bg-[#fbfcfb] p-3 [&_strong]:text-[11px] [&_strong]:font-bold [&_strong]:text-[#33443b] [&>span]:text-[10px] [&>span]:leading-relaxed [&>span]:text-[#7b8981] [&_small]:text-[10px] [&_small]:leading-relaxed [&_small]:text-[#7b8981]")} key={pod.id}><strong>{pod.name}</strong><span>{pod.specialization}</span><small>Lead: {pod.lead_employee_code ?? "Not assigned"}</small><small>{pod.members.length} member(s): {pod.members.join(", ")}</small><small>{pod.shift ? `${pod.shift.name} · ${pod.shift.start}–${pod.shift.end} · break ${pod.shift.break_start}–${pod.shift.break_end} · ${pod.shift.allocation_capacity_hours}h allocatable` : "No work window configured"}</small><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => { setPodEditId(pod.id); setPodName(pod.name); setPodSpecialization(pod.specialization); setPodLead(pod.lead_employee_code ?? ""); setPodMembers(pod.members); setPodShift(pod.shift ? { shift_name: pod.shift.name, shift_start: pod.shift.start, shift_end: pod.shift.end, break_start: pod.shift.break_start, break_end: pod.shift.break_end, allocation_capacity_hours: String(pod.shift.allocation_capacity_hours) } : { shift_name: "Work Window", shift_start: "09:00", shift_end: "18:00", break_start: "13:00", break_end: "14:00", allocation_capacity_hours: "6" }); document.getElementById("pod-edit-form")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>Edit pod</button><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} disabled={busy} onClick={() => void runAction(async () => { await request(`/api/v1/pods/${pod.id}`, { method: "DELETE" }); await refreshSummary(); setNotice("Empty pod removed."); })}>Remove empty pod</button></article>)}</div>
            <form id="pod-edit-form" className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void savePod(event)}><h3>{podEditId ? `Edit ${podName}` : "Add a pod"}</h3><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Pod name<input required value={podName} onChange={(event) => setPodName(event.target.value)} /></label><label>Specialization<input value={podSpecialization} onChange={(event) => setPodSpecialization(event.target.value)} /></label></div><label>Pod lead<select value={podLead} onChange={(event) => setPodLead(event.target.value)}><option value="">No lead</option>{activeEmployees.filter((employee) => podEditId === null || podMembers.includes(employee.code)).map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></label><fieldset className={cn("m-0 rounded-lg border border-[#d2d2d7] p-2.5 [&_legend]:text-[10px] [&_legend]:font-bold [&_legend]:text-[#6e6e73]")}><legend>Members of this pod</legend><p className={cn("text-xs leading-relaxed text-[#6e6e73] text-xs leading-relaxed text-[#6e6e73]")}>Select one or more people. A person can also belong to another pod; membership alone does not grant work eligibility.</p><div className={cn("mt-2 grid grid-cols-2 gap-1.5 max-[600px]:grid-cols-1 [&_label]:flex [&_label]:min-w-0 [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2 [&_label]:rounded-md [&_label]:border [&_label]:border-[#e5e5ea] [&_label]:bg-white [&_label]:p-2 [&_label]:text-[10px] [&_label]:text-[#424245] [&_label.selected]:border-[#7d8cc4] [&_label.selected]:bg-[#e9ecf6] [&_label.selected]:text-[#003459] [&_input]:accent-[#003459] [&_span]:truncate")}>{activeEmployees.map((employee) => <label key={employee.code} className={cn((podMembers.includes(employee.code) ? "border-[#7d8cc4] bg-[#e9ecf6] text-[#003459]" : ""))}><input type="checkbox" checked={podMembers.includes(employee.code)} onChange={() => setPodMembers((current) => current.includes(employee.code) ? current.filter((code) => code !== employee.code) : [...current, employee.code])} /><span>{employee.code} · {employee.name}{employee.code === podLead ? " · Lead" : ""}</span></label>)}</div></fieldset><p className={cn("text-xs leading-relaxed text-[#6e6e73] text-xs leading-relaxed text-[#6e6e73]")}>Work window is 8 clock hours with a lunch break; no more than six hours are allocated to brand work.</p><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Window name<input required value={podShift.shift_name} onChange={(event) => setPodShift({ ...podShift, shift_name: event.target.value })} /></label><label>Allocatable hours<input type="number" min="0.25" max="8" step="0.25" value={podShift.allocation_capacity_hours} onChange={(event) => setPodShift({ ...podShift, allocation_capacity_hours: event.target.value })} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Window starts<input type="time" value={podShift.shift_start} onChange={(event) => setPodShift({ ...podShift, shift_start: event.target.value })} /></label><label>Window ends<input type="time" value={podShift.shift_end} onChange={(event) => setPodShift({ ...podShift, shift_end: event.target.value })} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Lunch starts<input type="time" value={podShift.break_start} onChange={(event) => setPodShift({ ...podShift, break_start: event.target.value })} /></label><label>Lunch ends<input type="time" value={podShift.break_end} onChange={(event) => setPodShift({ ...podShift, break_end: event.target.value })} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy}>{podEditId ? "Save pod and members" : "Add pod and members"}</button>{podEditId !== null && <button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} type="button" disabled={busy} onClick={() => { setPodEditId(null); setPodName(""); setPodSpecialization(""); setPodLead(""); setPodMembers([]); }}>Cancel edit</button>}</div></form>
          </section>

          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Record employee availability</h2><p>Leave and unavailable dates</p></div></div></div>
            <form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void saveAvailability(event)}>
              <label>Employee<select value={employeeCode} onChange={(event) => setEmployeeCode(event.target.value)}>{activeEmployees.map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></label>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Type<select value={exceptionType} onChange={(event) => setExceptionType(event.target.value)}><option value="leave">Leave</option><option value="comp_off">Comp-off</option><option value="unavailable">Unavailable</option></select></label><label>Approval status<select value={approvalStatus} onChange={(event) => setApprovalStatus(event.target.value)}><option value="approved">Approved</option><option value="pending">Pending</option><option value="declined">Declined</option></select></label></div>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>From<input type="date" value={startDate} onChange={(event) => { setStartDate(event.target.value); if (!endDate) setEndDate(event.target.value); }} required /></label><label>Through<input type="date" value={endDate} min={startDate} onChange={(event) => setEndDate(event.target.value)} required /></label></div>
              <button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy || !employeeCode}>{busy ? "Saving…" : "Save and check roster impact"}</button>
            </form>
            {changeKind === "availability" && renderChangeImpact()}
          </section>

          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Recorded availability</h2><p>Requests and approvals</p></div></div></div>
            <div className={cn("max-h-[480px] overflow-auto [&_table]:w-full [&_table]:border-collapse [&_table]:whitespace-nowrap [&_table]:text-left [&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:border-y [&_th]:border-[#edf0f1] [&_th]:bg-[#f8f9f9] [&_th]:px-3.5 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-[10px] [&_th]:font-bold [&_th]:tracking-[0.03em] [&_th]:text-[#6e6e73] [&_td]:border-b [&_td]:border-[#f0f2f3] [&_td]:px-3.5 [&_td]:py-3 [&_td]:align-top [&_td]:text-[13px] [&_td]:text-[#424245] mx-[-23px] mb-[-4px] max-h-[560px] max-[760px]:mx-[-16px] [&_table]:min-w-[700px]")}><table><thead><tr><th>Employee</th><th>Type</th><th>Status</th><th>From</th><th>Through</th><th>Update status</th></tr></thead><tbody>{(summary?.employees ?? []).flatMap((employee) => employee.availability_exceptions.map((item) => <tr key={item.id}><td>{employee.code} · {employee.name}</td><td className={cn("capitalize")}>{item.type.replace(/_/g, " ")}</td><td><span className={cn(`inline-flex rounded-full bg-[#f1f3f9] px-2 py-1 text-[10px] font-semibold capitalize text-[#003459] ${item.approval_status === "approved" ? "bg-[#e6f2e9] text-[#276342]" : item.approval_status === "declined" ? "bg-[#fff0ec] text-[#b25242]" : "bg-[#fff5e6] text-[#a57020]"}`)}>{item.approval_status}</span></td><td>{item.start_date}</td><td>{item.end_date}</td><td><select value={item.approval_status} onChange={(event) => void changeAvailabilityStatus(item, employee, event.target.value)} disabled={busy}><option value="pending">Pending</option><option value="approved">Approved</option><option value="declined">Declined</option></select></td></tr>))}</tbody></table>{!(summary?.employees ?? []).some((employee) => employee.availability_exceptions.length) && <div className={cn("p-4 text-center text-xs text-[#6e6e73]")}>No availability exceptions have been recorded.</div>}</div>
          </section>
        </>}

        {page === "brands" && <>
          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Brand directory</h2><p>Clients, work, eligibility</p></div></div><span className={cn("ml-auto whitespace-nowrap text-[11px] text-[#6e6e73] max-[760px]:ml-0")}>{summary?.brands.length ?? 0} brands · {rules.length} processes</span><span className={cn("text-[10px] font-semibold tracking-[0.06em] text-[#6e6e73]")}>DEMO DATA</span></div>
            <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}>{(summary?.brands ?? []).map((brand) => <article className={cn("min-w-0 overflow-hidden rounded-[10px] border border-[#e7eaed] bg-white")} key={brand.code}>
              <div className={cn("flex items-center justify-between border-b border-[#eef0f1] p-3 max-[760px]:flex-wrap [&_h3]:mt-1 [&_h3]:text-[13px] [&_h3]:font-bold [&_h3]:text-[#29343c]")}><div><span className={cn("text-[9px] font-bold tracking-[0.07em] text-[#478067]")}>{brand.code}</span><h3>{brand.name}</h3><small>Tier {brand.client_tier} · deadline {brand.deadline_date ?? "not set"} · priority {brand.deadline_priority}/5 {brand.deadline_note ? `· ${brand.deadline_note}` : ""}</small></div><span className={cn("text-[10px] text-[#849087]")}>{brand.processes.length} process{brand.processes.length === 1 ? "" : "es"}</span><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => { setEditingBrandCode(brand.code); setEditingBrandActive(brand.active); setNewBrandCode(brand.code); setNewBrandName(brand.name); setBrandPriority(String(brand.deadline_priority)); setBrandDeadlineNote(brand.deadline_note); setBrandTier(String(brand.client_tier)); setBrandDeadlineDate(brand.deadline_date ?? ""); document.getElementById("brand-create-form")?.scrollIntoView({ behavior: "smooth" }); }}>Edit brand</button><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => void toggleBrand(brand)}>{brand.active ? "Archive" : "Reactivate"}</button>{brand.processes.length === 0 && <button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => void runAction(async () => { await request(`/api/v1/brands/${encodeURIComponent(brand.code)}`, { method: "DELETE" }); await refreshSummary(); setNotice("Brand removed."); })}>Remove</button>}</div>
              <div className={cn("px-3")}>{brand.processes.map((rule) => {
                const coverage = rule.coverage_by_weekday.filter((item) => rule.operating_weekdays.includes(item.weekday));
                const minimums = [...new Set(coverage.map((item) => item.minimum_staff))].sort((a, b) => a - b);
                const staffText = minimums.length ? minimums.map((count) => `${count} staff`).join(" / ") : "No daily coverage";
                const allocationRows = roster?.process_day_allocation?.filter((item) => item.brand_code === brand.code && item.process_code === rule.code) ?? [];
                const priorityBreakdown = allocationRows.find((item) => item.priority_score)?.priority_score;
                return <div className={cn("grid min-h-12 grid-cols-[minmax(80px,1.1fr)_minmax(95px,1.2fr)_minmax(55px,.5fr)_minmax(130px,1.4fr)_auto] items-center gap-2 border-b border-[#f0f2f3] max-[760px]:grid-cols-2 max-[760px]:gap-1 max-[760px]:py-2 [&>div_strong]:block [&>div_strong]:text-[10px] [&>div_strong]:font-bold [&>div_strong]:text-[#3d4b53] [&>div_small]:mt-1 [&>div_small]:block [&>div_small]:text-[9px] [&>div_small]:text-[#899198] [&>span]:text-[9px] [&>span]:leading-relaxed [&>span]:text-[#7d898c] [&.text-button]:whitespace-nowrap [&.text-button]:max-[760px]:justify-self-start [&.text-button]:max-[760px]:p-1")} key={rule.rule_id}>
                  <div><strong>{rule.code}</strong><small>{rule.name}</small></div>
                <span>{rule.operating_weekdays.map((day) => weekdays[day]).join(", ") || "No operating days"}</span>
                <span>{staffText}</span>
                <span>{rule.pod_name ?? "No work window"}</span>
                <span>{rule.eligible_employees.filter((employee) => employee.active).map((eligible) => `${eligible.employee_code} (${eligible.eligibility_type})`).join(", ") || "No eligible employees"}{roster?.process_day_allocation && <small className={cn("mt-1 block text-[10px] text-[#8b9499]")}>Roster served {allocationRows.reduce((sum, item) => sum + item.served_units, 0) / 2}h / required {allocationRows.reduce((sum, item) => sum + item.demand_units, 0) / 2}h</small>}{priorityBreakdown && <small className={cn("mt-1 block text-[10px] text-[#8b9499]")}>Score {priorityBreakdown.score}: tier {priorityBreakdown.client_tier} × urgency {priorityBreakdown.urgency} × backlog {priorityBreakdown.backlog_factor} × override {priorityBreakdown.manager_override}</small>}</span>
                  <div><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => { setRuleId(rule.rule_id); setSelectedDays(rule.operating_weekdays); setOperateOnHolidays(rule.operate_on_public_holidays); setCanShareSameDay(rule.can_share_same_day); setRulePod(rule.pod_name ?? ""); setRuleEffort(String(rule.effort_hours_per_employee)); setRuleDemandUnits(rule.demand_units == null ? "" : String(rule.demand_units)); setMinimumStaff(""); setCoverageWeekday(rule.operating_weekdays[0] ?? 0); document.getElementById("brand-rule-form")?.scrollIntoView({ behavior: "smooth" }); }}>Edit rules</button><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} disabled={busy} onClick={() => void runAction(async () => { await request(`/api/v1/brand-process-rules/${rule.rule_id}`, { method: "DELETE" }); await refreshSummary(); setNotice("Brand/process rule removed."); })}>Remove rule</button></div>
                </div>;
              })}</div>
            </article>)}</div>
          </section>

          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")} id="brand-rule-form">
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Brand and process rules</h2><p>Calendars and staffing levels</p></div></div></div>
            <form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void saveBrandRule(event)}>
              <label>Brand and process<select value={ruleId} onChange={(event) => { const nextId = Number(event.target.value); setRuleId(nextId); const match = rules.find((rule) => rule.rule_id === nextId); if (match) { setSelectedDays(match.operating_weekdays); setOperateOnHolidays(match.operate_on_public_holidays); setCanShareSameDay(match.can_share_same_day); setRulePod(match.pod_name ?? ""); setRuleEffort(String(match.effort_hours_per_employee)); setRuleDemandUnits(match.demand_units == null ? "" : String(match.demand_units)); setMinimumStaff(""); setCoverageWeekday(match.operating_weekdays[0] ?? 0); } }}><option value="">Choose a rule</option>{rules.map((rule) => <option key={rule.rule_id} value={rule.rule_id}>{rule.brand_code} · {rule.code} · {rule.name}</option>)}</select></label>
              {activeRule && <div className={cn("flex flex-wrap gap-2 [&_span]:rounded-md [&_span]:border [&_span]:border-[#e8ece9] [&_span]:bg-[#f8faf9] [&_span]:px-2 [&_span]:py-2 [&_span]:text-[10px] [&_span]:text-[#7c8882] [&_strong]:text-[#40584b]")}><span>Required skill: <strong>{activeRule.required_skill}</strong></span><span>Effort per person: <strong>{activeRule.effort_hours_per_employee}h</strong></span><span>Work window: <strong>{activeRule.pod_name ?? "Unassigned"}</strong></span><span>Deadline priority: <strong>{activeRule.deadline_priority}/5</strong></span><span>Currently eligible: <strong>{activeRule.eligible_employees.filter((employee) => employee.active).length}</strong></span></div>}
              <label>Pod work window<select value={rulePod} onChange={(event) => setRulePod(event.target.value)}><option value="">Choose a pod window</option>{summary?.pods.filter((pod) => pod.shift).map((pod) => <option key={pod.id} value={pod.name}>{pod.name} · {pod.shift?.name} ({pod.shift?.start}–{pod.shift?.end})</option>)}</select></label>
              <label>Effort hours per employee<input type="number" min="0.25" max="6" step="0.25" value={ruleEffort} onChange={(event) => setRuleEffort(event.target.value)} /></label>
              <label>Demand per operating day (half-hour units; blank = minimum staff × capacity)<input type="number" min="0" max="10000" value={ruleDemandUnits} onChange={(event) => setRuleDemandUnits(event.target.value)} placeholder="Use default demand" /></label>
              <p className={cn("text-xs leading-relaxed text-[#6e6e73] text-xs leading-relaxed text-[#6e6e73]")}>Operating days and staffing are separate settings. When opening a new weekday, select that weekday below and set its minimum staff so the solver has demand to allocate.</p>
              {activeRule && <><div className={cn("my-2.5 flex flex-col gap-1")}>{activeRule.eligible_employees.filter((item) => item.active).map((item) => <div className={cn("flex items-center justify-between gap-2 border-b border-[#edf0ee] py-2 [&>span]:flex [&>span]:flex-col [&>span]:gap-1 [&>span]:text-[10px] [&>span]:text-[#46534c] [&>span_small]:text-[9px] [&>span_small]:text-[#849087]")} key={item.employee_code}><span className={cn("rounded-md bg-[#f1f3f9] px-1.5 py-1 text-[10px] font-semibold text-[#003459]")}>{item.employee_code} · {summary?.employees.find((employee) => employee.code === item.employee_code)?.name} · {item.eligibility_type}</span><button type="button" className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} disabled={busy} onClick={() => void removeEligibility(activeRule, item.employee_code)}>Remove eligibility</button></div>)}</div><div className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")}><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Add or update eligible employee<select value={eligibilityEmployee} onChange={(event) => setEligibilityEmployee(event.target.value)}><option value="">Choose an employee</option>{activeEmployees.filter((employee) => employee.skills.includes(activeRule.required_skill)).map((employee) => <option key={employee.code} value={employee.code}>{employee.code} · {employee.name}</option>)}</select></label><label>Resource type<select value={eligibilityType} onChange={(event) => setEligibilityType(event.target.value)}><option value="primary">Primary owner</option><option value="backup">Backup</option></select></label></div><button type="button" className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy || !eligibilityEmployee} onClick={() => void saveEligibility()}>Save eligibility</button></div></>}
              <fieldset><legend>Operating weekdays</legend><div className={cn("flex flex-wrap gap-2 [&_label]:inline-flex [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2 [&_label]:rounded-full [&_label]:border [&_label]:border-[#d2d2d7] [&_label]:px-3 [&_label]:py-2 [&_label]:text-xs [&_label]:text-[#424245] [&_label.selected]:border-[#7d8cc4] [&_label.selected]:bg-[#e9ecf6] [&_label.selected]:text-[#003459] [&_input]:accent-[#003459]")}>{weekdays.map((day, index) => <label key={day} className={cn((selectedDays.includes(index) ? "border-[#7d8cc4] bg-[#e9ecf6] text-[#003459]" : ""))}><input type="checkbox" checked={selectedDays.includes(index)} onChange={() => setSelectedDays((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index].sort())} />{day}</label>)}</div></fieldset>
              <label>Public holiday operation<select value={operateOnHolidays ? "yes" : "no"} onChange={(event) => setOperateOnHolidays(event.target.value === "yes")}><option value="no">Closed</option><option value="yes">Open</option></select></label>
              <label className={cn("flex flex-row items-center gap-2 text-xs text-[#424245] [&_input]:accent-[#003459]")}><input type="checkbox" checked={canShareSameDay} onChange={(event) => setCanShareSameDay(event.target.checked)} /> Multiple employees may cover this process on one day</label>
              <div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Coverage weekday<select value={coverageWeekday} onChange={(event) => setCoverageWeekday(Number(event.target.value))}>{weekdays.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label><label>New minimum staff<input type="number" min="0" max={activeEmployees.length} placeholder="Leave unchanged" value={minimumStaff} onChange={(event) => setMinimumStaff(event.target.value)} /></label></div>
              <button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy || !ruleId || selectedDays.length === 0}>{busy ? "Saving…" : "Save rule and check roster impact"}</button>
            </form>
            {changeKind === "brand rule" && renderChangeImpact()}
          </section>

          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}><div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Brands and processes</h2><p>Configure future work</p></div></div><div><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} type="button" onClick={() => { setEditingBrandCode(""); setEditingBrandActive(true); setNewBrandCode(""); setNewBrandName(""); setBrandPriority("3"); setBrandDeadlineNote(""); setBrandTier("3"); setBrandDeadlineDate(""); document.getElementById("brand-create-form")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>New brand</button><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} type="button" onClick={() => { setEditingProcessCode(""); setNewProcessCode(""); setNewProcessName(""); setNewProcessSkill(""); setNewProcessHours("6"); }}>New process</button></div></div>
            <form id="brand-create-form" className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void saveNewBrand(event)}><h3>{editingBrandCode ? `Edit ${editingBrandCode}` : "Add a brand"}</h3><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Brand code<input required value={newBrandCode} onChange={(event) => setNewBrandCode(event.target.value)} /></label><label>Brand name<input required value={newBrandName} onChange={(event) => setNewBrandName(event.target.value)} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Client tier (1–5)<input type="number" min="1" max="5" value={brandTier} onChange={(event) => setBrandTier(event.target.value)} /></label><label>Deadline date<input type="date" value={brandDeadlineDate} onChange={(event) => setBrandDeadlineDate(event.target.value)} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Manager priority (1–5)<select value={brandPriority} onChange={(event) => setBrandPriority(event.target.value)}><option value="1">1 · Routine</option><option value="2">2 · Normal</option><option value="3">3 · Elevated</option><option value="4">4 · Urgent</option><option value="5">5 · Critical</option></select></label><label>Priority note<input maxLength={200} placeholder="Why this brand has this priority" value={brandDeadlineNote} onChange={(event) => setBrandDeadlineNote(event.target.value)} /></label></div><p className={cn("text-xs leading-relaxed text-[#6e6e73] text-xs leading-relaxed text-[#6e6e73]")}>Priority score combines client tier, deadline urgency, current backlog, and any dated manager override. The minimum coverage floor is allocated first.</p><button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy}>{editingBrandCode ? "Save brand" : "Add brand"}</button>{editingBrandCode && <button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} type="button" onClick={() => { setEditingBrandCode(""); setEditingBrandActive(true); setNewBrandCode(""); setNewBrandName(""); setBrandPriority("3"); setBrandDeadlineNote(""); setBrandTier("3"); setBrandDeadlineDate(""); }}>Cancel edit</button>}</form>
            <form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void saveAllocationSettings(event)}><h3>Hours allocation layer</h3><p className={cn("text-xs leading-relaxed text-[#6e6e73]")}>Half-hour allocation after minimum staffing is protected.</p><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Daily capacity (hours)<input type="number" min="0.5" max="8" step="0.5" value={allocationForm.capacity_units / 2} onChange={(event) => setAllocationForm({ ...allocationForm, capacity_units: Math.round(Number(event.target.value) * 2) })} /></label><label>Minimum task chunk (hours)<input type="number" min="0.5" max="8" step="0.5" value={allocationForm.min_chunk_units / 2} onChange={(event) => setAllocationForm({ ...allocationForm, min_chunk_units: Math.round(Number(event.target.value) * 2) })} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Max brands per person/day<input type="number" min="1" max="15" value={allocationForm.max_brands_per_day} onChange={(event) => setAllocationForm({ ...allocationForm, max_brands_per_day: Number(event.target.value) })} /></label><label>Solver time limit (seconds)<input type="number" min="1" max="600" value={allocationForm.solver_time_limit_seconds} onChange={(event) => setAllocationForm({ ...allocationForm, solver_time_limit_seconds: Number(event.target.value) })} /></label></div><label className={cn("flex flex-row items-center gap-2 text-xs text-[#424245] [&_input]:accent-[#003459]")}><input type="checkbox" checked={allocationForm.hours_layer_enabled} onChange={(event) => setAllocationForm({ ...allocationForm, hours_layer_enabled: event.target.checked })} /> Enable hours-based allocation for new rosters</label><button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy}>Save allocation settings</button></form>
            <form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void savePriorityOverride(event)}><h3>Dated priority override</h3><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Brand<select required value={overrideForm.brand_code} onChange={(event) => setOverrideForm({ ...overrideForm, brand_code: event.target.value })}><option value="">Choose brand</option>{summary?.brands.map((brand) => <option key={brand.code} value={brand.code}>{brand.code} · {brand.name}</option>)}</select></label><label>Multiplier<input type="number" min="0.1" max="10" step="0.1" value={overrideForm.multiplier} onChange={(event) => setOverrideForm({ ...overrideForm, multiplier: event.target.value })} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>From<input type="date" required value={overrideForm.start_date} onChange={(event) => setOverrideForm({ ...overrideForm, start_date: event.target.value })} /></label><label>Through<input type="date" required value={overrideForm.end_date} onChange={(event) => setOverrideForm({ ...overrideForm, end_date: event.target.value })} /></label></div><label>Reason<input maxLength={200} value={overrideForm.note} onChange={(event) => setOverrideForm({ ...overrideForm, note: event.target.value })} /></label><button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy}>{editingOverrideId ? "Save override" : "Add override"}</button>{(summary?.priority_overrides ?? []).map((item) => <div className={cn("flex items-center justify-between gap-2 border-b border-[#edf0ee] py-2 [&>span]:flex [&>span]:flex-col [&>span]:gap-1 [&>span]:text-[10px] [&>span]:text-[#46534c] [&>span_small]:text-[9px] [&>span_small]:text-[#849087]")} key={item.id}><span>{item.brand_code} · {item.start_date}–{item.end_date} · ×{item.multiplier}</span><div><button type="button" className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} disabled={busy} onClick={() => { setEditingOverrideId(item.id); setOverrideForm({ brand_code: item.brand_code, start_date: item.start_date, end_date: item.end_date, multiplier: String(item.multiplier), note: item.note }); }}>Edit</button><button type="button" className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} disabled={busy} onClick={() => void runAction(async () => { await request(`/api/v1/priority-overrides/${item.id}`, { method: "DELETE" }); await refreshSummary(); })}>Remove</button></div></div>)}</form>
            <form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void saveNewProcess(event)}><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Process code<input required value={newProcessCode} onChange={(event) => setNewProcessCode(event.target.value)} /></label><label>Process name<input required value={newProcessName} onChange={(event) => setNewProcessName(event.target.value)} /></label></div><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Required skill code<input required value={newProcessSkill} onChange={(event) => setNewProcessSkill(event.target.value)} /></label><label>Default allocatable hours per person<input type="number" min="0.25" max="6" step="0.25" value={newProcessHours} onChange={(event) => setNewProcessHours(event.target.value)} /></label></div><button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy}>{editingProcessCode ? "Save process" : "Add process"}</button>{editingProcessCode && <button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} type="button" onClick={() => { setEditingProcessCode(""); setNewProcessCode(""); setNewProcessName(""); setNewProcessSkill(""); setNewProcessHours("6"); }}>Cancel edit</button>}</form>
            <div className={cn("my-2.5 flex flex-col gap-1")}>{(summary?.process_catalog ?? []).map((process) => <div className={cn("flex items-center justify-between gap-2 border-b border-[#edf0ee] py-2 [&>span]:flex [&>span]:flex-col [&>span]:gap-1 [&>span]:text-[10px] [&>span]:text-[#46534c] [&>span_small]:text-[9px] [&>span_small]:text-[#849087]")} key={process.code}><span><strong>{process.code} · {process.name}</strong><small>Skill {process.required_skill} · {process.default_effort_hours}h per person</small></span><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => { setEditingProcessCode(process.code); setNewProcessCode(process.code); setNewProcessName(process.name); setNewProcessSkill(process.required_skill); setNewProcessHours(String(process.default_effort_hours)); }}>Edit</button>{!rules.some((rule) => rule.code === process.code) && <button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} disabled={busy} onClick={() => void runAction(async () => { await request(`/api/v1/processes/${encodeURIComponent(process.code)}`, { method: "DELETE" }); await refreshSummary(); setNotice("Unused process removed."); })}>Remove</button>}</div>)}</div>
            <form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void saveNewRule(event)}><label>Connect process to brand<div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><select required value={newRuleBrand} onChange={(event) => setNewRuleBrand(event.target.value)}><option value="">Brand</option>{summary?.brands.map((brand) => <option key={brand.code} value={brand.code}>{brand.code} · {brand.name}</option>)}</select><select required value={newRuleProcess} onChange={(event) => setNewRuleProcess(event.target.value)}><option value="">Process</option>{summary?.process_catalog.filter((process) => process.enabled).map((process) => <option key={process.code} value={process.code}>{process.code} · {process.name}</option>)}</select></div></label><label>Pod work window<select required value={newRulePod} onChange={(event) => setNewRulePod(event.target.value)}><option value="">Choose a pod window</option>{summary?.pods.filter((pod) => pod.shift).map((pod) => <option key={pod.id} value={pod.name}>{pod.name} · {pod.shift?.name} ({pod.shift?.start}–{pod.shift?.end})</option>)}</select></label><fieldset><legend>Operating weekdays</legend><div className={cn("flex flex-wrap gap-2 [&_label]:inline-flex [&_label]:cursor-pointer [&_label]:items-center [&_label]:gap-2 [&_label]:rounded-full [&_label]:border [&_label]:border-[#d2d2d7] [&_label]:px-3 [&_label]:py-2 [&_label]:text-xs [&_label]:text-[#424245] [&_label.selected]:border-[#7d8cc4] [&_label.selected]:bg-[#e9ecf6] [&_label.selected]:text-[#003459] [&_input]:accent-[#003459]")}>{weekdays.map((day, index) => <label key={day} className={cn((newRuleDays.includes(index) ? "border-[#7d8cc4] bg-[#e9ecf6] text-[#003459]" : ""))}><input type="checkbox" checked={newRuleDays.includes(index)} onChange={() => setNewRuleDays((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index].sort())} />{day}</label>)}</div></fieldset><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Minimum staff on operating days<input type="number" min="1" max={activeEmployees.length} value={newRuleMinimum} onChange={(event) => setNewRuleMinimum(event.target.value)} /></label><label>Staff effort per day<input type="number" min="0.25" max="6" step="0.25" value={newProcessHours} onChange={(event) => setNewProcessHours(event.target.value)} /></label></div><button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50")} disabled={busy || !newRuleBrand || !newRuleProcess || !newRulePod || !newRuleDays.length}>Add brand/process requirement</button></form>
          </section>

          <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] p-[21px_23px] max-[760px]:p-4")}>
            <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Public holidays</h2><p>Dates and operating rules</p></div></div></div>
            <div className={cn("grid grid-cols-3 gap-2.5 max-[760px]:grid-cols-1")}>{(summary?.public_holidays ?? []).map((holiday) => <div className={cn("flex items-center gap-2 rounded-lg border border-[#edf0f1] p-3 text-[10px] text-[#768188] [&_strong]:text-[#43514f]")} key={holiday.id}><strong>{holiday.name}</strong><span>{holiday.date}</span><button className={cn("inline-flex items-center gap-1.5 border-0 bg-transparent px-1.5 py-1.5 text-[11px] font-semibold text-[#003459] hover:underline disabled:cursor-wait disabled:opacity-45")} onClick={() => void runAction(async () => { await request(`/api/v1/public-holidays/${holiday.id}`, { method: "DELETE" }); await refreshSummary(); setNotice("Public holiday removed."); })}>Remove</button></div>)}</div><form className={cn("flex flex-col gap-3 rounded-xl border border-[#e5e5ea] bg-white p-4 [&_label]:flex [&_label]:flex-col [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-[#424245] [&_input]:h-10 [&_input]:rounded-lg [&_input]:border [&_input]:border-[#d2d2d7] [&_input]:bg-white [&_input]:px-3 [&_input]:text-[13px] [&_input]:text-[#1d1d1f] [&_input]:outline-none [&_input]:focus:border-[#7d8cc4] [&_input]:focus:ring-2 [&_input]:focus:ring-[#7d8cc4]/20 [&_select]:h-10 [&_select]:rounded-lg [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-3 [&_select]:text-[13px] [&_select]:text-[#1d1d1f] [&_select]:outline-none [&_select]:focus:border-[#7d8cc4] [&_select]:focus:ring-2 [&_select]:focus:ring-[#7d8cc4]/20 max-w-[760px] max-[760px]:max-w-none")} onSubmit={(event) => void addHoliday(event)}><div className={cn("grid grid-cols-2 gap-3 max-[760px]:grid-cols-1")}><label>Holiday name<input required value={holidayName} onChange={(event) => setHolidayName(event.target.value)} /></label><label>Date<input type="date" required value={holidayDate} onChange={(event) => setHolidayDate(event.target.value)} /></label></div><button className={cn("inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-[#d2d2d7] bg-[#f5f5f7] px-4 text-[13px] font-semibold text-[#1d1d1f] hover:bg-[#e9ecf6] disabled:cursor-wait disabled:opacity-50 inline-flex items-center gap-1.5 [&_svg]:size-4")} disabled={busy}><IconPlus aria-hidden="true" /> Add public holiday</button></form>
          </section>
        </>}
      </div>

    </section>
    </main>
    </div>
  </div>;
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
  return <main className={cn("mx-auto w-full max-w-[1600px] px-[34px] pb-14 pt-[30px] max-[760px]:px-[18px] max-[760px]:pb-9 max-[760px]:pt-[22px]")}>
    <header className={cn("flex h-[72px] items-center justify-between border-b border-[#e5e5ea]")}>
      <div className={cn("flex items-center gap-2.5 px-2 pb-7 max-[760px]:px-0 max-[760px]:pb-2")}><span className={cn("grid size-[34px] shrink-0 place-items-center rounded-[10px] bg-[#003459] text-[12px] font-extrabold tracking-[-1px] text-white shadow-sm")}>DS</span><div><strong>DataStraw</strong><small>Smart Roster</small></div></div>
      <div className={cn("flex items-center gap-2.5 max-[760px]:flex-col max-[760px]:items-end [&_label]:flex [&_label]:items-center [&_label]:gap-2 [&_label]:text-[10px] [&_label]:font-semibold [&_label]:text-[#424245] [&_select]:h-8 [&_select]:max-w-[220px] [&_select]:rounded-md [&_select]:border [&_select]:border-[#d2d2d7] [&_select]:bg-white [&_select]:px-2.5 [&_select]:text-[11px] [&_select]:text-[#1d1d1f]")}>
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
    <section className={cn("flex justify-between items-end [&_h1]:m-0 [&_h1]:text-[32px] [&_h1]:font-semibold [&_h1]:leading-[1.12] [&_h1]:tracking-[-0.8px] [&_h1]:text-[#1d1d1f] [&_h1]:max-[760px]:text-[29px] [&_p]:mt-[7px] [&_p]:text-[13px] [&_p]:leading-[1.35] [&_p]:text-[#6e6e73] mb-0 min-h-[93px] items-center pb-[25px] max-[760px]:min-h-0 max-[760px]:flex-col max-[760px]:items-start max-[760px]:gap-3 max-[760px]:pb-5")}>
      <div><h1>{role === "pod_lead" ? "Pod schedule" : "My schedule"}</h1>
        <p>{roleLabel}{actor ? ` · ${actor.code} ${actor.name}` : ""}</p>
      </div>
    </section>
    <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] mb-3.5 rounded-[14px] border border-[#e5e5ea] bg-white p-5 max-[760px]:p-4")}>
      <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Roster</h2>
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
        <div className={cn("mb-3 flex items-center gap-3.5 rounded-lg border border-[#e5e5ea] bg-[#f5f5f7] p-3 text-xs text-[#424245] max-[760px]:flex-col max-[760px]:items-start max-[760px]:gap-1.5")}>
          <span>{new Date(roster.year, roster.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" })}</span>

          {role === "pod_lead" && roster.generation && <span>{roster.generation.filled_slots}/{roster.generation.required_slots} pod assignments covered</span>}
        </div>
        <div className={cn("max-h-[480px] overflow-auto [&_table]:w-full [&_table]:border-collapse [&_table]:whitespace-nowrap [&_table]:text-left [&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:border-y [&_th]:border-[#edf0f1] [&_th]:bg-[#f8f9f9] [&_th]:px-3.5 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-[10px] [&_th]:font-bold [&_th]:tracking-[0.03em] [&_th]:text-[#6e6e73] [&_td]:border-b [&_td]:border-[#f0f2f3] [&_td]:px-3.5 [&_td]:py-3 [&_td]:align-top [&_td]:text-[13px] [&_td]:text-[#424245] mx-[-23px] mb-[-4px] max-h-[560px] max-[760px]:mx-[-16px] [&_table]:min-w-[700px] mx-[-23px] max-[760px]:mx-[-16px] [&_table]:min-w-[760px]")}><table><thead><tr>
        <th>Date</th><th>Brand</th><th>Process</th><th>Pod</th><th>Employee</th><th>Status</th><th>Reason</th>
        </tr></thead><tbody>{assignments.map((item) => <tr key={item.assignment_id}>
          <td>{item.date}</td><td>{item.brand_code}</td>
          <td>{item.process_code}</td><td>{item.pod_name ?? "—"}</td><td>{item.employee_code ?? "Uncovered"}</td>
          <td><span className={cn(`inline-flex rounded-full bg-[#f1f3f9] px-2 py-1 text-[10px] font-semibold capitalize text-[#003459] ${item.status === "working" ? "bg-[#e6f2e9] text-[#276342]" : item.status === "uncovered" ? "bg-[#fff0ec] text-[#b25242]" : item.status === "leave" || item.status === "unavailable" ? "bg-[#f0eafa] text-[#634b8a]" : item.status === "active" || item.status === "approved" ? "bg-[#e6f2e9] text-[#276342]" : item.status === "declined" ? "bg-[#fff0ec] text-[#b25242]" : item.status === "pending" ? "bg-[#fff5e6] text-[#a57020]" : "bg-[#f1f3f9] text-[#6e6e73]"}`)}>{item.status}</span></td><td className={cn("min-w-[220px] max-w-[420px] whitespace-normal leading-snug")}>{item.explanation}</td>
        </tr>)}</tbody></table>{assignments.length === 0 && <div className={cn("p-4 text-center text-xs text-[#6e6e73]")}>No assignments are available for this view.</div>}</div>
        {role === "employee" && <>
          <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start mt-6 border-t border-[#e5e5ea] pt-4")}><div><div><h2>My work days</h2><p>Scheduled work, weekly offs, and availability status.</p></div></div></div>
          <div className={cn("max-h-[480px] overflow-auto [&_table]:w-full [&_table]:border-collapse [&_table]:whitespace-nowrap [&_table]:text-left [&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:border-y [&_th]:border-[#edf0f1] [&_th]:bg-[#f8f9f9] [&_th]:px-3.5 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-[10px] [&_th]:font-bold [&_th]:tracking-[0.03em] [&_th]:text-[#6e6e73] [&_td]:border-b [&_td]:border-[#f0f2f3] [&_td]:px-3.5 [&_td]:py-3 [&_td]:align-top [&_td]:text-[13px] [&_td]:text-[#424245] mx-[-23px] mb-[-4px] max-h-[560px] max-[760px]:mx-[-16px] [&_table]:min-w-[700px] mx-[-23px] max-[760px]:mx-[-16px] [&_table]:min-w-[760px]")}><table><thead><tr><th>Date</th><th>Status</th><th>Allocated hours</th><th>Details</th></tr></thead>
            <tbody>{employeeDays.map((item) => <tr key={`${item.date}-${item.employee_code}`}><td>{item.date}</td>
              <td><span className={cn(`inline-flex rounded-full bg-[#f1f3f9] px-2 py-1 text-[10px] font-semibold capitalize text-[#003459] ${item.status === "working" ? "bg-[#e6f2e9] text-[#276342]" : item.status === "uncovered" ? "bg-[#fff0ec] text-[#b25242]" : item.status === "leave" || item.status === "unavailable" ? "bg-[#f0eafa] text-[#634b8a]" : item.status === "active" || item.status === "approved" ? "bg-[#e6f2e9] text-[#276342]" : item.status === "declined" ? "bg-[#fff0ec] text-[#b25242]" : item.status === "pending" ? "bg-[#fff5e6] text-[#a57020]" : "bg-[#f1f3f9] text-[#6e6e73]"}`)}>{item.status.replace(/_/g, " ")}</span></td>
              <td>{item.assigned_effort_hours}h</td><td>{item.explanation}</td></tr>)}</tbody>
          </table>{employeeDays.length === 0 && <div className={cn("p-4 text-center text-xs text-[#6e6e73]")}>No work-day details are available.</div>}</div>
          <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start mt-6 border-t border-[#e5e5ea] pt-4")}><div><div><h2>My availability</h2><p>Leave, comp-off, and unavailable dates recorded for you.</p></div></div></div>
          <div className={cn("max-h-[480px] overflow-auto [&_table]:w-full [&_table]:border-collapse [&_table]:whitespace-nowrap [&_table]:text-left [&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:border-y [&_th]:border-[#edf0f1] [&_th]:bg-[#f8f9f9] [&_th]:px-3.5 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-[10px] [&_th]:font-bold [&_th]:tracking-[0.03em] [&_th]:text-[#6e6e73] [&_td]:border-b [&_td]:border-[#f0f2f3] [&_td]:px-3.5 [&_td]:py-3 [&_td]:align-top [&_td]:text-[13px] [&_td]:text-[#424245] mx-[-23px] mb-[-4px] max-h-[560px] max-[760px]:mx-[-16px] [&_table]:min-w-[700px] mx-[-23px] max-[760px]:mx-[-16px] [&_table]:min-w-[760px]")}><table><thead><tr><th>Type</th><th>Approval</th><th>From</th><th>Through</th><th>Note</th></tr></thead>
            <tbody>{(actor?.availability_exceptions ?? []).map((item) => <tr key={item.id}><td>{item.type.replace(/_/g, " ")}</td>
              <td>{item.approval_status}</td><td>{item.start_date}</td><td>{item.end_date}</td><td>{item.note || "—"}</td></tr>)}</tbody>
          </table>{!(actor?.availability_exceptions ?? []).length && <div className={cn("p-4 text-center text-xs text-[#6e6e73]")}>No availability records have been entered.</div>}</div>
        </>}
      </> : <div className={cn("p-9 text-center text-xs text-[#6e6e73]")}>{savedRosters.length ? "Choose a saved roster to continue." : "No roster has been generated yet."}</div>}
    </section>
    {validation && <section className={cn("rounded-[14px] border border-[#e5e5ea] bg-white shadow-[0_4px_16px_rgb(30_55_43_/_4%)] mb-3.5 rounded-[14px] border border-[#e5e5ea] bg-white p-5 max-[760px]:p-4")}>
      <div className={cn("mb-5 flex items-center justify-between gap-3 [&>div]:flex [&>div]:items-center [&>div]:gap-3 max-[760px]:flex-wrap max-[760px]:items-start")}><div><div><h2>Validation</h2></div></div>

      </div>
      {validation.issues.length ? <div className={cn("max-h-[240px] overflow-auto overscroll-contain")}>{validation.issues.map((issue, index) => <div className={cn(`flex items-start gap-3 border-b border-[#f0f2f3] px-3.5 py-2.5 last:border-b-0`)} key={`${issue.code}-${index}`}>
        <div><strong>{issue.date ?? "Roster"} · {issue.brand_code ?? "Schedule"}{issue.process_code ? ` · ${issue.process_code}` : ""}</strong><small>{issue.message}</small></div>
      </div>)}</div> : <div className={cn("p-4 text-[13px] text-[#328360]")}>No related validation issues.</div>}
    </section>}

  </main>;
}

export default App;
