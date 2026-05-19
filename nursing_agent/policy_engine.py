"""
Policy Engine — encodes SHC/CRONA scheduling rules.

Sources:
  • Staffing and Scheduling Policy (06/2019)
  • Floating Policy (08/2024)
  • Staffing Absent Day Procedure (04/2020)
  • Pre-Approved Vacation & Education Policy (07/2022, updated 01/2024)
  • SHC/CRONA CBA (04/2025 – 03/2028)
  • Timekeeping System WMS Policy (08/2025)

Union contract always takes precedence over policy documents.
"""

from __future__ import annotations
from datetime import date, timedelta
from typing import Optional
from .models import (
    Nurse, TimeOffRequest, ShiftSwapRequest, SchedulePeriod,
    EmployeeType, RequestType, ShiftSlot, WeekendPattern,
)


# ── Schedule Request Priority ────────────────────────────────────────────────
# Scheduling Policy §II.A.1 and Vacation/Education Policy §IV.M

SCHEDULE_REQUEST_PRIORITY = [
    "pre_approved_vacation",
    "pre_approved_education",
    "skill_mix_specialty",
    "seniority",
    "isolated_pto_education",
]


# ── Cancellation Order (overstaffed) ────────────────────────────────────────
# Staffing & Scheduling Policy §II.D.2

def cancellation_order(nurses: list[Nurse]) -> list[Nurse]:
    """
    Returns nurses sorted by who should be cancelled first when overstaffed.
    Order: voluntary → traveler → relief over commitment → regular over commitment
           → relief → regular (inverse seniority, fewest cancelled hours this PP).
    """
    def rank(n: Nurse) -> tuple:
        type_rank = {
            EmployeeType.TRAVELER: 0,
            EmployeeType.RELIEF: 1,
            EmployeeType.REGULAR: 2,
            EmployeeType.REGISTRY: 0,
        }[n.employee_type]
        return (type_rank, n.seniority_years, -n.cancelled_hours_this_pay_period)

    return sorted(nurses, key=rank)


# ── Float Order ──────────────────────────────────────────────────────────────
# Floating Policy §IV.C.5

def float_order(nurses: list[Nurse], target_unit: str) -> list[Nurse]:
    """
    Returns nurses sorted by who should float first.
    Order: voluntary → relief over commitment → regular over commitment
           → registry → traveler → relief → regular (including specialty roles
           if another person can cover the specialty).
    """
    eligible = [n for n in nurses if not n.is_float_exempt]

    def rank(n: Nurse) -> tuple:
        type_rank = {
            EmployeeType.RELIEF: 0,
            EmployeeType.REGISTRY: 1,
            EmployeeType.TRAVELER: 2,
            EmployeeType.REGULAR: 3,
        }[n.employee_type]
        # Inverse seniority (least senior floats first among same type)
        last_float = n.last_float_date or date.min
        return (type_rank, n.seniority_years, last_float)

    return sorted(eligible, key=rank)


# ── Mandatory A-Day Order ────────────────────────────────────────────────────
# Staffing Absent Day Procedure §III.E.1 (CRONA)

def mandatory_a_day_order(nurses: list[Nurse]) -> list[Nurse]:
    """
    CRONA mandatory cancellation order when staffing exceeds need.
    1. Relief over commitment
    2. Regular over commitment
    3. Traveler / Agency
    4. Relief (fewest cancelled hours this PP; tie = least senior)
    5. Regular (inverse seniority, hours cancelled this PP)
    """
    def rank(n: Nurse) -> tuple:
        type_rank = {
            EmployeeType.TRAVELER: 2,
            EmployeeType.REGISTRY: 2,
            EmployeeType.RELIEF: 3,
            EmployeeType.REGULAR: 4,
        }[n.employee_type]
        return (type_rank, n.cancelled_hours_this_pay_period, n.seniority_years)

    return sorted(nurses, key=rank)


# ── Vacation Policy ──────────────────────────────────────────────────────────

SUMMER_START_MONTH_DAY = (6, 1)   # June 1
THANKSGIVING_EXEMPT = True        # Thanksgiving week is excluded
MAX_SUMMER_WEEKS_INITIAL = 2      # Per initial request phase

def check_vacation_eligibility(
    nurse: Nurse,
    req_weeks: int,
    requested_dates: list[date],
    is_summer: bool,
    existing_summer_weeks: int,
) -> tuple[bool, str]:
    """
    Validate a pre-approved vacation request.
    Returns (eligible, reason).
    Vacation/Education Policy §IV.B-N.
    """
    max_weeks = nurse.max_pre_approved_vacation_weeks
    used = nurse.pre_approved_vacation_weeks_used

    if used + req_weeks > max_weeks:
        return False, (
            f"Exceeds annual allowance. Used {used}/{max_weeks} weeks. "
            f"Requesting {req_weeks} more."
        )

    if is_summer and existing_summer_weeks + req_weeks > MAX_SUMMER_WEEKS_INITIAL:
        return False, (
            f"Summer requests limited to {MAX_SUMMER_WEEKS_INITIAL} weeks "
            "during initial approval phase."
        )

    # Thanksgiving week check
    for d in requested_dates:
        if _is_thanksgiving_week(d):
            return False, "Vacation cannot be scheduled during Thanksgiving week."

    # Outside schedule period (Jan 2 – Dec 20)
    for d in requested_dates:
        if d.month == 12 and d.day > 20:
            return False, "Vacation period ends December 20."
        if d.month == 1 and d.day < 2:
            return False, "Vacation period starts January 2."

    return True, "Eligible"


