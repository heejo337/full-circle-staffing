"""
Request Handler — manages PTO, A-day, shift swap, and float requests.

Applies all SHC/CRONA policy rules automatically and produces
structured decisions with explanations.
"""

from __future__ import annotations
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from .models import (
    Nurse, TimeOffRequest, ShiftSwapRequest, RequestType, RequestStatus,
    ShiftSlot, SchedulePeriod,
)
from .policy_engine import (
    check_vacation_eligibility, validate_shift_swap, validate_a_day_request,
    SCHEDULE_REQUEST_PRIORITY, MAX_RED_DAYS_PER_SCHEDULE,
    count_red_days, is_designated_weekend_day,
    pto_days_per_vacation_week, mandatory_a_day_order,
    mandatory_a_day_callback_rules, MANDATORY_A_DAY_NOTICE_MINUTES,
)


class RequestDecision:
    def __init__(
        self,
        approved: bool,
        reason: str,
        request_id: str,
        policy_references: list[str] | None = None,
    ) -> None:
        self.approved = approved
        self.reason = reason
        self.request_id = request_id
        self.policy_references = policy_references or []
        self.decided_at = datetime.now()

    def __repr__(self) -> str:
        status = "APPROVED" if self.approved else "DENIED"
        return f"[{status}] {self.reason}"


