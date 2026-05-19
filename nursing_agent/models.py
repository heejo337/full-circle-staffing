"""
Data models for the Nursing Scheduling Agent.
All scheduling rules derived from SHC/CRONA CBA (2025-2028) and SHC policies.
"""

from __future__ import annotations
from datetime import date, time, datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Enumerations ────────────────────────────────────────────────────────────

class EmployeeType(str, Enum):
    REGULAR = "regular"
    RELIEF = "relief"
    TRAVELER = "traveler"
    REGISTRY = "registry"

class ShiftType(str, Enum):
    EIGHT_HOUR = "8hr"
    TWELVE_HOUR = "12hr"

class ShiftSlot(str, Enum):
    """Standard SHC shift start windows (Staffing & Scheduling Policy §I.A.1)."""
    DAY = "day"          # 6:45 AM start
    EVENING = "evening"  # 2:45 PM start
    NIGHT_8 = "night_8"  # 10:45 PM start (8-hr)
    NIGHT_12 = "night_12"  # 6:45 PM start (12-hr)

class WeekendPattern(str, Enum):
    """Designated weekend patterns (Scheduling Policy §II.A.4-6)."""
    SAT_SUN = "sat_sun"    # Day default; Night option
    FRI_SAT = "fri_sat"    # Night option
    FRI_SUN = "fri_sun"    # Night option

class NurseRole(str, Enum):
    RN = "rn"              # Staff nurse
    CHARGE = "charge"      # Charge nurse
    RESOURCE = "resource"  # Resource / supervisory nurse (RSN)
    NA = "na"              # Nursing assistant
    US = "us"              # Unit secretary

class RequestType(str, Enum):
    PTO = "pto"
    A_DAY = "a_day"           # Voluntary absent day
    MANDATORY_A_DAY = "mandatory_a_day"
    SHIFT_SWAP = "shift_swap"
    PRE_APPROVED_VACATION = "pre_approved_vacation"
    PRE_APPROVED_EDUCATION = "pre_approved_education"
    FLOAT = "float"

class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"


# ── Core Models ──────────────────────────────────────────────────────────────

class Nurse(BaseModel):
    id: str
    name: str
    employee_type: EmployeeType = EmployeeType.REGULAR
    role: NurseRole = NurseRole.RN
    fte: float = Field(ge=0.1, le=1.0, description="FTE commitment (0.5–1.0)")
    shift_type: ShiftType = ShiftType.TWELVE_HOUR
    shift_slot: ShiftSlot = ShiftSlot.DAY
    designated_weekend: WeekendPattern = WeekendPattern.SAT_SUN

    hire_date: date
    adjusted_hire_date: Optional[date] = None

    # Competencies
    specialties: list[str] = Field(default_factory=list)
    float_regions: list[str] = Field(default_factory=list)
    can_charge: bool = False
    can_resource: bool = False
    cross_trained_units: list[str] = Field(default_factory=list)

    # Time banks
    pto_hours_balance: float = 0.0
    education_hours_balance: float = 0.0

    # Schedule period tracking
    pre_approved_vacation_weeks_used: int = 0
    pre_approved_education_hours_used: float = 0.0

    # A-day tracking (resets each pay period)
    a_days_this_pay_period: int = 0
    cancelled_hours_this_pay_period: float = 0.0

    # Float tracking
    last_float_date: Optional[date] = None
    float_count_this_schedule: int = 0

    # Gamification
    gamification_points: int = 0
    total_no_call_outs: int = 0
    total_on_time: int = 0
    total_shifts_picked_up: int = 0
    total_swaps_completed: int = 0
    current_streak_days: int = 0

    @property
    def seniority_years(self) -> float:
        ref = self.adjusted_hire_date or self.hire_date
        return (date.today() - ref).days / 365.25

    @property
    def biweekly_hours_commitment(self) -> float:
        """Hours required in a 2-week pay period based on FTE and shift type."""
        if self.shift_type == ShiftType.TWELVE_HOUR:
            # 12-hr nurses: 3 shifts/wk at 1.0 FTE = 36hrs/wk target
            return round(self.fte * 72, 1)
        else:
            # 8-hr nurses: 5 shifts/wk at 1.0 FTE = 40hrs/wk
            return round(self.fte * 80, 1)

    @property
    def max_pre_approved_vacation_weeks(self) -> int:
        """Updated vacation allotment per policy (effective Jan 1, 2024)."""
        y = self.seniority_years
        if y < 3:
            return 3
        elif y < 10:
            return 4
        return 5

    @property
    def max_pre_approved_education_hours(self) -> float:
        return 40.0

    @property
    def is_float_exempt(self) -> bool:
        """30+ year seniority float exemption (Floating Policy §IV.C.2.a)."""
        return self.seniority_years >= 30


class ShiftRequirement(BaseModel):
    """One slot on the department need grid submitted by the manager."""
    date: date
    shift_slot: ShiftSlot
    unit: str
    count: int = 1
    required_role: NurseRole = NurseRole.RN
    required_specialties: list[str] = Field(default_factory=list)
    charge_needed: bool = False
    resource_needed: bool = False
    notes: str = ""


class AssignedShift(BaseModel):
    """A resolved shift assignment linking a nurse to a requirement."""
    requirement_id: str
    nurse_id: str
    date: date
    shift_slot: ShiftSlot
    unit: str
    start_time: time
    end_time: time
    hours: float
    is_over_commitment: bool = False
    is_float: bool = False
    float_from_unit: Optional[str] = None


