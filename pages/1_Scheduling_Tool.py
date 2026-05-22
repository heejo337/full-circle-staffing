"""
Nursing Scheduling Agent — Web Interface
Run: streamlit run app.py
"""

import json
import os
import sys
from datetime import date, timedelta, datetime
from pathlib import Path

import streamlit as st

# ── Path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from nursing_agent import storage
from nursing_agent.models import (
    DepartmentNeedsInput, ShiftRequirement, ShiftSlot, NurseRole,
    RequestType, RequestStatus, Nurse, ShiftType, EmployeeType, WeekendPattern,
    SHIFT_TIMES, ICUShiftCensus, ICUOperationalNeeds,
)
from nursing_agent.agent import NursingSchedulingAgent
from nursing_agent.policy_engine import cancellation_order, float_order

# Page config is set in the main app.py

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
.chat-user   { background:#e8f4fd; border-radius:12px; padding:10px 14px; margin:6px 0; }
.chat-agent  { background:#f0f7f0; border-radius:12px; padding:10px 14px; margin:6px 0; }
.badge       { display:inline-block; background:#ffd700; color:#333; border-radius:8px;
               padding:2px 8px; font-size:0.8rem; margin:2px; }
.stat-box    { background:#f8f9fa; border-radius:8px; padding:12px; text-align:center; }
.approved    { color:#28a745; font-weight:bold; }
.denied      { color:#dc3545; font-weight:bold; }
.gap-warning { color:#dc3545; }
section[data-testid="stSidebar"] { width: 240px !important; }
</style>
""", unsafe_allow_html=True)


# ── Agent init ────────────────────────────────────────────────────────────────
def agent() -> NursingSchedulingAgent:
    return NursingSchedulingAgent(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


# ── Schedule-building helpers ────────────────────────────────────────────────

def _build_default_needs(unit: str, start: date, weeks: int) -> DepartmentNeedsInput:
    end = start + timedelta(weeks=weeks) - timedelta(days=1)
    return _build_custom_needs(unit, start, end, 3, 2, 2, True)


def _build_custom_needs(
    unit: str, start: date, end: date,
    day_rn: int, eve_rn: int, night_rn: int, charge_day: bool,
) -> DepartmentNeedsInput:
    reqs = []
    current = start
    while current <= end:
        if day_rn:
            reqs.append(ShiftRequirement(
                date=current, shift_slot=ShiftSlot.DAY, unit=unit,
                count=day_rn, required_role=NurseRole.RN, charge_needed=charge_day,
            ))
        if eve_rn:
            reqs.append(ShiftRequirement(
                date=current, shift_slot=ShiftSlot.EVENING, unit=unit,
                count=eve_rn, required_role=NurseRole.RN,
            ))
        if night_rn:
            reqs.append(ShiftRequirement(
                date=current, shift_slot=ShiftSlot.NIGHT_12, unit=unit,
                count=night_rn, required_role=NurseRole.RN,
            ))
        current += timedelta(days=1)
    return DepartmentNeedsInput(
        unit=unit, schedule_start=start, schedule_end=end, daily_requirements=reqs,
    )


def _check_request_eligibility(
    req, nurse, all_requests: list, all_nurses: list
) -> tuple[list[str], list[str], list[str]]:
    """
    Returns (blocks, warnings, info).
    blocks  = hard policy violations that prevent approval
    warnings = soft concerns the manager should weigh
    info    = neutral context (balance, seniority, etc.)
    """
    from nursing_agent.policy_engine import (
        check_vacation_eligibility, validate_a_day_request,
        pto_days_per_vacation_week,
    )
    blocks, warnings, info = [], [], []

    hrs_per_day = 12.0 if nurse.shift_type.value == "12hr" else 8.0
    days_requested = len(req.dates)
    hours_needed = hrs_per_day * days_requested

    req_dates_set = {d if isinstance(d, date) else date.fromisoformat(str(d)) for d in req.dates}

    # ── Conflict: same nurse already has approved time-off on any of these dates ──
    same_nurse_approved = [
        r for r in all_requests
        if r.nurse_id == nurse.id and r.id != req.id and r.status.value == "approved"
    ]
    conflict_dates = []
    for r in same_nurse_approved:
        overlap = req_dates_set & {d if isinstance(d, date) else date.fromisoformat(str(d)) for d in r.dates}
        if overlap:
            conflict_dates += sorted(overlap)
    if conflict_dates:
        blocks.append(f"Already has approved time-off on: {', '.join(str(d) for d in conflict_dates)}")

    # ── Seniority context vs other nurses approved on same dates ──────────────
    others_same_dates = []
    for r in all_requests:
        if r.nurse_id == nurse.id or r.status.value != "approved":
            continue
        overlap = req_dates_set & {d if isinstance(d, date) else date.fromisoformat(str(d)) for d in r.dates}
        if overlap:
            other = next((n for n in all_nurses if n.id == r.nurse_id), None)
            if other:
                others_same_dates.append(other)

    sorted_nurses = sorted(all_nurses, key=lambda n: n.seniority_years, reverse=True)
    seniority_rank = next((i + 1 for i, n in enumerate(sorted_nurses) if n.id == nurse.id), len(all_nurses))
    info.append(f"Seniority rank #{seniority_rank} of {len(all_nurses)} ({nurse.seniority_years:.1f} yrs)")

    if others_same_dates:
        seniors = [n.name for n in others_same_dates if n.seniority_years > nurse.seniority_years]
        juniors = [n.name for n in others_same_dates if n.seniority_years <= nurse.seniority_years]
        if seniors:
            warnings.append(f"More-senior nurses already approved on overlapping dates: {', '.join(seniors)}")
        if juniors:
            info.append(f"Less-senior nurses already approved on same dates: {', '.join(juniors)}")

    # ── Request-type specific checks ───────────────────────────────────────────
    rtype = req.request_type.value

    if rtype == "pto":
        if nurse.pto_hours_balance < hours_needed:
            blocks.append(
                f"Insufficient PTO balance: needs {hours_needed:.0f} hrs, "
                f"has {nurse.pto_hours_balance:.0f} hrs"
            )
        else:
            remaining = nurse.pto_hours_balance - hours_needed
            info.append(
                f"PTO balance: {nurse.pto_hours_balance:.0f} hrs → "
                f"{remaining:.0f} hrs remaining after approval"
            )

    elif rtype == "pre_approved_vacation":
        days_per_week = pto_days_per_vacation_week(nurse)
        weeks_requested = max(1, round(days_requested / days_per_week))
        weeks_remaining = nurse.max_pre_approved_vacation_weeks - nurse.pre_approved_vacation_weeks_used
        summer_months = {6, 7, 8, 9}
        is_summer = any(
            (d if isinstance(d, date) else date.fromisoformat(str(d))).month in summer_months
            for d in req.dates
        )
        # Count existing approved summer vacation weeks for this nurse
        summer_weeks_used = 0
        for r in same_nurse_approved:
            if r.request_type.value == "pre_approved_vacation":
                summer_weeks_used += sum(
                    1 for d in r.dates
                    if (d if isinstance(d, date) else date.fromisoformat(str(d))).month in summer_months
                ) // days_per_week

        eligible_vac, vac_reason = check_vacation_eligibility(
            nurse, weeks_requested,
            [d if isinstance(d, date) else date.fromisoformat(str(d)) for d in req.dates],
            is_summer, summer_weeks_used,
        )
        if not eligible_vac:
            blocks.append(vac_reason)
        else:
            info.append(
                f"Vacation allotment: {nurse.pre_approved_vacation_weeks_used}/"
                f"{nurse.max_pre_approved_vacation_weeks} wks used → "
                f"{weeks_remaining} wk(s) remaining"
            )

        pto_needed = days_per_week * weeks_requested * hrs_per_day
        if nurse.pto_hours_balance < pto_needed:
            warnings.append(
                f"Low PTO balance for vacation pay: needs ~{pto_needed:.0f} hrs, "
                f"has {nurse.pto_hours_balance:.0f} hrs"
            )
        else:
            info.append(f"PTO for vacation pay: ~{pto_needed:.0f} hrs needed, {nurse.pto_hours_balance:.0f} hrs available")

        if is_summer:
            info.append(f"Summer period request — current summer weeks used: {summer_weeks_used}")

    elif rtype == "pre_approved_education":
        edu_remaining = nurse.max_pre_approved_education_hours - nurse.pre_approved_education_hours_used
        if edu_remaining <= 0:
            blocks.append(
                f"No education hours remaining "
                f"({nurse.pre_approved_education_hours_used:.0f}/"
                f"{nurse.max_pre_approved_education_hours:.0f} hrs used this year)"
            )
        elif hours_needed > edu_remaining:
            blocks.append(
                f"Insufficient education hours: needs {hours_needed:.0f} hrs, "
                f"only {edu_remaining:.0f} hrs remaining"
            )
        else:
            info.append(
                f"Education hours: {nurse.pre_approved_education_hours_used:.0f}/"
                f"{nurse.max_pre_approved_education_hours:.0f} hrs used → "
                f"{edu_remaining:.0f} hrs remaining after approval"
            )
        if req.education_activity:
            info.append(f"Activity: {req.education_activity}")

    elif rtype == "a_day":
        req_date = req.dates[0] if req.dates else date.today()
        req_date = req_date if isinstance(req_date, date) else date.fromisoformat(str(req_date))
        days_ahead = (req_date - date.today()).days

        if days_ahead > 28:
            blocks.append(f"Submitted too far in advance: {days_ahead} days (max 28 days / 4 weeks)")
        elif days_ahead < 0:
            blocks.append("Requested date is in the past")

        if nurse.a_days_this_pay_period >= 2:
            blocks.append(
                f"A-day limit reached this pay period "
                f"({nurse.a_days_this_pay_period}/2 used)"
            )
        else:
            info.append(f"A-days this pay period: {nurse.a_days_this_pay_period}/2 used")

        # Equity: others without an A-day this PP have priority
        others_no_aday = [
            n for n in all_nurses
            if n.id != nurse.id and n.a_days_this_pay_period == 0
        ]
        if nurse.a_days_this_pay_period > 0 and others_no_aday:
            warnings.append(
                f"{len(others_no_aday)} colleague(s) have not yet had an A-day "
                "this pay period and have priority per CRONA §III.D.6"
            )

    return blocks, warnings, info


def _check_swap_eligibility(
    swap, all_nurses: list, all_requests: list
) -> tuple[list[str], list[str], list[str]]:
    from nursing_agent.policy_engine import validate_shift_swap
    blocks, warnings, info = [], [], []

    req_nurse = next((n for n in all_nurses if n.id == swap.requesting_nurse_id), None)
    acc_nurse = next((n for n in all_nurses if n.id == swap.accepting_nurse_id), None)

    if not req_nurse or not acc_nurse:
        blocks.append("One or both nurses not found in roster")
        return blocks, warnings, info

    trade_date = swap.trade_date if isinstance(swap.trade_date, date) else date.fromisoformat(str(swap.trade_date))

    # Lead-time check via policy engine
    valid, reason = validate_shift_swap(swap, req_nurse, acc_nurse, date.today())
    if not valid:
        blocks.append(reason)
    else:
        days_until = (trade_date - date.today()).days
        info.append(f"Lead time: {days_until} days ✓ (minimum 3 required per Scheduling Policy §II.B.2.b)")

    # Check both nurses for approved time-off on trade date
    for nurse in (req_nurse, acc_nurse):
        conflict = [
            r for r in all_requests
            if r.nurse_id == nurse.id and r.status.value == "approved"
            and trade_date in {d if isinstance(d, date) else date.fromisoformat(str(d)) for d in r.dates}
        ]
        if conflict:
            blocks.append(f"{nurse.name} has approved time-off on {trade_date}")

    # Seniority info for both nurses
    sorted_nurses = sorted(all_nurses, key=lambda n: n.seniority_years, reverse=True)
    for nurse in (req_nurse, acc_nurse):
        rank = next((i + 1 for i, n in enumerate(sorted_nurses) if n.id == nurse.id), "?")
        info.append(f"{nurse.name}: seniority rank #{rank} ({nurse.seniority_years:.1f} yrs), FTE {nurse.fte}")

    return blocks, warnings, info


def _icu_needs_to_department_needs(icu_needs: ICUOperationalNeeds) -> DepartmentNeedsInput:
    import math
    reqs = []
    for entry in icu_needs.census_entries:
        bedside_rns = entry.required_bedside_rns
        if bedside_rns == 0:
            continue
        specialties = []
        if icu_needs.vent_specialist_needed and entry.vent_patients > 0:
            specialties.append("vent")
        if icu_needs.ecmo_capable_needed and entry.ecmo_patients > 0:
            specialties.append("ecmo")
        if icu_needs.crrt_capable_needed:
            specialties.append("crrt")
        reqs.append(ShiftRequirement(
            date=entry.date,
            shift_slot=entry.shift_slot,
            unit=icu_needs.unit,
            count=bedside_rns,
            required_role=NurseRole.RN,
            required_specialties=specialties,
            charge_needed=(icu_needs.charge_each_shift and entry.shift_slot == ShiftSlot.DAY),
            resource_needed=icu_needs.resource_nurse_needed,
            notes=(
                f"Census {entry.total_census} "
                f"(critical {entry.critical_patients}, stable {entry.stable_patients}"
                + (f", vent {entry.vent_patients}" if entry.vent_patients else "")
                + (f", ecmo {entry.ecmo_patients}" if entry.ecmo_patients else "")
                + ")"
            ),
        ))
    return DepartmentNeedsInput(
        unit=icu_needs.unit,
        schedule_start=icu_needs.schedule_start,
        schedule_end=icu_needs.schedule_end,
        daily_requirements=reqs,
        notes=icu_needs.notes,
    )


# ── Session state defaults ────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "api_key_set" not in st.session_state:
    st.session_state.api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))


# ════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("🏥 FullCircle")
    st.caption("SHC / CRONA Compliant")
    st.divider()

    if not st.session_state.api_key_set:
        key = st.text_input("Anthropic API Key", type="password", placeholder="sk-ant-...")
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key.strip()
            st.session_state.api_key_set = True
            st.rerun()
    else:
        st.success("API key active", icon="🔑")
        if st.button("🔑 Reset key", use_container_width=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            st.session_state.api_key_set = False
            st.rerun()

    st.divider()
    nurses = storage.load_nurses()
    st.metric("Staff on Roster", len(nurses))

    schedules = storage.load_schedules()
    active = [s for s in schedules if s.start_date <= date.today() <= s.end_date]
    st.metric("Active Schedules", len(active))

    pending = [r for r in storage.load_time_off_requests() if r.status == RequestStatus.PENDING]
    st.metric("Pending Requests", len(pending))

    st.divider()
    st.caption("Quick actions")
    if st.button("🔄  Refresh data", use_container_width=True):
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# TABS
# ════════════════════════════════════════════════════════════════════════════
tab_chat, tab_schedule, tab_requests, tab_roster, tab_gamification, tab_icu = st.tabs([
    "💬 Agent Chat",
    "📅 Schedule",
    "📋 Requests",
    "👥 Roster",
    "🏆 Gamification",
    "🏥 ICU Schedule",
])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — AGENT CHAT
# ════════════════════════════════════════════════════════════════════════════
with tab_chat:
    st.header("Talk to the Scheduling Agent")
    st.caption("Ask anything: generate a schedule, check policy, process a request, or get a status update.")

    if not st.session_state.api_key_set:
        st.warning("Enter your Anthropic API key in the sidebar to start chatting.")
    else:
        # Render conversation history
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                st.markdown(f'<div class="chat-user">👤 <b>You</b><br>{msg["content"]}</div>',
                            unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="chat-agent">🤖 <b>Agent</b><br>{msg["content"]}</div>',
                            unsafe_allow_html=True)

        # Suggested prompts
        if not st.session_state.messages:
            st.markdown("**Try asking:**")
            cols = st.columns(2)
            prompts = [
                "Generate a 4-week schedule for unit K5",
                "What is the float order when a unit is short-staffed?",
                "How many vacation weeks does a nurse with 8 years get?",
                "Who should be cancelled first when K5 is overstaffed?",
                "What are the A-day request rules?",
                "Show me the top performers on the leaderboard",
            ]
            for i, p in enumerate(prompts):
                col = cols[i % 2]
                if col.button(p, key=f"prompt_{i}", use_container_width=True):
                    st.session_state.messages.append({"role": "user", "content": p})
                    st.rerun()

        # Chat input
        user_input = st.chat_input("Type your message…")
        if user_input:
            st.session_state.messages.append({"role": "user", "content": user_input})

            with st.spinner("Thinking…"):
                # Build context for the agent
                context = {
                    "roster_size": len(storage.load_nurses()),
                    "today": date.today().isoformat(),
                    "pending_requests": len(pending),
                    "active_schedules": len(active),
                }
                # Check if user wants to generate a schedule
                lower = user_input.lower()
                if any(w in lower for w in ["generate", "schedule", "create schedule", "make schedule"]):
                    # Extract unit from message or default to K5
                    unit = "K5"
                    for word in user_input.split():
                        if word.upper().startswith(("K", "M", "L", "J", "D", "E", "F", "G", "H")):
                            unit = word.upper().rstrip(".,!?")
                            break
                    try:
                        needs = _build_default_needs(unit, date.today() + timedelta(days=7), 4)
                        result, narrative = agent().generate_schedule_from_needs(needs)
                        response = narrative
                        if result and result.coverage_gaps:
                            response += f"\n\n⚠️ **{len(result.coverage_gaps)} coverage gap(s) detected** — see Schedule tab for details."
                    except Exception as e:
                        response = agent().answer_policy_question(user_input, context)
                else:
                    response = agent().answer_policy_question(user_input, context)

            st.session_state.messages.append({"role": "assistant", "content": response})
            st.rerun()

        if st.session_state.messages:
            if st.button("🗑️ Clear conversation", key="clear_chat"):
                st.session_state.messages = []
                st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — SCHEDULE
# ════════════════════════════════════════════════════════════════════════════
with tab_schedule:
    st.header("Schedule Management")

    col_gen, col_view = st.columns([1, 2])

    with col_gen:
        st.subheader("Generate Schedule")
        unit_gen = st.text_input("Unit", value="K5", key="gen_unit")
        start_gen = st.date_input("Start date", value=date.today() + timedelta(days=7), key="gen_start")
        weeks_gen = st.number_input("Weeks", min_value=1, max_value=8, value=4, key="gen_weeks")

        st.markdown("**Daily staffing needs:**")
        day_rn = st.number_input("Day shift RNs", min_value=1, max_value=20, value=3, key="day_rn")
        eve_rn = st.number_input("Evening shift RNs", min_value=0, max_value=20, value=2, key="eve_rn")
        night_rn = st.number_input("Night shift RNs (12hr)", min_value=0, max_value=20, value=2, key="night_rn")
        charge_day = st.checkbox("Charge nurse required on days", value=True, key="charge_day")

        if st.button("⚡ Generate Schedule", type="primary", use_container_width=True, key="btn_gen"):
            if not st.session_state.api_key_set:
                st.error("API key required.")
            else:
                end_gen = start_gen + timedelta(weeks=weeks_gen) - timedelta(days=1)
                needs = _build_custom_needs(unit_gen, start_gen, end_gen, day_rn, eve_rn, night_rn, charge_day)

                with st.spinner(f"Generating {weeks_gen}-week schedule for {unit_gen}…"):
                    result, narrative = agent().generate_schedule_from_needs(needs)

                if result:
                    st.session_state["last_result"] = result
                    st.session_state["last_narrative"] = narrative
                    st.success(f"Schedule generated: {len(result.schedule.assignments)} assignments")
                    if result.coverage_gaps:
                        st.warning(f"{len(result.coverage_gaps)} coverage gap(s)")

        if "last_narrative" in st.session_state:
            with st.expander("📄 Agent Summary", expanded=True):
                st.markdown(st.session_state["last_narrative"])

    with col_view:
        st.subheader("View Schedule")
        all_schedules = storage.load_schedules()

        if not all_schedules:
            st.info("No schedules yet. Generate one on the left.")
        else:
            nurse_map = {n.id: n.name for n in storage.load_nurses()}

            # Schedule selector
            sched_options = {
                f"{s.unit} — {s.start_date} to {s.end_date}": s
                for s in sorted(all_schedules, key=lambda x: x.start_date, reverse=True)
            }
            selected_label = st.selectbox("Select schedule", list(sched_options.keys()), key="sched_select")
            sched = sched_options[selected_label]

            # Filter by date range
            date_filter = st.date_input(
                "Show from / to",
                value=(sched.start_date, min(sched.start_date + timedelta(6), sched.end_date)),
                min_value=sched.start_date,
                max_value=sched.end_date,
                key="sched_date_filter",
            )
            if isinstance(date_filter, (list, tuple)) and len(date_filter) == 2:
                filter_start, filter_end = date_filter
            else:
                filter_start = filter_end = sched.start_date

            assignments = [
                a for a in sched.assignments
                if filter_start <= a.date <= filter_end
            ]

            if assignments:
                import pandas as pd
                rows = []
                for a in sorted(assignments, key=lambda x: (x.date, x.shift_slot.value)):
                    rows.append({
                        "Date": a.date.strftime("%a %b %d"),
                        "Shift": a.shift_slot.value,
                        "Nurse": nurse_map.get(a.nurse_id, a.nurse_id),
                        "Unit": a.unit,
                        "Hours": a.hours,
                        "Over Commit": "⚠️" if a.is_over_commitment else "",
                        "Float": f"← {a.float_from_unit}" if a.is_float else "",
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, height=420)

                # Coverage gap summary
                if "last_result" in st.session_state and st.session_state["last_result"].coverage_gaps:
                    st.markdown("### ⚠️ Coverage Gaps")
                    for g in st.session_state["last_result"].coverage_gaps:
                        st.markdown(
                            f'<span class="gap-warning">• {g["date"]} {g["shift"]} on {g["unit"]}: '
                            f'short by {g["gap"]} {g["required_role"]}(s)</span>',
                            unsafe_allow_html=True,
                        )
            else:
                st.info("No assignments in selected date range.")


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — REQUESTS
# ════════════════════════════════════════════════════════════════════════════
with tab_requests:
    st.header("Schedule Requests")
    req_col, hist_col = st.columns([1, 1])

    nurses = storage.load_nurses()
    nurse_options = {n.name: n.id for n in sorted(nurses, key=lambda x: x.name)}

    with req_col:
        req_type = st.radio(
            "Request type",
            ["PTO / Regular Day Off", "Pre-Approved Vacation", "Pre-Approved Education",
             "Voluntary A-Day", "Shift Swap"],
            key="req_type_radio",
        )

        nurse_name = st.selectbox("Nurse", list(nurse_options.keys()), key="req_nurse")
        nurse_id = nurse_options[nurse_name]
        selected_nurse = next(n for n in nurses if n.id == nurse_id)

        # Show FTE context
        st.caption(
            f"FTE: {selected_nurse.fte} | PTO balance: {selected_nurse.pto_hours_balance:.0f} hrs | "
            f"Seniority: {selected_nurse.seniority_years:.1f} yrs | "
            f"Max vacation: {selected_nurse.max_pre_approved_vacation_weeks} wks"
        )

        if req_type in ("PTO / Regular Day Off", "Pre-Approved Vacation", "Pre-Approved Education"):
            date_range = st.date_input(
                "Date(s)", value=(date.today() + timedelta(14),), key="req_dates"
            )
            dates = (
                [date_range[0] + timedelta(i) for i in range((date_range[1] - date_range[0]).days + 1)]
                if isinstance(date_range, (list, tuple)) and len(date_range) == 2
                else [date_range] if not isinstance(date_range, (list, tuple))
                else [date_range[0]]
            )

            edu_activity = ""
            if req_type == "Pre-Approved Education":
                edu_activity = st.text_input("Education activity name", key="edu_activity")

            type_map = {
                "PTO / Regular Day Off": "pto",
                "Pre-Approved Vacation": "pre_approved_vacation",
                "Pre-Approved Education": "pre_approved_education",
            }

            if st.button("Submit Request", type="primary", key="btn_submit_pto"):
                if not st.session_state.api_key_set:
                    st.error("API key required.")
                else:
                    with st.spinner("Reviewing request against policy…"):
                        decision, message = agent().process_time_off_request(
                            nurse_id=nurse_id,
                            request_type_str=type_map[req_type],
                            dates=dates,
                            education_activity=edu_activity,
                        )
                    status_class = "approved" if decision.approved else "denied"
                    status_text = "APPROVED ✓" if decision.approved else "DENIED ✗"
                    st.markdown(f'<p class="{status_class}">{status_text}</p>', unsafe_allow_html=True)
                    st.info(decision.reason)
                    if decision.policy_references:
                        st.caption(f"Policy: {', '.join(decision.policy_references)}")
                    with st.expander("Message to nurse"):
                        st.write(message)

        elif req_type == "Voluntary A-Day":
            a_date = st.date_input("Requested date", value=date.today() + timedelta(7), key="a_date")
            a_shift = st.selectbox("Shift", ["day", "evening", "night_12", "night_8"], key="a_shift")
            a_unit = st.text_input("Unit", value="K5", key="a_unit")

            if st.button("Request A-Day", type="primary", key="btn_aday"):
                if not st.session_state.api_key_set:
                    st.error("API key required.")
                else:
                    with st.spinner("Processing A-day request…"):
                        decision, message = agent().process_a_day_request(
                            nurse_id=nurse_id,
                            requested_date=a_date,
                            shift_slot=ShiftSlot(a_shift),
                            unit=a_unit,
                        )
                    status_class = "approved" if decision.approved else "denied"
                    status_text = "APPROVED ✓" if decision.approved else "DENIED ✗"
                    st.markdown(f'<p class="{status_class}">{status_text}</p>', unsafe_allow_html=True)
                    st.info(decision.reason)
                    with st.expander("Message to nurse"):
                        st.write(message)

        elif req_type == "Shift Swap":
            other_name = st.selectbox(
                "Swap with",
                [n for n in nurse_options.keys() if n != nurse_name],
                key="swap_with",
            )
            other_id = nurse_options[other_name]
            swap_date = st.date_input("Trade date", value=date.today() + timedelta(7), key="swap_date")
            st.caption("Shift IDs can be found in the Schedule tab (date + nurse + slot).")
            shift_a = st.text_input("Your shift ID (or description)", key="shift_a")
            shift_b = st.text_input(f"{other_name}'s shift ID (or description)", key="shift_b")

            if st.button("Request Swap", type="primary", key="btn_swap"):
                if not st.session_state.api_key_set:
                    st.error("API key required.")
                elif not shift_a or not shift_b:
                    st.warning("Both shift IDs are required.")
                else:
                    with st.spinner("Reviewing swap request…"):
                        decision, message = agent().process_shift_swap(
                            requesting_nurse_id=nurse_id,
                            accepting_nurse_id=other_id,
                            original_shift_id=shift_a,
                            swap_shift_id=shift_b,
                            trade_date=swap_date,
                        )
                    status_class = "approved" if decision.approved else "denied"
                    status_text = "APPROVED ✓" if decision.approved else "DENIED ✗"
                    st.markdown(f'<p class="{status_class}">{status_text}</p>', unsafe_allow_html=True)
                    st.info(decision.reason)
                    with st.expander("Message to nurses"):
                        st.write(message)

    with hist_col:
        st.subheader("Pending Approvals")

        nmap = {n.id: n.name for n in nurses}

        # ── Pending time-off requests ────────────────────────────────────────
        pending_tor = [r for r in storage.load_time_off_requests() if r.status.value == "pending"]
        pending_swaps = [s for s in storage.load_swap_requests() if s.status.value == "pending"]

        if not pending_tor and not pending_swaps:
            st.success("No pending requests — all caught up.")
        else:
            st.caption(f"{len(pending_tor)} time-off · {len(pending_swaps)} swap requests pending")

        all_nurses_list = storage.load_nurses()
        all_tor_list = storage.load_time_off_requests()

        for r in sorted(pending_tor, key=lambda x: x.submitted_at, reverse=True):
            date_str = (
                f"{min(r.dates)} – {max(r.dates)}" if len(r.dates) > 1 else str(r.dates[0])
            )
            nurse_obj = next((n for n in all_nurses_list if n.id == r.nurse_id), None)
            blocks, warnings_list, info_list = (
                _check_request_eligibility(r, nurse_obj, all_tor_list, all_nurses_list)
                if nurse_obj else (["Nurse not found in roster"], [], [])
            )
            eligible = len(blocks) == 0
            badge = "✅ Eligible" if eligible else ("⚠️ Conditional" if not blocks else "❌ Blocked")
            badge_color = "green" if eligible else ("orange" if warnings_list and not blocks else "red")

            label = f"🟡 {nmap.get(r.nurse_id, r.nurse_id)} — {r.request_type.value.replace('_',' ').title()} — {date_str}"
            with st.expander(label, expanded=True):
                st.write(f"**Nurse:** {nmap.get(r.nurse_id, r.nurse_id)}  |  **Type:** {r.request_type.value.replace('_',' ').title()}  |  **Date(s):** {date_str}")
                if r.education_activity:
                    st.write(f"**Activity:** {r.education_activity}")
                st.write(f"**Submitted:** {r.submitted_at.strftime('%Y-%m-%d %H:%M')}")

                st.markdown(f"**Eligibility: :{badge_color}[{badge}]**")

                if blocks:
                    for b in blocks:
                        st.error(f"🚫 {b}")
                if warnings_list:
                    for w in warnings_list:
                        st.warning(f"⚠️ {w}")
                if info_list:
                    with st.expander("Details", expanded=not blocks):
                        for item in info_list:
                            st.markdown(f"- {item}")

                mgr_note = st.text_input(
                    "Manager note (optional)", key=f"note_{r.id}", placeholder="Reason for decision…"
                )
                col_a, col_d = st.columns(2)
                if col_a.button("✅ Approve", key=f"approve_{r.id}", use_container_width=True,
                                type="primary" if eligible else "secondary"):
                    r.status = RequestStatus.APPROVED
                    r.decided_at = datetime.now()
                    r.decision_reason = mgr_note or "Approved by manager."
                    storage.save_time_off_request(r)
                    st.success(f"Approved {nmap.get(r.nurse_id, r.nurse_id)}'s request.")
                    st.rerun()
                if col_d.button("❌ Decline", key=f"decline_{r.id}", use_container_width=True):
                    r.status = RequestStatus.DENIED
                    r.decided_at = datetime.now()
                    r.decision_reason = mgr_note or "Declined by manager."
                    storage.save_time_off_request(r)
                    st.warning(f"Declined {nmap.get(r.nurse_id, r.nurse_id)}'s request.")
                    st.rerun()

        for s in sorted(pending_swaps, key=lambda x: x.submitted_at, reverse=True):
            req_name = nmap.get(s.requesting_nurse_id, s.requesting_nurse_id)
            acc_name = nmap.get(s.accepting_nurse_id, s.accepting_nurse_id) if s.accepting_nurse_id else "TBD"
            s_blocks, s_warnings, s_info = _check_swap_eligibility(s, all_nurses_list, all_tor_list)
            s_eligible = len(s_blocks) == 0
            s_badge = "✅ Eligible" if s_eligible else "❌ Blocked"
            s_color = "green" if s_eligible else "red"

            label = f"🟡 Swap — {req_name} ↔ {acc_name} — {s.trade_date}"
            with st.expander(label, expanded=True):
                st.write(f"**Requesting:** {req_name}  |  **Accepting:** {acc_name}  |  **Trade date:** {s.trade_date}")
                st.write(f"**Shift A:** {s.original_shift_id}  |  **Shift B:** {s.swap_shift_id}")
                if s.notes:
                    st.write(f"**Notes:** {s.notes}")
                st.write(f"**Submitted:** {s.submitted_at.strftime('%Y-%m-%d %H:%M')}")

                st.markdown(f"**Eligibility: :{s_color}[{s_badge}]**")

                if s_blocks:
                    for b in s_blocks:
                        st.error(f"🚫 {b}")
                if s_warnings:
                    for w in s_warnings:
                        st.warning(f"⚠️ {w}")
                if s_info:
                    with st.expander("Details", expanded=not s_blocks):
                        for item in s_info:
                            st.markdown(f"- {item}")

                swap_note = st.text_input(
                    "Manager note (optional)", key=f"swap_note_{s.id}", placeholder="Reason for decision…"
                )
                col_a, col_d = st.columns(2)
                if col_a.button("✅ Approve", key=f"swap_approve_{s.id}", use_container_width=True,
                                type="primary" if s_eligible else "secondary"):
                    s.status = RequestStatus.APPROVED
                    s.manager_approved = True
                    storage.save_swap_request(s)
                    st.success(f"Swap approved: {req_name} ↔ {acc_name}")
                    st.rerun()
                if col_d.button("❌ Decline", key=f"swap_decline_{s.id}", use_container_width=True):
                    s.status = RequestStatus.DENIED
                    s.manager_approved = False
                    storage.save_swap_request(s)
                    st.warning(f"Swap declined: {req_name} ↔ {acc_name}")
                    st.rerun()

        st.divider()
        st.subheader("Request History")
        all_requests = storage.load_time_off_requests()
        filter_nurse = st.selectbox(
            "Filter by nurse", ["All"] + list(nurse_options.keys()), key="hist_filter"
        )
        filter_status = st.selectbox(
            "Filter by status", ["All", "pending", "approved", "denied"], key="hist_status"
        )

        filtered = all_requests
        if filter_nurse != "All":
            filtered = [r for r in filtered if r.nurse_id == nurse_options[filter_nurse]]
        if filter_status != "All":
            filtered = [r for r in filtered if r.status.value == filter_status]
        filtered = sorted(filtered, key=lambda r: r.submitted_at, reverse=True)[:30]

        if not filtered:
            st.info("No requests found.")
        else:
            for r in filtered:
                color = {"approved": "🟢", "denied": "🔴", "pending": "🟡"}.get(r.status.value, "⚪")
                date_str = (
                    f"{min(r.dates)} – {max(r.dates)}" if len(r.dates) > 1 else str(r.dates[0])
                )
                with st.expander(
                    f"{color} {nmap.get(r.nurse_id, r.nurse_id)} — {r.request_type.value} — {date_str}"
                ):
                    st.write(f"**Status:** {r.status.value}")
                    st.write(f"**Submitted:** {r.submitted_at.strftime('%Y-%m-%d %H:%M')}")
                    if r.decision_reason:
                        st.write(f"**Decision:** {r.decision_reason}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — ROSTER
# ════════════════════════════════════════════════════════════════════════════
with tab_roster:
    st.header("Nurse Roster")
    nurses = storage.load_nurses()

    roster_col, detail_col = st.columns([2, 1])

    with roster_col:
        import pandas as pd
        rows = []
        for n in sorted(nurses, key=lambda x: x.seniority_years, reverse=True):
            rows.append({
                "ID": n.id,
                "Name": n.name,
                "Role": n.role.value,
                "Type": n.employee_type.value,
                "FTE": n.fte,
                "Shift": n.shift_type.value,
                "Seniority": f"{n.seniority_years:.1f} yr",
                "Float Regions": ", ".join(n.float_regions) or "—",
                "PTO Hrs": n.pto_hours_balance,
                "Points": n.gamification_points,
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=380)

    with detail_col:
        st.subheader("Add Nurse")
        with st.form("add_nurse_form"):
            new_id = st.text_input("ID (e.g. N011)")
            new_name = st.text_input("Full name")
            new_fte = st.slider("FTE", 0.5, 1.0, 1.0, step=0.1)
            new_shift = st.selectbox("Shift type", ["12hr", "8hr"])
            new_slot = st.selectbox("Shift slot", ["day", "evening", "night_12", "night_8"])
            new_role = st.selectbox("Role", ["rn", "charge", "resource", "na", "us"])
            new_type = st.selectbox("Employee type", ["regular", "relief", "traveler", "registry"])
            new_hire = st.date_input("Hire date", value=date.today())
            new_regions = st.text_input("Float regions (comma-separated)", "K5,K6")
            new_specs = st.text_input("Specialties (comma-separated)", "")
            new_charge = st.checkbox("Can serve as charge nurse")
            new_pto = st.number_input("Initial PTO balance (hrs)", value=80.0)

            submitted = st.form_submit_button("Add to Roster", type="primary")
            if submitted:
                if not new_id or not new_name:
                    st.error("ID and name are required.")
                elif storage.get_nurse(new_id):
                    st.error(f"Nurse {new_id} already exists.")
                else:
                    from nursing_agent.models import NurseRole
                    new_nurse = Nurse(
                        id=new_id, name=new_name, fte=new_fte,
                        shift_type=ShiftType(new_shift),
                        shift_slot=ShiftSlot(new_slot),
                        role=NurseRole(new_role),
                        employee_type=EmployeeType(new_type),
                        hire_date=new_hire,
                        float_regions=[r.strip() for r in new_regions.split(",") if r.strip()],
                        specialties=[s.strip() for s in new_specs.split(",") if s.strip()],
                        can_charge=new_charge,
                        pto_hours_balance=new_pto,
                    )
                    storage.upsert_nurse(new_nurse)
                    st.success(f"Added {new_name} to roster!")
                    st.rerun()

        st.divider()
        st.subheader("Float / Cancellation Order")
        st.caption("Who goes first if unit is overstaffed or short-staffed today")
        if nurses:
            st.markdown("**Cancel first (overstaffed):**")
            for i, n in enumerate(cancellation_order(nurses)[:5], 1):
                st.markdown(f"{i}. {n.name} ({n.employee_type.value})")
            st.markdown("**Float first (understaffed):**")
            for i, n in enumerate(float_order(nurses, "K5")[:5], 1):
                st.markdown(f"{i}. {n.name} ({n.employee_type.value})")


# ════════════════════════════════════════════════════════════════════════════
# TAB 5 — GAMIFICATION
# ════════════════════════════════════════════════════════════════════════════
with tab_gamification:
    st.header("Gamification & Recognition")

    gam_col, record_col = st.columns([2, 1])

    with gam_col:
        # Leaderboard
        cat = st.selectbox(
            "Leaderboard category",
            ["total_points", "no_call_outs", "shifts_picked_up", "swaps_completed", "avg_shift_rating"],
            format_func=lambda x: x.replace("_", " ").title(),
            key="lb_cat",
        )

        if nurses:
            board = agent().get_leaderboard(cat, top_n=10)
            medals = ["🥇", "🥈", "🥉"]
            for entry in board:
                r = entry["rank"]
                medal = medals[r - 1] if r <= 3 else f"#{r}"
                metric_keys = [k for k in entry.keys() if k not in ("rank", "name")]
                vals = " · ".join(f"{k.replace('_',' ').title()}: **{entry[k]}**" for k in metric_keys)
                st.markdown(f"{medal} **{entry['name']}** — {vals}")

        st.divider()
        st.subheader("Individual Profile")
        profile_nurse = st.selectbox(
            "Select nurse", list(nurse_options.keys()), key="profile_nurse"
        )
        profile_id = nurse_options[profile_nurse]
        profile = agent().get_nurse_gamification_profile(profile_id)

        if profile:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Points", profile["total_points"])
            c2.metric("Streak", f"{profile['current_streak_days']} days")
            c3.metric("No Call-Outs", profile["no_call_outs"])
            c4.metric("Avg Rating", f"{profile['avg_shift_rating']} ⭐")

            c5, c6 = st.columns(2)
            c5.metric("Pickups", profile["shifts_picked_up"])
            c6.metric("Swaps", profile["swaps_completed"])

            if profile["badges"]:
                st.markdown("**Badges earned:**")
                badge_html = " ".join(
                    f'<span class="badge">{b["icon"]} {b["name"]}</span>'
                    for b in profile["badges"]
                )
                st.markdown(badge_html, unsafe_allow_html=True)
            else:
                st.caption("No badges yet — keep going!")

            if profile["recent_events"]:
                st.markdown("**Recent activity:**")
                for ev in profile["recent_events"]:
                    st.markdown(f"• {ev['desc']} `+{ev['pts']} pts`")

    with record_col:
        st.subheader("Record Event")
        event_nurse = st.selectbox("Nurse", list(nurse_options.keys()), key="event_nurse")
        event_nurse_id = nurse_options[event_nurse]

        event_type = st.selectbox("Event", [
            "no_call_out", "on_time", "shift_pickup",
            "shift_pickup_short_notice", "swap_completed", "volunteer_float",
        ], format_func=lambda x: x.replace("_", " ").title(), key="event_type")

        event_date = st.date_input("Date", value=date.today(), key="event_date")
        extra = ""
        if "float" in event_type:
            extra = st.text_input("Float to unit", value="K6", key="float_unit")
        if "swap" in event_type:
            extra = st.text_input("Swapped with", key="swap_with_name")

        if st.button("Record Event", type="primary", key="btn_record_event"):
            from nursing_agent.gamification import GamificationEngine
            gam_engine = GamificationEngine()
            ev_nurses = storage.load_nurses()
            ev_nurse = next((n for n in ev_nurses if n.id == event_nurse_id), None)
            if ev_nurse:
                ev = None
                if event_type == "no_call_out":
                    ev = gam_engine.award_no_call_out(ev_nurse, event_date)
                elif event_type == "on_time":
                    ev = gam_engine.award_on_time(ev_nurse, event_date)
                elif event_type == "shift_pickup":
                    ev = gam_engine.award_shift_pickup(ev_nurse, event_date, ShiftSlot.DAY)
                elif event_type == "shift_pickup_short_notice":
                    ev = gam_engine.award_shift_pickup(ev_nurse, event_date, ShiftSlot.DAY, is_short_notice=True)
                elif event_type == "swap_completed":
                    ev = gam_engine.award_swap_completed(ev_nurse, event_date, extra or "a colleague")
                elif event_type == "volunteer_float":
                    ev = gam_engine.award_volunteer_float(ev_nurse, event_date, extra or "another unit")

                if ev:
                    storage.save_gamification_event(ev)
                    storage.upsert_nurse(ev_nurse)
                    st.success(f"+{ev.points_awarded} pts — {ev.description}")

        st.divider()
        st.subheader("Rate a Shift")
        rate_nurse = st.selectbox("Nurse", list(nurse_options.keys()), key="rate_nurse")
        rate_nurse_id = nurse_options[rate_nurse]
        rate_date = st.date_input("Shift date", value=date.today(), key="rate_date")
        rate_shift = st.selectbox("Shift", ["day", "evening", "night_12", "night_8"], key="rate_shift")
        rate_unit = st.text_input("Unit", value="K5", key="rate_unit")
        rate_stars = st.slider("Rating", 0, 5, 4, key="rate_stars")
        rate_comment = st.text_area("Comments (optional)", key="rate_comment", height=80)

        if st.button("Submit Rating", key="btn_rate"):
            agent().record_shift_rating(
                nurse_id=rate_nurse_id,
                shift_date=rate_date,
                shift_slot=ShiftSlot(rate_shift),
                unit=rate_unit,
                rating=rate_stars,
                comments=rate_comment,
            )
            st.success(f"{'⭐' * rate_stars} rating recorded for {rate_nurse}!")




# ════════════════════════════════════════════════════════════════════════════
# TAB 6 — ICU SCHEDULE DASHBOARD
# ════════════════════════════════════════════════════════════════════════════
with tab_icu:
    import plotly.express as px
    import calendar as _cal
    from collections import defaultdict
    from datetime import datetime as _dt

    st.markdown("""
    <style>
    .fc-unit-bar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
    .fc-unit-btn{padding:6px 18px;border-radius:100px;border:1.5px solid #E2E8F0;
        font-size:13px;font-weight:600;cursor:pointer;background:#fff;color:#64748B;
        transition:all .15s}
    .fc-unit-btn.active{background:#0F172A;color:#fff;border-color:#0F172A}
    .fc-hdr{background:#0F172A;border-radius:12px;padding:16px 24px;
        display:flex;justify-content:space-between;align-items:center;
        flex-wrap:wrap;gap:12px;margin-bottom:16px}
    .fc-hdr-left{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    .fc-org{font-size:15px;font-weight:700;color:#F1F5F9}
    .fc-hdot{color:#475569;font-size:16px}
    .fc-hmeta{font-size:13px;color:#94A3B8}
    .fc-hdr-right{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    .fc-shift-chip{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);
        color:#CBD5E1;font-size:12px;font-weight:500;padding:4px 10px;border-radius:6px}
    .fc-online{background:#DCFCE7;color:#166534;font-size:12px;font-weight:600;
        padding:4px 12px;border-radius:100px}
    .fc-mcard{background:#fff;border:1px solid #E2E8F0;border-radius:10px;
        padding:18px 20px;height:100%}
    .fc-mcard-label{font-size:11px;font-weight:700;letter-spacing:.08em;
        text-transform:uppercase;color:#94A3B8;margin-bottom:6px}
    .fc-mcard-val{font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-1px;
        line-height:1.1}
    .fc-mcard-sub{font-size:12px;color:#64748B;margin-top:4px;line-height:1.4}
    .fc-mcard-badge{display:inline-block;background:#DCFCE7;color:#166534;
        font-size:11px;font-weight:700;padding:2px 8px;border-radius:100px;margin-top:6px}
    .fc-stat-box{background:#F8FAFC;border-radius:8px;padding:14px 16px;text-align:center}
    .fc-stat-val{font-size:28px;font-weight:800;color:#0F172A;letter-spacing:-1px}
    .fc-stat-label{font-size:12px;color:#64748B;margin-top:2px}
    .fc-stat-sub{font-size:11px;color:#94A3B8;margin-top:2px}
    .fc-cal-full{width:100%;border-collapse:collapse;font-family:'Inter',sans-serif;
        table-layout:fixed;font-size:13px}
    .fc-cal-full th{background:#0F172A;color:#fff;padding:10px 6px;text-align:center;
        font-weight:700;font-size:13px}
    .fc-cal-full td{border:1px solid #E2E8F0;padding:8px;vertical-align:top;
        height:120px;width:14.28%}
    .fc-cal-full td.out{background:#F8FAFC;opacity:.5}
    .fc-dn{font-weight:800;color:#334155;font-size:15px;margin-bottom:4px}
    .fc-dn.today{color:#2563EB}
    .fc-wk{font-size:10px;font-weight:700;letter-spacing:.08em;color:#94A3B8;
        text-transform:uppercase;margin-bottom:4px}
    .fc-cnt{font-size:18px;font-weight:800;letter-spacing:-0.5px;margin:4px 0 2px}
    .fc-cnt.full{color:#16A34A}
    .fc-cnt.warn{color:#D97706}
    .fc-cnt.gap{color:#DC2626}
    .s-day-f{background:#DBEAFE;color:#1E40AF;border-radius:3px;padding:2px 5px;
        margin:1px 0;display:block;font-size:11px;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
    .s-eve-f{background:#FEF3C7;color:#92400E;border-radius:3px;padding:2px 5px;
        margin:1px 0;display:block;font-size:11px;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
    .s-ngt-f{background:#EDE9FE;color:#4C1D95;border-radius:3px;padding:2px 5px;
        margin:1px 0;display:block;font-size:11px;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
    </style>
    """, unsafe_allow_html=True)

    active_unit = "ICU"

    # ── Derive metrics ────────────────────────────────────────────────────────
    all_nurses_icu = storage.load_nurses()
    icu_result_d  = st.session_state.get("icu_result")
    needs_obj_d   = st.session_state.get("icu_needs_obj")

    total_n       = len(all_nurses_icu)
    charge_n      = sum(1 for n in all_nurses_icu if n.can_charge)
    schedule_generated = icu_result_d is not None and needs_obj_d is not None

    if schedule_generated:
        all_asgn      = icu_result_d.schedule.assignments
        total_slots   = len(all_asgn)
        gaps          = icu_result_d.coverage_gaps or []
        gap_count     = len(gaps)
        charge_gaps   = sum(1 for g in gaps if "charge" in g.get("required_role","").lower())
        filled_pct    = round((total_slots / max(total_slots, 1)) * 100)
        period_label  = f"{needs_obj_d.schedule_start.strftime('%b %d')} – {needs_obj_d.schedule_end.strftime('%b %d, %Y')}"
        schedule_id   = needs_obj_d.schedule_start.strftime("%Y-%m") + " schedule"
        weeks_count   = max(1, round((needs_obj_d.schedule_end - needs_obj_d.schedule_start).days / 7))
    else:
        total_slots = gaps = gap_count = charge_gaps = 0
        filled_pct  = 0
        period_label = "Not generated"
        schedule_id  = date.today().strftime("%Y-%m") + " schedule"
        weeks_count  = 4

    # ── Header info bar ───────────────────────────────────────────────────────
    status_html = '<span class="fc-online">● Manager Online · {}% filled</span>'.format(
        filled_pct if schedule_generated else "—"
    )
    st.markdown(f"""
    <div class="fc-hdr">
      <div class="fc-hdr-left">
        <span class="fc-org">Stanford Health Care · {active_unit}</span>
        <span class="fc-hdot">·</span>
        <span class="fc-hmeta">{total_n} nurses</span>
        <span class="fc-hdot">·</span>
        <span class="fc-hmeta">target 14–16/shift</span>
        <span class="fc-hdot">·</span>
        <span class="fc-hmeta">12h shifts</span>
      </div>
      <div class="fc-hdr-right">
        <span class="fc-shift-chip">Day 0700–1900</span>
        <span class="fc-shift-chip">Night 1900–0700</span>
        {status_html}
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 4 metric cards ────────────────────────────────────────────────────────
    mc1, mc2, mc3, mc4 = st.columns(4)
    filled_str = f"{total_slots} of {total_slots}" if schedule_generated else "—"
    mc1.markdown(f"""<div class="fc-mcard">
      <div class="fc-mcard-label">Coverage</div>
      <div class="fc-mcard-val">{filled_str}</div>
      <div class="fc-mcard-sub">shift-slots filled</div>
      <span class="fc-mcard-badge">{'100% filled' if schedule_generated and gap_count==0 else f'{gap_count} gap(s)'}</span>
    </div>""", unsafe_allow_html=True)

    mc2.markdown(f"""<div class="fc-mcard">
      <div class="fc-mcard-label">Staffing Composition</div>
      <div class="fc-mcard-val">12–14</div>
      <div class="fc-mcard-sub">Bed RNs per shift<br>1 Charge (RSN1) · 1 Resource (RSN2)</div>
    </div>""", unsafe_allow_html=True)

    mc3.markdown(f"""<div class="fc-mcard">
      <div class="fc-mcard-label">Shift Structure</div>
      <div class="fc-mcard-val">12h</div>
      <div class="fc-mcard-sub">Day 0700–1900 · Night 1900–0700<br>~30 min handoff overlap</div>
    </div>""", unsafe_allow_html=True)

    mc4.markdown(f"""<div class="fc-mcard">
      <div class="fc-mcard-label">Schedule Period</div>
      <div class="fc-mcard-val">{weeks_count}wk</div>
      <div class="fc-mcard-sub">{period_label}</div>
    </div>""", unsafe_allow_html=True)

    st.markdown("<div style='margin-top:4px'></div>", unsafe_allow_html=True)

    # ── Stats row + schedule controls ─────────────────────────────────────────
    stats_col, gen_col = st.columns([3, 1])
    with stats_col:
        s1, s2, s3, s4 = st.columns(4)
        s1.markdown(f"""<div class="fc-stat-box">
          <div class="fc-stat-val">{total_n}</div>
          <div class="fc-stat-label">Nurses</div>
          <div class="fc-stat-sub">{charge_n} charge-eligible</div>
        </div>""", unsafe_allow_html=True)
        shifts_n = total_slots if schedule_generated else 0
        s2.markdown(f"""<div class="fc-stat-box">
          <div class="fc-stat-val">{shifts_n}</div>
          <div class="fc-stat-label">Shifts</div>
          <div class="fc-stat-sub">targeting 14–16/shift</div>
        </div>""", unsafe_allow_html=True)
        cov_str = f"{total_slots}/{total_slots}" if schedule_generated else "—"
        s3.markdown(f"""<div class="fc-stat-box">
          <div class="fc-stat-val">{cov_str}</div>
          <div class="fc-stat-label">Coverage</div>
          <div class="fc-stat-sub">{'100% filled' if schedule_generated else 'not generated'}</div>
        </div>""", unsafe_allow_html=True)
        s4.markdown(f"""<div class="fc-stat-box">
          <div class="fc-stat-val">{gap_count}</div>
          <div class="fc-stat-label">Gaps</div>
          <div class="fc-stat-sub">{charge_gaps} charge gaps</div>
        </div>""", unsafe_allow_html=True)

    with gen_col:
        if schedule_generated:
            if st.button("🔄 Regenerate", use_container_width=True, key="fc_regen"):
                for k in ["icu_result", "icu_narrative", "icu_needs_obj"]:
                    st.session_state.pop(k, None)
                st.rerun()
            st.button("📤 Publish Schedule", use_container_width=True,
                      type="primary", key="fc_publish")

    st.divider()


    SHIFT_COLORS = {
        "day": "#3B82F6", "evening": "#F59E0B",
        "night_12": "#6366F1", "night_8": "#8B5CF6",
    }
    SHIFT_LABELS = {
        "day": "Day  0700–1900", "evening": "Eve  1500–0300",
        "night_12": "Night  1900–0700", "night_8": "Night  2245–0715",
    }
    nurse_map_d = {n.id: n.name for n in all_nurses_icu}

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 1 — SCHEDULE BUILDER
    # ════════════════════════════════════════════════════════════════════════
    with st.expander("⚙️ ICU Operational Needs", expanded=not schedule_generated):
        icu_unit = st.text_input(
            "ICU Unit", value="ICU",
            help="e.g. ICU, CVICU, MICU, SICU", key="icu_unit",
        )
        bc1, bc2, bc3 = st.columns(3)
        icu_start = bc1.date_input(
            "Start date", value=date.today() + timedelta(days=7), key="icu_start"
        )
        icu_weeks = bc2.number_input(
            "Weeks", min_value=1, max_value=8, value=4, key="icu_weeks"
        )
        icu_bed_cap = bc3.number_input(
            "Bed capacity", min_value=4, max_value=60, value=20, key="icu_bed_cap"
        )

        st.markdown("**Census & Acuity**")
        st.caption("Critical = 1:1 RN ratio · Stable = 1:2 RN ratio")
        census_mode = st.radio(
            "Census mode",
            ["Uniform (all shifts)", "By shift type"],
            horizontal=True, key="icu_census_mode",
        )

        def _cb(suffix, label):
            with st.expander(label, expanded=True):
                a, b = st.columns(2)
                cr = a.number_input("Critical (1:1)", 0, 30, 4, key=f"icu_crit_{suffix}")
                st = b.number_input("Stable (1:2)",   0, 30, 8, key=f"icu_stbl_{suffix}")
                c, d = st.columns(2) if False else (a, b)
                vt = a.number_input("Vent",  0, 30, 2, key=f"icu_vent_{suffix}")
                ec = b.number_input("ECMO",  0, 10, 0, key=f"icu_ecmo_{suffix}")
            return cr, st, vt, ec

        import math as _math

        if census_mode.startswith("Uniform"):
            with st.expander("All Shifts", expanded=True):
                ca1, ca2 = st.columns(2)
                day_crit = ca1.number_input("Critical (1:1)", 0, 30, 4, key="icu_crit_all")
                day_stbl = ca2.number_input("Stable (1:2)",   0, 30, 8, key="icu_stbl_all")
                ca3, ca4 = st.columns(2)
                day_vent = ca3.number_input("Vent", 0, 30, 2, key="icu_vent_all")
                day_ecmo = ca4.number_input("ECMO", 0, 10, 0, key="icu_ecmo_all")
            eve_crit, eve_stbl, eve_vent, eve_ecmo = day_crit, day_stbl, day_vent, day_ecmo
            ngt_crit, ngt_stbl, ngt_vent, ngt_ecmo = day_crit, day_stbl, day_vent, day_ecmo
        else:
            with st.expander("Day Shift", expanded=True):
                da1, da2 = st.columns(2)
                day_crit = da1.number_input("Critical", 0, 30, 4, key="icu_crit_day")
                day_stbl = da2.number_input("Stable",   0, 30, 8, key="icu_stbl_day")
                da3, da4 = st.columns(2)
                day_vent = da3.number_input("Vent", 0, 30, 2, key="icu_vent_day")
                day_ecmo = da4.number_input("ECMO", 0, 10, 0, key="icu_ecmo_day")
            with st.expander("Evening Shift", expanded=True):
                ea1, ea2 = st.columns(2)
                eve_crit = ea1.number_input("Critical", 0, 30, 4, key="icu_crit_eve")
                eve_stbl = ea2.number_input("Stable",   0, 30, 8, key="icu_stbl_eve")
                ea3, ea4 = st.columns(2)
                eve_vent = ea3.number_input("Vent", 0, 30, 2, key="icu_vent_eve")
                eve_ecmo = ea4.number_input("ECMO", 0, 10, 0, key="icu_ecmo_eve")
            with st.expander("Night Shift", expanded=True):
                na1, na2 = st.columns(2)
                ngt_crit = na1.number_input("Critical", 0, 30, 4, key="icu_crit_ngt")
                ngt_stbl = na2.number_input("Stable",   0, 30, 8, key="icu_stbl_ngt")
                na3, na4 = st.columns(2)
                ngt_vent = na3.number_input("Vent", 0, 30, 2, key="icu_vent_ngt")
                ngt_ecmo = na4.number_input("ECMO", 0, 10, 0, key="icu_ecmo_ngt")

        day_rns = day_crit + _math.ceil(day_stbl / 2) if (day_crit + day_stbl) > 0 else 0
        eve_rns = eve_crit + _math.ceil(eve_stbl / 2) if (eve_crit + eve_stbl) > 0 else 0
        ngt_rns = ngt_crit + _math.ceil(ngt_stbl / 2) if (ngt_crit + ngt_stbl) > 0 else 0

        rn1, rn2, rn3 = st.columns(3)
        rn1.metric("Day RNs needed",   day_rns)
        rn2.metric("Evening RNs needed", eve_rns)
        rn3.metric("Night RNs needed", ngt_rns)

        xc1, xc2 = st.columns(2)
        icu_charge  = xc1.checkbox("Charge nurse (day shift)", True, key="icu_charge")
        icu_resource= xc2.checkbox("Resource nurse",          False, key="icu_resource")
        xs1, xs2, xs3 = st.columns(3)
        icu_vent_spec = xs1.checkbox("Vent specialist", False, key="icu_vent_spec")
        icu_ecmo_spec = xs2.checkbox("ECMO capable",   False, key="icu_ecmo_spec")
        icu_crrt_spec = xs3.checkbox("CRRT capable",   False,              key="icu_crrt_spec")
        icu_notes = st.text_area("Clinical notes", key="icu_notes", height=56,
            placeholder="e.g. post-cardiac surgery surge, 2 ECMO patients expected")

        if st.button("⚡ Generate ICU Schedule", type="primary",
                     use_container_width=True, key="btn_icu_gen"):
            if not st.session_state.api_key_set:
                st.error("API key required.")
            elif day_rns == 0 and eve_rns == 0 and ngt_rns == 0:
                st.warning("Enter census data above.")
            else:
                icu_end = icu_start + timedelta(weeks=int(icu_weeks)) - timedelta(days=1)
                census_entries = []
                cur = icu_start
                while cur <= icu_end:
                    census_entries.extend([
                        ICUShiftCensus(date=cur, shift_slot=ShiftSlot.DAY,
                            critical_patients=day_crit, stable_patients=day_stbl,
                            vent_patients=day_vent, ecmo_patients=day_ecmo),
                        ICUShiftCensus(date=cur, shift_slot=ShiftSlot.EVENING,
                            critical_patients=eve_crit, stable_patients=eve_stbl,
                            vent_patients=eve_vent, ecmo_patients=eve_ecmo),
                        ICUShiftCensus(date=cur, shift_slot=ShiftSlot.NIGHT_12,
                            critical_patients=ngt_crit, stable_patients=ngt_stbl,
                            vent_patients=ngt_vent, ecmo_patients=ngt_ecmo),
                    ])
                    cur += timedelta(days=1)
                icu_needs = ICUOperationalNeeds(
                    unit=icu_unit, bed_capacity=int(icu_bed_cap),
                    schedule_start=icu_start, schedule_end=icu_end,
                    census_entries=census_entries,
                    charge_each_shift=icu_charge, resource_nurse_needed=icu_resource,
                    vent_specialist_needed=icu_vent_spec, ecmo_capable_needed=icu_ecmo_spec,
                    crrt_capable_needed=icu_crrt_spec, notes=icu_notes,
                )
                dept_needs = _icu_needs_to_department_needs(icu_needs)
                with st.spinner(f"Generating {int(icu_weeks)}-week ICU schedule…"):
                    result, narrative = agent().generate_schedule_from_needs(dept_needs)
                if result:
                    st.session_state["icu_result"]    = result
                    st.session_state["icu_narrative"] = narrative
                    st.session_state["icu_needs_obj"] = icu_needs
                    st.success(f"Generated {len(result.schedule.assignments)} assignments")
                    st.rerun()

    if "icu_narrative" in st.session_state:
        with st.expander("📄 Agent Summary"):
            st.markdown(st.session_state["icu_narrative"])

    # ── Calendar view ─────────────────────────────────────────────────
    if schedule_generated:
        st.markdown("---")
        ctrl_l, _ = st.columns([2, 4])
        cal_view = ctrl_l.radio(
            "Calendar view", ["Monthly", "Weekly", "Daily"],
            horizontal=True, key="icu_cal_view",
        )

        all_assigns = icu_result_d.schedule.assignments

        # ── Monthly ──────────────────────────────────────────────────
        if cal_view == "Monthly":
            months_ls, seen_m = [], set()
            cur_m = date(needs_obj_d.schedule_start.year,
                         needs_obj_d.schedule_start.month, 1)
            while cur_m <= needs_obj_d.schedule_end:
                if cur_m not in seen_m:
                    months_ls.append(cur_m)
                    seen_m.add(cur_m)
                cur_m = (cur_m.replace(day=28) + timedelta(days=4)).replace(day=1)

            if len(months_ls) > 1:
                pm, _ = st.columns([1, 4])
                sel_month = pm.selectbox("Month", months_ls,
                    format_func=lambda m: m.strftime("%B %Y"), key="icu_cal_month")
            else:
                sel_month = months_ls[0]

            by_date_m: dict = defaultdict(lambda: defaultdict(list))
            target_by_date: dict = {}
            for a in all_assigns:
                by_date_m[a.date][a.shift_slot.value].append(
                    nurse_map_d.get(a.nurse_id, a.nurse_id))
            for e in needs_obj_d.census_entries:
                if e.shift_slot == ShiftSlot.DAY:
                    target_by_date[e.date] = e.required_bedside_rns

            weeks_grid = _cal.monthcalendar(sel_month.year, sel_month.month)
            today_d = date.today()

            wk_num = 0
            html = f"""
<h3 style="margin:0 0 14px;font-size:22px;font-weight:800;color:#0F172A">
{sel_month.strftime('%B %Y')}</h3>
<table class="fc-cal-full">
<tr><th>Sun</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th></tr>"""

            # Reorder weeks to Sun-Sat
            import calendar as _cal2
            _cal2.setfirstweekday(6)
            weeks_grid2 = _cal2.monthcalendar(sel_month.year, sel_month.month)

            for week in weeks_grid2:
                has_sched = any(
                    date(sel_month.year, sel_month.month, dn) in target_by_date
                    for dn in week if dn > 0
                )
                if has_sched:
                    wk_num += 1
                html += "<tr>"
                for dn in week:
                    if dn == 0:
                        html += '<td class="out"></td>'
                    else:
                        d = date(sel_month.year, sel_month.month, dn)
                        in_r = needs_obj_d.schedule_start <= d <= needs_obj_d.schedule_end
                        t_cls = " today" if d == today_d else ""
                        html += f'<td><div class="fc-dn{t_cls}">{dn}</div>'
                        if in_r and has_sched:
                            html += f'<div class="fc-wk">W{wk_num}</div>'
                        if in_r:
                            day_count = len(by_date_m[d].get("day", []))
                            ngt_count = len(by_date_m[d].get("night_12", []))
                            tgt = target_by_date.get(d, day_rns)
                            day_cls = "full" if day_count >= tgt else ("warn" if day_count >= tgt - 1 else "gap")
                            ngt_cls = "full" if ngt_count >= tgt else ("warn" if ngt_count >= tgt - 1 else "gap")
                            html += f'<div class="fc-cnt {day_cls}">☀ {day_count}/{tgt}</div>'
                            html += f'<div class="fc-cnt {ngt_cls}">★ {ngt_count}/{tgt}</div>'
                            for nm in by_date_m[d].get("day", [])[:2]:
                                html += f'<span class="s-day-f">{nm}</span>'
                            extra = len(by_date_m[d].get("day", [])) - 2
                            if extra > 0:
                                html += f'<span class="s-day-f">+{extra} more</span>'
                        html += "</td>"
                html += "</tr>"

            html += """</table>
<div style="margin-top:12px;display:flex;gap:14px;font-size:12px;align-items:center">
  <span style="background:#DBEAFE;color:#1E40AF;padding:3px 10px;border-radius:4px;font-weight:600">☀ Day 0700–1900</span>
  <span style="background:#EDE9FE;color:#4C1D95;padding:3px 10px;border-radius:4px;font-weight:600">★ Night 1900–0700</span>
  <span style="color:#16A34A;font-weight:700">● Filled</span>
  <span style="color:#D97706;font-weight:700">● −1 from target</span>
  <span style="color:#DC2626;font-weight:700">● Gap</span>
</div>"""
            st.markdown(html, unsafe_allow_html=True)

        # ── Weekly ───────────────────────────────────────────────────
        elif cal_view == "Weekly":
            seen_w, week_starts = set(), []
            for d in sorted({a.date for a in all_assigns}):
                ws = d - timedelta(days=d.weekday())
                if ws not in seen_w:
                    week_starts.append(ws)
                    seen_w.add(ws)
            pw, _ = st.columns([1, 3])
            sel_week = pw.selectbox("Week", week_starts,
                format_func=lambda w: f"Week of {w.strftime('%b %d, %Y')}",
                key="icu_cal_week")
            week_days  = [sel_week + timedelta(days=i) for i in range(7)]
            week_asgn  = [a for a in all_assigns if sel_week <= a.date <= sel_week + timedelta(days=6)]
            shift_order = ["day", "evening", "night_12"]

            pivot = []
            for slot in shift_order:
                row = {"Shift": SHIFT_LABELS.get(slot, slot)}
                for d in week_days:
                    names = [nurse_map_d.get(a.nurse_id, a.nurse_id)
                             for a in week_asgn if a.date == d and a.shift_slot.value == slot]
                    row[d.strftime("%a %-d")] = ", ".join(names) if names else "—"
                pivot.append(row)
            st.dataframe(pd.DataFrame(pivot).set_index("Shift"),
                         use_container_width=True, height=160)

            dcols = st.columns(7)
            bg_map  = {"day": "#DBEAFE", "evening": "#FEF3C7", "night_12": "#EDE9FE"}
            fg_map  = {"day": "#1E40AF", "evening": "#92400E", "night_12": "#4C1D95"}
            ico_map = {"day": "☀", "evening": "🌙", "night_12": "★"}
            for i, d in enumerate(week_days):
                day_a = [a for a in week_asgn if a.date == d]
                with dcols[i]:
                    st.markdown(
                        f"<div style='text-align:center;background:#0F172A;color:#fff;"
                        f"border-radius:8px;padding:6px 2px;margin-bottom:6px'>"
                        f"<b style='font-size:11px'>{d.strftime('%A')}</b><br>"
                        f"<span style='font-size:20px;font-weight:900'>{d.strftime('%-d')}</span>"
                        f"</div>", unsafe_allow_html=True)
                    for slot in shift_order:
                        nurses_on = [nurse_map_d.get(a.nurse_id, a.nurse_id)
                                     for a in day_a if a.shift_slot.value == slot]
                        tgt = day_rns if slot == "day" else (eve_rns if slot == "evening" else ngt_rns)
                        cnt_color = "#16A34A" if len(nurses_on) >= tgt else "#DC2626"
                        st.markdown(
                            f"<div style='font-size:11px;font-weight:700;color:{cnt_color};"
                            f"margin:3px 0 1px'>{ico_map[slot]} {len(nurses_on)}/{tgt}</div>",
                            unsafe_allow_html=True)
                        for nm in nurses_on:
                            st.markdown(
                                f"<div style='background:{bg_map[slot]};color:{fg_map[slot]};"
                                f"border-radius:3px;padding:2px 5px;margin:1px 0;font-size:11px;"
                                f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>"
                                f"{nm}</div>", unsafe_allow_html=True)

        # ── Daily ────────────────────────────────────────────────────
        elif cal_view == "Daily":
            all_sched_dates = sorted({a.date for a in all_assigns})
            pd_col, _ = st.columns([1, 4])
            sel_day = pd_col.date_input("Date",
                value=all_sched_dates[0] if all_sched_dates else needs_obj_d.schedule_start,
                min_value=needs_obj_d.schedule_start,
                max_value=needs_obj_d.schedule_end,
                key="icu_cal_day")
            day_asgn = [a for a in all_assigns if a.date == sel_day]
            if not day_asgn:
                st.info(f"No assignments on {sel_day}.")
            else:
                gantt = []
                for a in sorted(day_asgn, key=lambda x: x.shift_slot.value):
                    s_t, e_t, hrs = SHIFT_TIMES[a.shift_slot]
                    gantt.append({
                        "Nurse": nurse_map_d.get(a.nurse_id, a.nurse_id),
                        "Start": _dt.combine(sel_day, s_t),
                        "Finish": _dt.combine(
                            sel_day + timedelta(days=1) if e_t < s_t else sel_day, e_t),
                        "Shift": SHIFT_LABELS.get(a.shift_slot.value, a.shift_slot.value),
                        "Hours": hrs,
                        "Over Commit": "Yes" if a.is_over_commitment else "No",
                    })
                fig = px.timeline(pd.DataFrame(gantt),
                    x_start="Start", x_end="Finish", y="Nurse", color="Shift",
                    color_discrete_map={SHIFT_LABELS[k]: v for k, v in SHIFT_COLORS.items()
                                        if k in SHIFT_LABELS},
                    hover_data=["Hours", "Over Commit"],
                    title=sel_day.strftime("%A, %B %-d %Y"))
                fig.update_yaxes(autorange="reversed")
                fig.update_layout(height=max(420, len(gantt) * 48 + 140),
                    font=dict(size=14), margin=dict(l=10, r=10, t=50, b=10))
                st.plotly_chart(fig, use_container_width=True)

                tbl = [{"Nurse": nurse_map_d.get(a.nurse_id, a.nurse_id),
                        "Shift": SHIFT_LABELS.get(a.shift_slot.value, a.shift_slot.value),
                        "Hrs": SHIFT_TIMES[a.shift_slot][2],
                        "Float": f"← {a.float_from_unit}" if a.is_float else "",
                        "Over Commit": "⚠️" if a.is_over_commitment else ""}
                       for a in sorted(day_asgn, key=lambda x: x.shift_slot.value)]
                st.dataframe(pd.DataFrame(tbl), use_container_width=True,
                             hide_index=True, height=min(500, len(tbl) * 40 + 42))
    else:
        st.info("Configure census above and click **Generate ICU Schedule** to see the calendar.")

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 2 — ROSTER
    # ════════════════════════════════════════════════════════════════════════
    st.subheader(f"{active_unit} Roster")
    rows = []
    for n in sorted(all_nurses_icu, key=lambda x: x.seniority_years, reverse=True):
        rows.append({
            "ID": n.id, "Name": n.name,
            "Role": n.role.value, "Type": n.employee_type.value,
            "FTE": n.fte, "Shift": n.shift_type.value,
            "Seniority": f"{n.seniority_years:.1f} yr",
            "Charge": "✓" if n.can_charge else "",
            "Resource": "✓" if n.can_resource else "",
            "Specialties": ", ".join(n.specialties) or "—",
            "PTO Hrs": f"{n.pto_hours_balance:.0f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=500, hide_index=True)
    st.caption(f"{len(rows)} nurses · {charge_n} charge-eligible · "
               f"{sum(1 for n in all_nurses_icu if n.can_resource)} resource-eligible")

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 3 — REQUESTS (all pending)
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("Pending Requests")
    all_tor = storage.load_time_off_requests()
    all_swp = storage.load_swap_requests()
    pending_tor = [r for r in all_tor if r.status.value == "pending"]
    pending_swp = [s for s in all_swp if s.status.value == "pending"]
    st.caption(f"{len(pending_tor)} time-off · {len(pending_swp)} swap requests pending")

    if not pending_tor and not pending_swp:
        st.success("No pending requests.")
    for r in sorted(pending_tor, key=lambda x: x.submitted_at, reverse=True):
        date_str = (f"{min(r.dates)} – {max(r.dates)}"
                    if len(r.dates) > 1 else str(r.dates[0]))
        nurse_obj2 = next((n for n in all_nurses_icu if n.id == r.nurse_id), None)
        blocks2, warns2, info2 = (
            _check_request_eligibility(r, nurse_obj2, all_tor, all_nurses_icu)
            if nurse_obj2 else (["Nurse not found"], [], [])
        )
        eligible2 = len(blocks2) == 0
        badge2 = "✅ Eligible" if eligible2 else "❌ Blocked"
        color2 = "green" if eligible2 else "red"
        with st.expander(
            f"🟡 {nurse_map_d.get(r.nurse_id, r.nurse_id)} — "
            f"{r.request_type.value.replace('_',' ').title()} — {date_str}", expanded=False
        ):
            st.write(f"**Submitted:** {r.submitted_at.strftime('%Y-%m-%d %H:%M')}")
            if r.education_activity:
                st.write(f"**Activity:** {r.education_activity}")
            st.markdown(f"**Eligibility:** :{color2}[{badge2}]")
            for b in blocks2: st.error(f"🚫 {b}")
            for w in warns2:  st.warning(f"⚠️ {w}")
            if info2:
                with st.expander("Details"): [st.markdown(f"- {i}") for i in info2]
            note = st.text_input("Note", key=f"r2_note_{r.id}",
                                 placeholder="Optional reason…")
            ca, cd = st.columns(2)
            if ca.button("✅ Approve", key=f"r2_app_{r.id}", use_container_width=True,
                         type="primary" if eligible2 else "secondary"):
                r.status = RequestStatus.APPROVED
                r.decided_at = datetime.now()
                r.decision_reason = note or "Approved by manager."
                storage.save_time_off_request(r)
                st.rerun()
            if cd.button("❌ Decline", key=f"r2_dec_{r.id}", use_container_width=True):
                r.status = RequestStatus.DENIED
                r.decided_at = datetime.now()
                r.decision_reason = note or "Declined by manager."
                storage.save_time_off_request(r)
                st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 4 — TIME OFF
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("Time Off Requests")
    all_tor2 = storage.load_time_off_requests()
    tf1, tf2 = st.columns(2)
    tf_nurse = tf1.selectbox("Filter nurse", ["All"] + list(nurse_map_d.values()),
                              key="tf_nurse")
    tf_status = tf2.selectbox("Status", ["All", "pending", "approved", "denied"],
                               key="tf_status")
    filtered_tor = all_tor2
    if tf_nurse != "All":
        nid2 = next((k for k, v in nurse_map_d.items() if v == tf_nurse), None)
        if nid2: filtered_tor = [r for r in filtered_tor if r.nurse_id == nid2]
    if tf_status != "All":
        filtered_tor = [r for r in filtered_tor if r.status.value == tf_status]
    filtered_tor = sorted(filtered_tor, key=lambda r: r.submitted_at, reverse=True)
    if not filtered_tor:
        st.info("No requests found.")
    else:
        trows = []
        for r in filtered_tor:
            date_str2 = (f"{min(r.dates)} – {max(r.dates)}"
                         if len(r.dates) > 1 else str(r.dates[0]))
            icon = {"approved": "🟢", "denied": "🔴", "pending": "🟡"}.get(r.status.value, "⚪")
            trows.append({
                "": icon,
                "Nurse": nurse_map_d.get(r.nurse_id, r.nurse_id),
                "Type": r.request_type.value.replace("_", " ").title(),
                "Date(s)": date_str2,
                "Status": r.status.value,
                "Submitted": r.submitted_at.strftime("%Y-%m-%d"),
                "Decision": r.decision_reason[:60] if r.decision_reason else "",
            })
        st.dataframe(pd.DataFrame(trows), use_container_width=True,
                     height=min(600, len(trows) * 38 + 42), hide_index=True)

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 5 — SWAPS
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("Shift Swaps")
    all_swp2 = storage.load_swap_requests()
    sw_status = st.selectbox("Status", ["All", "pending", "approved", "denied"],
                              key="sw_status")
    filtered_swp = all_swp2 if sw_status == "All" else \
        [s for s in all_swp2 if s.status.value == sw_status]
    if not filtered_swp:
        st.info("No swap requests found.")
    else:
        srows = []
        for s in sorted(filtered_swp, key=lambda x: x.submitted_at, reverse=True):
            icon = {"approved": "🟢", "denied": "🔴", "pending": "🟡"}.get(s.status.value, "⚪")
            srows.append({
                "": icon,
                "Requesting": nurse_map_d.get(s.requesting_nurse_id, s.requesting_nurse_id),
                "Accepting": nurse_map_d.get(s.accepting_nurse_id, "—") if s.accepting_nurse_id else "—",
                "Trade Date": str(s.trade_date),
                "Shift A": s.original_shift_id,
                "Shift B": s.swap_shift_id or "—",
                "Status": s.status.value,
                "Manager Approved": "✓" if s.manager_approved else "",
            })
        st.dataframe(pd.DataFrame(srows), use_container_width=True,
                     height=min(600, len(srows) * 38 + 42), hide_index=True)

        st.subheader("Approve / Decline Pending Swaps")
        pending_swp2 = [s for s in all_swp2 if s.status.value == "pending"]
        if not pending_swp2:
            st.success("No pending swaps.")
        for s in pending_swp2:
            req_nm = nurse_map_d.get(s.requesting_nurse_id, s.requesting_nurse_id)
            acc_nm = nurse_map_d.get(s.accepting_nurse_id, "—") if s.accepting_nurse_id else "—"
            sb2, sw2, si2 = _check_swap_eligibility(s, all_nurses_icu,
                                                     storage.load_time_off_requests())
            s_ok = len(sb2) == 0
            with st.expander(f"🟡 {req_nm} ↔ {acc_nm} — {s.trade_date}", expanded=True):
                st.write(f"**Shift A:** {s.original_shift_id}  |  **Shift B:** {s.swap_shift_id}")
                st.markdown(f"**Eligibility:** :{'green' if s_ok else 'red'}[{'✅ Eligible' if s_ok else '❌ Blocked'}]")
                for b in sb2: st.error(f"🚫 {b}")
                for w in sw2: st.warning(f"⚠️ {w}")
                if si2:
                    with st.expander("Details"): [st.markdown(f"- i") for i in si2]
                sn, sd_ = st.columns(2)
                if sn.button("✅ Approve", key=f"sw_app_{s.id}", use_container_width=True,
                             type="primary" if s_ok else "secondary"):
                    s.status = RequestStatus.APPROVED
                    s.manager_approved = True
                    storage.save_swap_request(s)
                    st.rerun()
                if sd_.button("❌ Decline", key=f"sw_dec_{s.id}", use_container_width=True):
                    s.status = RequestStatus.DENIED
                    storage.save_swap_request(s)
                    st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 6 — COVERAGE
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("Coverage Analysis")
    if not schedule_generated:
        st.info("Generate a schedule in Schedule Builder to see coverage analysis.")
    else:
        cov_data = []
        by_date_cov: dict = defaultdict(lambda: defaultdict(int))
        for a in icu_result_d.schedule.assignments:
            by_date_cov[a.date][a.shift_slot.value] += 1
        for e in needs_obj_d.census_entries:
            tgt = e.required_bedside_rns
            actual = by_date_cov[e.date].get(e.shift_slot.value, 0)
            cov_data.append({
                "Date": e.date, "Shift": e.shift_slot.value,
                "Target": tgt, "Actual": actual,
                "Gap": max(0, tgt - actual),
                "Status": "Filled" if actual >= tgt else ("−1" if actual == tgt - 1 else "Gap"),
            })
        df_cov = pd.DataFrame(cov_data)
        total_r = len(df_cov)
        filled_r = (df_cov["Gap"] == 0).sum()
        gap_r = (df_cov["Gap"] > 0).sum()
        cv1, cv2, cv3 = st.columns(3)
        cv1.metric("Total shift-slots", total_r)
        cv2.metric("Fully filled", filled_r, f"{round(filled_r/total_r*100)}%")
        cv3.metric("Gaps", gap_r)

        fig_cov = px.bar(df_cov, x="Date", y=["Actual", "Target"],
            barmode="overlay", color_discrete_map={"Actual": "#3B82F6", "Target": "#E2E8F0"},
            facet_row="Shift", height=500,
            title="Actual vs Target Staffing by Day")
        fig_cov.update_layout(margin=dict(l=0, r=0, t=50, b=0))
        st.plotly_chart(fig_cov, use_container_width=True)

        st.dataframe(df_cov.sort_values(["Date", "Shift"]),
                     use_container_width=True, height=300, hide_index=True)

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 7 — WELLNESS
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("Staff Wellness & Engagement")
    wl1, wl2 = st.columns(2)
    with wl1:
        cat_w = st.selectbox("Leaderboard",
            ["total_points","no_call_outs","shifts_picked_up","swaps_completed","avg_shift_rating"],
            format_func=lambda x: x.replace("_"," ").title(), key="wl_cat")
        if st.session_state.api_key_set:
            board = agent().get_leaderboard(cat_w, top_n=10)
            medals = ["🥇","🥈","🥉"]
            for entry in board:
                r = entry["rank"]
                med = medals[r-1] if r <= 3 else f"#{r}"
                vals = " · ".join(f"**{entry[k]}**" for k in entry if k not in ("rank","name"))
                st.markdown(f"{med} **{entry['name']}** — {vals}")
    with wl2:
        wl_nurse = st.selectbox("Nurse profile",
            list(nurse_map_d.values()), key="wl_nurse")
        wl_id = next((k for k, v in nurse_map_d.items() if v == wl_nurse), None)
        if wl_id and st.session_state.api_key_set:
            prof = agent().get_nurse_gamification_profile(wl_id)
            if prof:
                p1,p2,p3,p4 = st.columns(4)
                p1.metric("Points",    prof["total_points"])
                p2.metric("Streak",    f"{prof['current_streak_days']}d")
                p3.metric("No-callouts", prof["no_call_outs"])
                p4.metric("Avg rating", f"{prof['avg_shift_rating']}⭐")
                if prof["badges"]:
                    st.markdown(" ".join(
                        f'<span style="background:#FEF3C7;color:#92400E;padding:3px 10px;'
                        f'border-radius:100px;font-size:12px;font-weight:600">'
                        f'{b["icon"]} {b["name"]}</span>'
                        for b in prof["badges"]
                    ), unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════
    # SUB-TAB 8 — POLICY ASSISTANT
    # ════════════════════════════════════════════════════════════════════════
    st.subheader("Policy Assistant")
    st.caption("Ask any SHC/CRONA policy question in plain English.")
    if "policy_msgs" not in st.session_state:
        st.session_state["policy_msgs"] = []
    for msg in st.session_state["policy_msgs"]:
        icon = "👤" if msg["role"] == "user" else "📖"
        bg   = "#EFF6FF" if msg["role"] == "user" else "#F0FDF4"
        st.markdown(
            f'<div style="background:{bg};border-radius:10px;padding:10px 14px;'
            f'margin:4px 0">{icon} {msg["content"]}</div>',
            unsafe_allow_html=True)
    pol_input = st.chat_input("Ask a policy question…", key="pol_input")
    if pol_input:
        st.session_state["policy_msgs"].append({"role":"user","content":pol_input})
        if st.session_state.api_key_set:
            with st.spinner("Consulting policy…"):
                ans = agent().answer_policy_question(pol_input, {
                    "unit": active_unit, "today": date.today().isoformat()})
            st.session_state["policy_msgs"].append({"role":"assistant","content":ans})
        st.rerun()
    if st.session_state["policy_msgs"]:
        if st.button("🗑 Clear", key="pol_clear"):
            st.session_state["policy_msgs"] = []
            st.rerun()