class RequestHandler:
    """
    Processes all nurse schedule-change requests against policy rules.
    Returns structured RequestDecision objects ready for confirmation.
    """

    def __init__(self, nurses: dict[str, Nurse]) -> None:
        self.nurses = nurses

    # ── PTO / Pre-Approved Vacation ───────────────────────────────────────────

    def review_pto_request(
        self,
        req: TimeOffRequest,
        existing_approved: list[TimeOffRequest],
        schedule: Optional[SchedulePeriod],
        today: date = None,
    ) -> RequestDecision:
        today = today or date.today()
        nurse = self.nurses.get(req.nurse_id)
        if not nurse:
            return RequestDecision(False, "Nurse not found in roster.", req.id)

        # Pre-approved vacation
        if req.request_type == RequestType.PRE_APPROVED_VACATION:
            return self._review_pre_approved_vacation(req, nurse, existing_approved)

        # Pre-approved education
        if req.request_type == RequestType.PRE_APPROVED_EDUCATION:
            return self._review_pre_approved_education(req, nurse)

        # Regular PTO / isolated days off
        if req.request_type == RequestType.PTO:
            return self._review_regular_pto(req, nurse, existing_approved, schedule)

        return RequestDecision(False, f"Unsupported request type: {req.request_type}", req.id)

    def _review_pre_approved_vacation(
        self,
        req: TimeOffRequest,
        nurse: Nurse,
        existing_approved: list[TimeOffRequest],
    ) -> RequestDecision:
        # Calculate number of weeks being requested
        days_count = len(req.dates)
        req_weeks = max(1, round(days_count / 7))

        # Check summer constraint
        summer_start = date(req.dates[0].year, 6, 1)
        labor_day = self._labor_day(req.dates[0].year)
        is_summer = any(summer_start <= d <= labor_day for d in req.dates)

        already_approved_summer_weeks = sum(
            max(1, round(len(r.dates) / 7))
            for r in existing_approved
            if r.nurse_id == nurse.id
            and r.request_type == RequestType.PRE_APPROVED_VACATION
            and r.status == RequestStatus.APPROVED
            and any(summer_start <= d <= labor_day for d in r.dates)
        )

        eligible, reason = check_vacation_eligibility(
            nurse=nurse,
            req_weeks=req_weeks,
            requested_dates=req.dates,
            is_summer=is_summer,
            existing_summer_weeks=already_approved_summer_weeks,
        )

        if not eligible:
            return RequestDecision(
                False,
                reason,
                req.id,
                policy_references=["Pre-Approved Vacation Policy §IV.C, §IV.N"],
            )

        # Check PTO balance
        pto_days_needed = pto_days_per_vacation_week(nurse) * req_weeks
        pto_hrs_needed = pto_days_needed * (12 if nurse.shift_type.value == "12hr" else 8)
        if nurse.pto_hours_balance < pto_hrs_needed:
            return RequestDecision(
                False,
                f"Insufficient PTO balance. Need {pto_hrs_needed} hrs; have {nurse.pto_hours_balance} hrs. "
                "Uncovered days will be forfeited per policy.",
                req.id,
                policy_references=["Pre-Approved Vacation Policy §IV.I"],
            )

        return RequestDecision(
            True,
            f"Pre-approved vacation granted: {req_weeks} week(s) starting {min(req.dates)}. "
            f"PTO to charge: {pto_hrs_needed} hrs.",
            req.id,
            policy_references=["Pre-Approved Vacation Policy §IV.D"],
        )

    def _review_pre_approved_education(
        self,
        req: TimeOffRequest,
        nurse: Nurse,
    ) -> RequestDecision:
        hrs = req.pto_hours_to_use or 8.0
        remaining = nurse.max_pre_approved_education_hours - nurse.pre_approved_education_hours_used
        if hrs > remaining:
            return RequestDecision(
                False,
                f"Exceeds education allowance. Requesting {hrs} hrs; {remaining} hrs remaining.",
                req.id,
                policy_references=["Pre-Approved Vacation Policy §IV.G"],
            )
        return RequestDecision(
            True,
            f"Pre-approved education day granted: {hrs} hrs for '{req.education_activity}'.",
            req.id,
            policy_references=["Pre-Approved Vacation Policy §IV.E"],
        )

    def _review_regular_pto(
        self,
        req: TimeOffRequest,
        nurse: Nurse,
        existing_approved: list[TimeOffRequest],
        schedule: Optional[SchedulePeriod],
    ) -> RequestDecision:
        # Count red days already used this schedule period
        pre_approved_dates: set[date] = set()
        designated_weekends: set[date] = set()

        if schedule:
            for r in existing_approved:
                if r.nurse_id == nurse.id and r.status == RequestStatus.APPROVED:
                    if r.request_type in (
                        RequestType.PRE_APPROVED_VACATION,
                        RequestType.PRE_APPROVED_EDUCATION,
                    ):
                        pre_approved_dates.update(r.dates)

            # Compute designated weekend days in the period
            current = schedule.start_date
            while current <= schedule.end_date:
                if is_designated_weekend_day(current, nurse.designated_weekend):
                    designated_weekends.add(current)
                current += timedelta(days=1)

        existing_red_days = count_red_days(
            nurse,
            [d for r in existing_approved
             if r.nurse_id == nurse.id and r.status == RequestStatus.APPROVED
             for d in r.dates],
            pre_approved_dates,
            designated_weekends,
        )

        new_red_days = count_red_days(
            nurse, req.dates, pre_approved_dates, designated_weekends
        )

        if existing_red_days + new_red_days > MAX_RED_DAYS_PER_SCHEDULE:
            allowed = MAX_RED_DAYS_PER_SCHEDULE - existing_red_days
            return RequestDecision(
                False,
                f"Exceeds maximum of {MAX_RED_DAYS_PER_SCHEDULE} red days per schedule. "
                f"Used {existing_red_days}, requesting {new_red_days}. Only {allowed} more allowed.",
                req.id,
                policy_references=["Staffing & Scheduling Policy §II.A.3"],
            )

        # Check PTO balance
        hrs_needed = req.pto_hours_to_use or (
            len(req.dates) * (12 if nurse.shift_type.value == "12hr" else 8)
        )
        if nurse.pto_hours_balance < hrs_needed:
            return RequestDecision(
                False,
                f"Insufficient PTO: need {hrs_needed} hrs, balance is {nurse.pto_hours_balance} hrs.",
                req.id,
            )

        return RequestDecision(
            True,
            f"PTO request approved: {len(req.dates)} day(s). Charging {hrs_needed} hrs PTO.",
            req.id,
            policy_references=["Staffing & Scheduling Policy §II.A"],
        )

    # ── A-Day (Voluntary Absent Day) ──────────────────────────────────────────

    def review_a_day_request(
        self,
        req: TimeOffRequest,
        nurse: Nurse,
        nurses_on_shift: list[Nurse],
        today: date = None,
    ) -> RequestDecision:
        today = today or date.today()
        eligible, reason = validate_a_day_request(
            nurse=nurse,
            requested_date=req.dates[0],
            request_submitted=today,
            nurses_on_shift=nurses_on_shift,
        )

        if not eligible:
            return RequestDecision(
                False,
                reason,
                req.id,
                policy_references=["Staffing Absent Day §III.D"],
            )

        return RequestDecision(
            True,
            f"Voluntary A-day request accepted for {req.dates[0]}. "
            "Check website 75 minutes before shift start to confirm status. "
            "You have 15 minutes to accept or deny once notified.",
            req.id,
            policy_references=["Staffing Absent Day §III.D.8"],
        )

    def grant_mandatory_a_day(
        self,
        nurses_on_shift: list[Nurse],
        extra_staff: int,
        notify_minutes_before: int = MANDATORY_A_DAY_NOTICE_MINUTES,
    ) -> list[tuple[str, str]]:
        """
        Determines who receives mandatory A-days when unit is overstaffed.
        Returns list of (nurse_id, reason) tuples.
        CRONA order: Staffing Absent Day §III.E.1.
        """
        ordered = mandatory_a_day_order(nurses_on_shift)
        results = []
        for nurse in ordered[:extra_staff]:
            results.append((
                nurse.id,
                f"Mandatory Absent Day issued to {nurse.name} "
                f"({nurse.employee_type.value}, seniority {nurse.seniority_years:.1f} yrs). "
                f"Notification sent ≥{notify_minutes_before} min before shift start. "
                "CRONA: if called back after 1 hour, time-and-a-half applies.",
            ))
        return results

    # ── Shift Swap ────────────────────────────────────────────────────────────

    def review_shift_swap(
        self,
        swap: ShiftSwapRequest,
        nurse1: Nurse,
        nurse2: Nurse,
        today: date = None,
    ) -> RequestDecision:
        today = today or date.today()
        valid, reason = validate_shift_swap(swap, nurse1, nurse2, today)
        if not valid:
            return RequestDecision(
                False,
                reason,
                swap.id,
                policy_references=["Staffing & Scheduling Policy §II.B.2.b"],
            )

        return RequestDecision(
            True,
            f"Shift swap between {nurse1.name} and {nurse2.name} on {swap.trade_date} "
            "is valid. Pending manager approval.",
            swap.id,
            policy_references=["Staffing & Scheduling Policy §II.B.2.b"],
        )

    # ── Overtime Prevention ───────────────────────────────────────────────────

    def check_overtime_risk(
        self,
        nurse: Nurse,
        shift_slot: ShiftSlot,
        current_weekly_hours: float,
    ) -> tuple[bool, str]:
        """
        Staffing & Scheduling §I.C — staff must notify RSN/PCM 2 hours before
        shift end if overtime >15 min anticipated.
        """
        from .models import SHIFT_TIMES
        _, _, shift_hrs = SHIFT_TIMES[shift_slot]
        weekly_ot_threshold = 36.0 if nurse.shift_type.value == "12hr" else 40.0

        if current_weekly_hours + shift_hrs > weekly_ot_threshold:
            return True, (
                f"Adding this {shift_hrs}-hr shift would put {nurse.name} at "
                f"{current_weekly_hours + shift_hrs} hrs/week (threshold: {weekly_ot_threshold} hrs). "
                "Notify RSN/PCM 2 hours before shift end if >15 min overtime is expected."
            )
        return False, "No overtime risk."

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _labor_day(year: int) -> date:
        """First Monday of September."""
        d = date(year, 9, 1)
        while d.weekday() != 0:
            d += timedelta(days=1)
        return d

    def create_time_off_request(
        self,
        nurse_id: str,
        request_type: RequestType,
        dates: list[date],
        pto_hours: float = 0.0,
        education_activity: str = "",
    ) -> TimeOffRequest:
        return TimeOffRequest(
            id=str(uuid.uuid4()),
            nurse_id=nurse_id,
            request_type=request_type,
            dates=dates,
            pto_hours_to_use=pto_hours,
            education_activity=education_activity,
        )

    def create_swap_request(
        self,
        requesting_nurse_id: str,
        accepting_nurse_id: str,
        original_shift_id: str,
        swap_shift_id: str,
        trade_date: date,
    ) -> ShiftSwapRequest:
        return ShiftSwapRequest(
            id=str(uuid.uuid4()),
            requesting_nurse_id=requesting_nurse_id,
            accepting_nurse_id=accepting_nurse_id,
            original_shift_id=original_shift_id,
            swap_shift_id=swap_shift_id,
            trade_date=trade_date,
        )