class SchedulePeriod(BaseModel):
    """A 4-week (28-day) schedule period."""
    id: str
    start_date: date
    end_date: date
    unit: str
    requirements: list[ShiftRequirement] = Field(default_factory=list)
    assignments: list[AssignedShift] = Field(default_factory=list)
    published: bool = False
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)


class TimeOffRequest(BaseModel):
    id: str
    nurse_id: str
    request_type: RequestType
    dates: list[date]
    shift_slot: Optional[ShiftSlot] = None
    status: RequestStatus = RequestStatus.PENDING
    submitted_at: datetime = Field(default_factory=datetime.now)
    decided_at: Optional[datetime] = None
    decision_reason: str = ""
    pto_hours_to_use: float = 0.0
    # For pre-approved education
    education_activity: str = ""


class ShiftSwapRequest(BaseModel):
    id: str
    requesting_nurse_id: str
    accepting_nurse_id: Optional[str] = None
    original_shift_id: str
    swap_shift_id: Optional[str] = None
    status: RequestStatus = RequestStatus.PENDING
    submitted_at: datetime = Field(default_factory=datetime.now)
    trade_date: date
    notes: str = ""
    manager_approved: bool = False


class DepartmentNeedsInput(BaseModel):
    """Manager submits this to trigger schedule generation."""
    unit: str
    schedule_start: date
    schedule_end: date
    daily_requirements: list[ShiftRequirement]
    notes: str = ""


class ICUShiftCensus(BaseModel):
    """Census and acuity snapshot for a single ICU shift."""
    date: date
    shift_slot: ShiftSlot
    critical_patients: int = 0   # require 1:1 RN ratio
    stable_patients: int = 0     # require 1:2 RN ratio
    vent_patients: int = 0       # subset requiring vent-trained RN
    ecmo_patients: int = 0       # subset requiring ECMO-trained RN

    @property
    def required_bedside_rns(self) -> int:
        import math
        return self.critical_patients + math.ceil(self.stable_patients / 2)

    @property
    def total_census(self) -> int:
        return self.critical_patients + self.stable_patients


class ICUOperationalNeeds(BaseModel):
    """Manager-submitted ICU staffing needs derived from census and acuity."""
    unit: str
    bed_capacity: int = 20
    schedule_start: date
    schedule_end: date
    census_entries: list[ICUShiftCensus] = Field(default_factory=list)
    charge_each_shift: bool = True
    resource_nurse_needed: bool = False
    vent_specialist_needed: bool = False
    ecmo_capable_needed: bool = False
    crrt_capable_needed: bool = False
    notes: str = ""


class ShiftRating(BaseModel):
    nurse_id: str
    shift_date: date
    shift_slot: ShiftSlot
    unit: str
    rating: int = Field(ge=0, le=5, description="0–5 star shift rating")
    comments: str = ""
    submitted_at: datetime = Field(default_factory=datetime.now)


class GamificationEvent(BaseModel):
    id: str
    nurse_id: str
    event_type: str
    points_awarded: int
    description: str
    occurred_at: datetime = Field(default_factory=datetime.now)


class Badge(BaseModel):
    id: str
    name: str
    description: str
    icon: str


# ── Shift Time Lookup ────────────────────────────────────────────────────────

SHIFT_TIMES: dict[ShiftSlot, tuple[time, time, float]] = {
    ShiftSlot.DAY:      (time(6, 45),  time(15, 15), 8.0),
    ShiftSlot.EVENING:  (time(14, 45), time(23, 15), 8.0),
    ShiftSlot.NIGHT_8:  (time(22, 45), time(7, 15),  8.0),
    ShiftSlot.NIGHT_12: (time(18, 45), time(7, 15),  12.0),
}

# ── Gamification Point Values ────────────────────────────────────────────────

POINTS = {
    "no_call_out":          50,
    "on_time":              10,
    "shift_pickup":         75,
    "shift_pickup_short_notice": 100,  # <4 hrs notice
    "swap_completed":       25,
    "5_star_rating":        20,
    "4_star_rating":        10,
    "streak_7_days":        50,
    "streak_30_days":       200,
    "volunteer_float":      30,
    "perfect_attendance_pp": 150,     # full pay period no call-outs
}

BADGES: list[Badge] = [
    Badge(id="reliable_rn",     name="Reliable RN",       description="Zero call-outs for 3 months",       icon="🏆"),
    Badge(id="team_player",     name="Team Player",       description="Completed 10 shift swaps",           icon="🤝"),
    Badge(id="shift_hero",      name="Shift Hero",        description="Picked up 5 short-notice shifts",    icon="🦸"),
    Badge(id="early_bird",      name="Early Bird",        description="On-time 30 shifts in a row",         icon="⏰"),
    Badge(id="float_champ",     name="Float Champion",    description="Volunteered to float 10 times",      icon="🌊"),
    Badge(id="5_star_nurse",    name="5-Star Nurse",      description="10 five-star shift ratings",         icon="⭐"),
    Badge(id="veteran",         name="Veteran",           description="10+ years seniority",                icon="🎖️"),
    Badge(id="century_points",  name="Century Club",      description="Earned 1,000 gamification points",   icon="💯"),
]