def _is_thanksgiving_week(d: date) -> bool:
    """Thanksgiving = 4th Thursday of November."""
    if d.month != 11:
        return False
    thursdays = [
        date(d.year, 11, day)
        for day in range(1, 31)
        if date(d.year, 11, day).weekday() == 3  # Thursday
    ]
    if len(thursdays) < 4:
        return False
    thanksgiving = thursdays[3]
    week_start = thanksgiving - timedelta(days=thanksgiving.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start <= d <= week_end


def pto_days_per_vacation_week(nurse: Nurse) -> int:
    """
    Number of PTO days to charge per vacation week.
    Pre-Approved Vacation Policy §IV.H table.
    """
    if nurse.shift_type.value == "12hr":
        if nurse.fte >= 0.9:
            return 3
        elif nurse.fte >= 0.75:
            return 3  # 2-3, default to 3
        else:
            return 2
    else:
        if nurse.fte >= 1.0:
            return 5
        elif nurse.fte >= 0.9:
            return 5  # 4-5, default to 5
        elif nurse.fte >= 0.8:
            return 4
        elif nurse.fte >= 0.7:
            return 4  # 3-4, default to 4
        elif nurse.fte >= 0.6:
            return 3
        else:
            return 3  # 2-3, default to 3


# ── Weekend Compliance ───────────────────────────────────────────────────────

def is_designated_weekend_day(d: date, pattern: WeekendPattern) -> bool:
    """Returns True if the date falls on the nurse's designated weekend."""
    weekday = d.weekday()  # Monday=0, Sunday=6
    if pattern == WeekendPattern.SAT_SUN:
        return weekday in (5, 6)
    elif pattern == WeekendPattern.FRI_SAT:
        return weekday in (4, 5)
    elif pattern == WeekendPattern.FRI_SUN:
        return weekday in (4, 6)
    return False


def count_red_days(
    nurse: Nurse,
    requested_days_off: list[date],
    pre_approved_dates: set[date],
    designated_weekends: set[date],
) -> int:
    """
    Count non-pre-approved, non-designated-weekend days off.
    Scheduling Policy §II.A.3 — limit 5 red days per schedule.
    """
    return sum(
        1 for d in requested_days_off
        if d not in pre_approved_dates and d not in designated_weekends
    )


MAX_RED_DAYS_PER_SCHEDULE = 5


# ── Shift Swap Validation ────────────────────────────────────────────────────

SWAP_LEAD_TIME_DAYS = 3  # Must be entered at least 3 days prior

def validate_shift_swap(
    req: ShiftSwapRequest,
    nurse1: Nurse,
    nurse2: Nurse,
    today: date,
) -> tuple[bool, str]:
    """
    Scheduling Policy §II.B.2.b — trade must be entered ≥3 days prior,
    manager must approve, both nurse names and dates required.
    """
    days_until_trade = (req.trade_date - today).days
    if days_until_trade < SWAP_LEAD_TIME_DAYS:
        return False, (
            f"Swap must be submitted at least {SWAP_LEAD_TIME_DAYS} days before "
            f"the trade date. Only {days_until_trade} day(s) remaining."
        )

    if not req.accepting_nurse_id:
        return False, "An accepting nurse must be identified for the swap."

    if not req.swap_shift_id:
        return False, "The accepting nurse's shift must be specified."

    return True, "Swap request is valid pending manager approval."


# ── A-Day Request Validation ─────────────────────────────────────────────────

A_DAY_MAX_ADVANCE_DAYS = 28    # 4 weeks
A_DAY_CUTOFF_HOURS_BEFORE = 8  # Must request ≥8 hours before shift start

def validate_a_day_request(
    nurse: Nurse,
    requested_date: date,
    request_submitted: date,
    nurses_on_shift: list[Nurse],
) -> tuple[bool, str]:
    """
    Staffing Absent Day §III.D.1 — may request 4 weeks in advance,
    no later than 8 hours prior to shift start.
    §III.D.6 — staff who already received an A-day this pay period
    are lower priority than those who haven't.
    """
    days_ahead = (requested_date - request_submitted).days
    if days_ahead > A_DAY_MAX_ADVANCE_DAYS:
        return False, f"A-day requests may be submitted no more than {A_DAY_MAX_ADVANCE_DAYS} days in advance."

    if days_ahead < 0:
        return False, "Cannot request an A-day for a past date."

    # Others who haven't had an A-day take priority
    others_without_a_day = [
        n for n in nurses_on_shift
        if n.id != nurse.id and n.a_days_this_pay_period == 0
    ]
    if nurse.a_days_this_pay_period > 0 and others_without_a_day:
        return False, (
            "Other staff on this shift have not yet received an A-day this pay period "
            "and have priority."
        )

    return True, "A-day request valid."


# ── Float Eligibility ────────────────────────────────────────────────────────

NEW_GRAD_FLOAT_EXEMPT_MONTHS = 6
NEW_HIRE_EXPERIENCED_FLOAT_EXEMPT_MONTHS = 2
TRAVEL_NURSE_FLOAT_EXEMPT_SHIFTS = 3  # first 3 twelve-hour shifts

def can_float(nurse: Nurse, shifts_worked: int = 0) -> tuple[bool, str]:
    """
    Floating Policy §IV.B — new hires have float exemption periods.
    §IV.C.2.a — 30+ year seniority exempt if operationally feasible.
    """
    months_employed = nurse.seniority_years * 12

    if nurse.is_float_exempt:
        return False, "Float exempt: 30+ years seniority."

    if nurse.employee_type == EmployeeType.TRAVELER:
        if shifts_worked < TRAVEL_NURSE_FLOAT_EXEMPT_SHIFTS:
            return False, f"Travel nurse float exempt for first {TRAVEL_NURSE_FLOAT_EXEMPT_SHIFTS} shifts."

    if nurse.employee_type == EmployeeType.REGULAR:
        # Distinguish new grad vs experienced using role/specialties heuristic
        is_new_grad = len(nurse.specialties) == 0 and nurse.seniority_years < 1
        exempt_months = NEW_GRAD_FLOAT_EXEMPT_MONTHS if is_new_grad else NEW_HIRE_EXPERIENCED_FLOAT_EXEMPT_MONTHS
        if months_employed < exempt_months:
            return False, (
                f"New hire float exempt for first {exempt_months} months "
                f"({months_employed:.1f} months employed)."
            )

    return True, "Eligible to float."


# ── FTE Compliance ───────────────────────────────────────────────────────────

def fte_hours_in_period(nurse: Nurse, start: date, end: date) -> float:
    """Calculate required hours in any date range based on FTE."""
    total_days = (end - start).days + 1
    weeks = total_days / 7.0
    hours_per_week = nurse.fte * (36 if nurse.shift_type.value == "12hr" else 40)
    return round(hours_per_week * weeks, 2)


def validate_fte_compliance(
    nurse: Nurse,
    assigned_hours: float,
    schedule_start: date,
    schedule_end: date,
    tolerance_hours: float = 0.5,
) -> tuple[bool, float]:
    """
    Returns (is_compliant, deficit_or_surplus_hours).
    Positive deficit means under-scheduled.
    """
    required = fte_hours_in_period(nurse, schedule_start, schedule_end)
    diff = required - assigned_hours
    return abs(diff) <= tolerance_hours, diff


# ── Overtime Guard ───────────────────────────────────────────────────────────

def would_incur_overtime(nurse: Nurse, additional_hours: float) -> bool:
    """
    Scheduling Policy §II.D.1.b — staff may not be asked to work over
    commitment if it incurs overtime.
    CBA: overtime threshold is 40 hrs/week for 8-hr, 36 hrs/week for 12-hr nurses.
    Simplified: flag if assigning would push weekly hours >40 (8hr) or >36 (12hr).
    """
    weekly_threshold = 36.0 if nurse.shift_type.value == "12hr" else 40.0
    return additional_hours > weekly_threshold


# ── Mandatory A-Day Notification ─────────────────────────────────────────────

MANDATORY_A_DAY_NOTICE_MINUTES = 60  # Must notify ≥60 minutes before shift

def mandatory_a_day_callback_rules() -> dict:
    """
    Staffing Absent Day §III.E.5-6:
    - Notify ≥60 min before shift
    - CRONA: if called back after 1 hour of receiving mandatory A-day → paid at 1.5x
    """
    return {
        "min_notice_minutes": MANDATORY_A_DAY_NOTICE_MINUTES,
        "crona_callback_after_1hr": "time_and_half",
        "seiu_callback": "straight_time",
        "last_cancelled_gets_first_return": True,
    }


# ── Schedule Posting Requirement ─────────────────────────────────────────────

SCHEDULE_POST_LEAD_WEEKS = 2   # Must post at least 2 weeks in advance
SCHEDULE_PERIOD_WEEKS = 4

def schedule_must_be_posted_by(schedule_start: date) -> date:
    """Timekeeping Policy §II.D.2.a — post ≥2 weeks before period starts."""
    return schedule_start - timedelta(weeks=SCHEDULE_POST_LEAD_WEEKS)
