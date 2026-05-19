"""
Core Scheduling Engine.

Generates a compliant 4-week schedule from department needs, applying
all SHC/CRONA policy constraints in the correct priority order.
"""

from __future__ import annotations
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from .models import (
    AssignedShift, DepartmentNeedsInput, EmployeeType, Nurse,
    NurseRole, SchedulePeriod, ShiftRequirement, ShiftSlot,
    ShiftSwapRequest, TimeOffRequest, SHIFT_TIMES, RequestType, RequestStatus,
)
from .policy_engine import (
    cancellation_order, float_order, validate_fte_compliance,
    fte_hours_in_period, is_designated_weekend_day,
    would_incur_overtime, can_float,
)


class SchedulingResult:
    def __init__(self) -> None:
        self.schedule: Optional[SchedulePeriod] = None
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.coverage_gaps: list[dict] = []
        self.fte_report: dict[str, dict] = {}

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


class Scheduler:
    """
    Constraint-based scheduler that:
    1. Locks pre-approved time off
    2. Assigns designated weekends
    3. Fills requirements by priority/seniority
    4. Checks FTE compliance
    5. Identifies gaps and suggests floats/relief
    """

    def __init__(self, nurses: list[Nurse]) -> None:
        self.nurses = {n.id: n for n in nurses}

    # ── Main Entry Point ─────────────────────────────────────────────────────

    def generate_schedule(
        self,
        needs: DepartmentNeedsInput,
        approved_time_off: list[TimeOffRequest],
    ) -> SchedulingResult:
        result = SchedulingResult()

        period = SchedulePeriod(
            id=str(uuid.uuid4()),
            start_date=needs.schedule_start,
            end_date=needs.schedule_end,
            unit=needs.unit,
            requirements=needs.daily_requirements,
        )

        # Build unavailability index: nurse_id → set of dates off
        unavailable: dict[str, set[date]] = defaultdict(set)
        for req in approved_time_off:
            if req.status == RequestStatus.APPROVED:
                for d in req.dates:
                    unavailable[req.nurse_id].add(d)

        # Track assigned hours per nurse in this period
        assigned_hours: dict[str, float] = defaultdict(float)
        assignments: list[AssignedShift] = []

        # Group requirements by date for daily processing
        by_date: dict[date, list[ShiftRequirement]] = defaultdict(list)
        for req in needs.daily_requirements:
            by_date[req.date].append(req)

        current = needs.schedule_start
        while current <= needs.schedule_end:
            daily_reqs = by_date.get(current, [])
            for req in sorted(daily_reqs, key=lambda r: self._shift_priority(r)):
                filled = self._fill_requirement(
                    req=req,
                    date_=current,
                    unavailable=unavailable,
                    assigned_hours=assigned_hours,
                    period_start=needs.schedule_start,
                    period_end=needs.schedule_end,
                )
                if len(filled) < req.count:
                    gap = req.count - len(filled)
                    result.coverage_gaps.append({
                        "date": current.isoformat(),
                        "shift": req.shift_slot.value,
                        "unit": req.unit,
                        "gap": gap,
                        "required_role": req.required_role.value,
                    })
                    result.warnings.append(
                        f"Coverage gap on {current} {req.shift_slot.value}: "
                        f"need {req.count}, filled {len(filled)} ({gap} short)."
                    )

                for nurse_id in filled:
                    start_t, end_t, hrs = SHIFT_TIMES[req.shift_slot]
                    nurse = self.nurses[nurse_id]
                    # 12-hr nurses on day/evening slots work 12 hrs, not 8
                    if req.shift_slot in (ShiftSlot.DAY, ShiftSlot.EVENING) and nurse.shift_type.value == "12hr":
                        hrs = 12.0
                    assignment = AssignedShift(
                        requirement_id=str(uuid.uuid4()),
                        nurse_id=nurse_id,
                        date=current,
                        shift_slot=req.shift_slot,
                        unit=req.unit,
                        start_time=start_t,
                        end_time=end_t,
                        hours=hrs,
                        is_over_commitment=assigned_hours[nurse_id] + hrs
                            > fte_hours_in_period(nurse, needs.schedule_start, needs.schedule_end),
                    )
                    assignments.append(assignment)
                    assigned_hours[nurse_id] += hrs
                    unavailable[nurse_id].add(current)  # avoid double-booking

            current += timedelta(days=1)

        period.assignments = assignments

        # FTE compliance report
        for nurse_id, nurse in self.nurses.items():
            required = fte_hours_in_period(nurse, needs.schedule_start, needs.schedule_end)
            actual = assigned_hours.get(nurse_id, 0.0)
            compliant, diff = validate_fte_compliance(
                nurse, actual, needs.schedule_start, needs.schedule_end
            )
            result.fte_report[nurse_id] = {
                "name": nurse.name,
                "fte": nurse.fte,
                "required_hours": required,
                "assigned_hours": round(actual, 1),
                "deficit": round(diff, 1),
                "compliant": compliant,
            }
            if not compliant and diff > 0:
                result.warnings.append(
                    f"{nurse.name}: under-scheduled by {diff:.1f} hrs "
                    f"(FTE {nurse.fte}). Required: {required} hrs, assigned: {actual} hrs."
                )

        result.schedule = period
        return result

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _shift_priority(self, req: ShiftRequirement) -> int:
        """Charge/resource roles fill first to ensure specialty coverage."""
        if req.charge_needed or req.resource_needed:
            return 0
        if req.required_specialties:
            return 1
        return 2

    def _fill_requirement(
        self,
        req: ShiftRequirement,
        date_: date,
        unavailable: dict[str, set[date]],
        assigned_hours: dict[str, float],
        period_start: date,
        period_end: date,
    ) -> list[str]:
        """
        Returns list of nurse IDs that fill this requirement slot.
        Applies priority: pre-approved time off locked → skill match →
        seniority → FTE compliance.
        """
        candidates = self._eligible_nurses(req, date_, unavailable)
        if not candidates:
            return []

        # Sort by fill priority
        candidates = sorted(candidates, key=lambda n: self._fill_rank(
            n, req, date_, assigned_hours, period_start, period_end
        ))

        selected: list[str] = []
        for nurse in candidates:
            if len(selected) >= req.count:
                break
            nurse_hours = assigned_hours.get(nurse.id, 0.0)
            required_total = fte_hours_in_period(nurse, period_start, period_end)
            _, _, shift_hrs = SHIFT_TIMES[req.shift_slot]
            if req.shift_slot in (ShiftSlot.DAY, ShiftSlot.EVENING) and nurse.shift_type.value == "12hr":
                shift_hrs = 12.0

            # Prefer nurses who still need hours to meet FTE; allow over only if needed
            if nurse_hours + shift_hrs > required_total + 12 and not self._is_understaffed(selected, req):
                continue  # skip severely over-committed unless unit needs it

            selected.append(nurse.id)

        return selected

    def _eligible_nurses(
        self,
        req: ShiftRequirement,
        date_: date,
        unavailable: dict[str, set[date]],
    ) -> list[Nurse]:
        eligible = []
        for nurse in self.nurses.values():
            # Skip if on approved time off or already assigned today
            if date_ in unavailable.get(nurse.id, set()):
                continue

            # Shift type compatibility
            if req.shift_slot in (ShiftSlot.NIGHT_12,) and nurse.shift_type.value != "12hr":
                continue

            # Role requirements
            if req.charge_needed and not nurse.can_charge:
                continue
            if req.resource_needed and not nurse.can_resource:
                continue

            # Unit/region match (regular staff must be in their unit or float region)
            if nurse.employee_type == EmployeeType.REGULAR:
                in_home_unit = req.unit in nurse.float_regions or req.unit in (nurse.cross_trained_units or [])
                if not in_home_unit and not self._is_primary_unit(nurse, req.unit):
                    # Would need to float — check eligibility
                    floatable, _ = can_float(nurse)
                    if not floatable:
                        continue

            # Specialty match
            if req.required_specialties:
                if not any(s in nurse.specialties for s in req.required_specialties):
                    continue

            eligible.append(nurse)

        return eligible

    def _fill_rank(
        self,
        nurse: Nurse,
        req: ShiftRequirement,
        date_: date,
        assigned_hours: dict[str, float],
        period_start: date,
        period_end: date,
    ) -> tuple:
        """
        Lower rank = higher priority for assignment.
        Priority: meet FTE > seniority > fewer hours so far.
        """
        required = fte_hours_in_period(nurse, period_start, period_end)
        current_hrs = assigned_hours.get(nurse.id, 0.0)
        deficit = required - current_hrs  # positive = still needs hours
        is_weekend = is_designated_weekend_day(date_, nurse.designated_weekend)

        return (
            -deficit,            # nurses most behind on FTE fill first
            -nurse.seniority_years,  # more senior fills first (tie-break)
            1 if is_weekend else 0,  # prefer non-weekend day on designated weekends
        )

    def _is_primary_unit(self, nurse: Nurse, unit: str) -> bool:
        return unit in nurse.float_regions

    def _is_understaffed(self, selected: list[str], req: ShiftRequirement) -> bool:
        return len(selected) < req.count

    # ── Overstaffing Resolution ───────────────────────────────────────────────

    def resolve_overstaffing(
        self,
        date_: date,
        shift_slot: ShiftSlot,
        unit: str,
        extra_count: int,
        nurses_on_shift: list[Nurse],
        voluntary_requests: list[str],
    ) -> list[str]:
        """
        Returns list of nurse IDs to cancel, in policy order.
        Staffing & Scheduling §II.D.2.
        """
        # Voluntary first
        to_cancel: list[str] = []
        remaining = list(nurses_on_shift)

        for nid in voluntary_requests:
            if nid in [n.id for n in remaining]:
                to_cancel.append(nid)
                remaining = [n for n in remaining if n.id != nid]
            if len(to_cancel) >= extra_count:
                return to_cancel

        # Then mandatory order
        ordered = cancellation_order(remaining)
        for nurse in ordered:
            if len(to_cancel) >= extra_count:
                break
            to_cancel.append(nurse.id)

        return to_cancel[:extra_count]

    # ── Float Resolution ──────────────────────────────────────────────────────

    def resolve_understaffing_via_float(
        self,
        date_: date,
        shift_slot: ShiftSlot,
        target_unit: str,
        needed: int,
        surplus_nurses: list[Nurse],
        voluntary_float_ids: list[str],
    ) -> list[str]:
        """
        Returns nurse IDs to float in. Floating Policy §IV.C.5.
        """
        to_float: list[str] = []

        # Voluntary volunteers first
        for nid in voluntary_float_ids:
            match = next((n for n in surplus_nurses if n.id == nid), None)
            if match:
                eligible, _ = can_float(match)
                if eligible:
                    to_float.append(nid)
            if len(to_float) >= needed:
                return to_float

        # Then ordered float list
        ordered = float_order(surplus_nurses, target_unit)
        for nurse in ordered:
            if nurse.id in to_float:
                continue
            eligible, _ = can_float(nurse)
            if eligible:
                to_float.append(nurse.id)
            if len(to_float) >= needed:
                break

        return to_float[:needed]

    # ── Shift Swap Processing ─────────────────────────────────────────────────

    def execute_shift_swap(
        self,
        swap: ShiftSwapRequest,
        schedule: SchedulePeriod,
    ) -> tuple[bool, str]:
        """
        Executes an approved shift swap by swapping assignments in the schedule.
        """
        if not swap.manager_approved:
            return False, "Swap requires manager approval before execution."

        a1 = next((a for a in schedule.assignments if a.requirement_id == swap.original_shift_id), None)
        a2 = next((a for a in schedule.assignments if a.requirement_id == swap.swap_shift_id), None)

        if not a1 or not a2:
            return False, "One or both shift assignments not found in schedule."

        # Swap nurse IDs
        a1.nurse_id, a2.nurse_id = a2.nurse_id, a1.nurse_id
        return True, "Shift swap executed successfully."
