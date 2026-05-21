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

        for r in sorted(pending_tor, key=lambda x: x.submitted_at, reverse=True):
            date_str = (
                f"{min(r.dates)} – {max(r.dates)}" if len(r.dates) > 1 else str(r.dates[0])
            )
            label = f"🟡 {nmap.get(r.nurse_id, r.nurse_id)} — {r.request_type.value.replace('_',' ').title()} — {date_str}"
            with st.expander(label, expanded=True):
                st.write(f"**Nurse:** {nmap.get(r.nurse_id, r.nurse_id)}")
                st.write(f"**Type:** {r.request_type.value.replace('_',' ').title()}")
                st.write(f"**Date(s):** {date_str}")
                if r.education_activity:
                    st.write(f"**Activity:** {r.education_activity}")
                st.write(f"**Submitted:** {r.submitted_at.strftime('%Y-%m-%d %H:%M')}")

                mgr_note = st.text_input(
                    "Manager note (optional)", key=f"note_{r.id}", placeholder="Reason for decision…"
                )
                col_a, col_d = st.columns(2)
                if col_a.button("✅ Approve", key=f"approve_{r.id}", use_container_width=True):
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
            label = f"🟡 Swap — {req_name} ↔ {acc_name} — {s.trade_date}"
            with st.expander(label, expanded=True):
                st.write(f"**Requesting:** {req_name}")
                st.write(f"**Accepting:** {acc_name}")
                st.write(f"**Trade date:** {s.trade_date}")
                st.write(f"**Shift A:** {s.original_shift_id}")
                st.write(f"**Shift B:** {s.swap_shift_id}")
                if s.notes:
                    st.write(f"**Notes:** {s.notes}")
                st.write(f"**Submitted:** {s.submitted_at.strftime('%Y-%m-%d %H:%M')}")

                swap_note = st.text_input(
                    "Manager note (optional)", key=f"swap_note_{s.id}", placeholder="Reason for decision…"
                )
                col_a, col_d = st.columns(2)
                if col_a.button("✅ Approve", key=f"swap_approve_{s.id}", use_container_width=True):
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
# TAB 6 — ICU SCHEDULE
# ════════════════════════════════════════════════════════════════════════════
with tab_icu:
    st.header("ICU Schedule Generator")
    st.caption(
        "Enter census and acuity data to automatically calculate staffing requirements "
        "and generate a CRONA-compliant ICU schedule."
    )

    icu_left, icu_right = st.columns([1, 1])

    # ── Left column: ICU Operational Needs Input ─────────────────────────────
    with icu_left:
        st.subheader("ICU Operational Needs")

        icu_unit = st.text_input(
            "ICU Unit", value="CVICU",
            help="e.g. CVICU, MICU, SICU, NSICU, PICU",
            key="icu_unit",
        )
        icu_col1, icu_col2 = st.columns(2)
        icu_start = icu_col1.date_input(
            "Schedule start", value=date.today() + timedelta(days=7), key="icu_start"
        )
        icu_weeks = icu_col2.number_input(
            "Weeks", min_value=1, max_value=8, value=4, key="icu_weeks"
        )
        icu_bed_cap = st.number_input(
            "ICU bed capacity", min_value=4, max_value=60, value=20, key="icu_bed_cap"
        )

        st.divider()
        st.markdown("**Census & Acuity by Shift**")
        st.caption(
            "Critical patients require a **1:1** RN ratio. "
            "Stable patients require a **1:2** RN ratio."
        )

        census_mode = st.radio(
            "Census input mode",
            ["Uniform (same for all shifts)", "By shift type (day / eve / night)"],
            key="icu_census_mode",
            horizontal=True,
        )

        def _census_block(suffix: str, label: str):
            with st.expander(label, expanded=True):
                c1, c2 = st.columns(2)
                crit = c1.number_input("Critical (1:1)", min_value=0, max_value=30,
                                       value=4, key=f"icu_crit_{suffix}")
                stbl = c2.number_input("Stable (1:2)", min_value=0, max_value=30,
                                       value=8, key=f"icu_stbl_{suffix}")
                c3, c4 = st.columns(2)
                vent = c3.number_input("On ventilator", min_value=0, max_value=30,
                                       value=2, key=f"icu_vent_{suffix}")
                ecmo = c4.number_input("On ECMO", min_value=0, max_value=10,
                                       value=0, key=f"icu_ecmo_{suffix}")
            return crit, stbl, vent, ecmo

        if census_mode.startswith("Uniform"):
            day_crit, day_stbl, day_vent, day_ecmo = _census_block("all", "All Shifts")
            eve_crit, eve_stbl, eve_vent, eve_ecmo = day_crit, day_stbl, day_vent, day_ecmo
            ngt_crit, ngt_stbl, ngt_vent, ngt_ecmo = day_crit, day_stbl, day_vent, day_ecmo
        else:
            day_crit, day_stbl, day_vent, day_ecmo = _census_block("day", "Day Shift")
            eve_crit, eve_stbl, eve_vent, eve_ecmo = _census_block("eve", "Evening Shift")
            ngt_crit, ngt_stbl, ngt_vent, ngt_ecmo = _census_block("ngt", "Night Shift (12hr)")

        import math as _math

        def _rns(crit, stbl):
            return crit + _math.ceil(stbl / 2) if (crit + stbl) > 0 else 0

        day_rns = _rns(day_crit, day_stbl)
        eve_rns = _rns(eve_crit, eve_stbl)
        ngt_rns = _rns(ngt_crit, ngt_stbl)

        st.markdown("**Calculated staffing requirement:**")
        pc1, pc2, pc3 = st.columns(3)
        pc1.metric("Day RNs", day_rns, help="= critical + ⌈stable ÷ 2⌉")
        pc2.metric("Evening RNs", eve_rns)
        pc3.metric("Night RNs (12hr)", ngt_rns)

        day_census_total = day_crit + day_stbl
        occupancy_pct = round((day_census_total / icu_bed_cap) * 100) if icu_bed_cap > 0 else 0
        st.caption(
            f"Day shift occupancy: **{day_census_total}/{icu_bed_cap} beds** ({occupancy_pct}%)"
        )

        st.divider()
        st.markdown("**Additional staffing needs:**")
        ac1, ac2 = st.columns(2)
        icu_charge = ac1.checkbox("Charge nurse (each day shift)", value=True, key="icu_charge")
        icu_resource = ac2.checkbox("Resource nurse needed", value=False, key="icu_resource")

        sc1, sc2, sc3 = st.columns(3)
        icu_vent_spec = sc1.checkbox(
            "Vent specialist", value=bool(day_vent > 0), key="icu_vent_spec"
        )
        icu_ecmo_spec = sc2.checkbox(
            "ECMO capable", value=bool(day_ecmo > 0), key="icu_ecmo_spec"
        )
        icu_crrt_spec = sc3.checkbox("CRRT capable", value=False, key="icu_crrt_spec")

        icu_notes = st.text_area(
            "Clinical notes / context", key="icu_notes", height=68,
            placeholder="e.g. Post-cardiac surgery surge, 2 ECMO patients expected this week",
        )

        if st.button(
            "⚡ Generate ICU Schedule", type="primary",
            use_container_width=True, key="btn_icu_gen"
        ):
            if not st.session_state.api_key_set:
                st.error("API key required.")
            elif day_rns == 0 and eve_rns == 0 and ngt_rns == 0:
                st.warning("No patients entered — fill in census data above.")
            else:
                icu_end = icu_start + timedelta(weeks=int(icu_weeks)) - timedelta(days=1)

                census_entries = []
                cur = icu_start
                while cur <= icu_end:
                    census_entries.extend([
                        ICUShiftCensus(
                            date=cur, shift_slot=ShiftSlot.DAY,
                            critical_patients=day_crit, stable_patients=day_stbl,
                            vent_patients=day_vent, ecmo_patients=day_ecmo,
                        ),
                        ICUShiftCensus(
                            date=cur, shift_slot=ShiftSlot.EVENING,
                            critical_patients=eve_crit, stable_patients=eve_stbl,
                            vent_patients=eve_vent, ecmo_patients=eve_ecmo,
                        ),
                        ICUShiftCensus(
                            date=cur, shift_slot=ShiftSlot.NIGHT_12,
                            critical_patients=ngt_crit, stable_patients=ngt_stbl,
                            vent_patients=ngt_vent, ecmo_patients=ngt_ecmo,
                        ),
                    ])
                    cur += timedelta(days=1)

                icu_needs = ICUOperationalNeeds(
                    unit=icu_unit,
                    bed_capacity=int(icu_bed_cap),
                    schedule_start=icu_start,
                    schedule_end=icu_end,
                    census_entries=census_entries,
                    charge_each_shift=icu_charge,
                    resource_nurse_needed=icu_resource,
                    vent_specialist_needed=icu_vent_spec,
                    ecmo_capable_needed=icu_ecmo_spec,
                    crrt_capable_needed=icu_crrt_spec,
                    notes=icu_notes,
                )

                dept_needs = _icu_needs_to_department_needs(icu_needs)

                with st.spinner(f"Generating {int(icu_weeks)}-week ICU schedule for {icu_unit}…"):
                    result, narrative = agent().generate_schedule_from_needs(dept_needs)

                if result:
                    st.session_state["icu_result"] = result
                    st.session_state["icu_narrative"] = narrative
                    st.session_state["icu_needs_obj"] = icu_needs
                    st.success(
                        f"ICU schedule generated: {len(result.schedule.assignments)} assignments"
                    )
                    if result.coverage_gaps:
                        st.warning(
                            f"{len(result.coverage_gaps)} coverage gap(s) — review results panel"
                        )

        if "icu_narrative" in st.session_state:
            with st.expander("📄 Agent Summary", expanded=True):
                st.markdown(st.session_state["icu_narrative"])

    # ── Right column: Staffing Overview & Results ────────────────────────────
    with icu_right:
        st.subheader("ICU Staffing Overview")

        if "icu_needs_obj" in st.session_state:
            import pandas as pd
            needs_obj: ICUOperationalNeeds = st.session_state["icu_needs_obj"]
            icu_result = st.session_state.get("icu_result")

            total_days = (needs_obj.schedule_end - needs_obj.schedule_start).days + 1
            total_rn_shifts = sum(e.required_bedside_rns for e in needs_obj.census_entries)
            avg_census = (
                sum(e.total_census for e in needs_obj.census_entries)
                / len(needs_obj.census_entries)
                if needs_obj.census_entries else 0
            )

            m1, m2, m3 = st.columns(3)
            m1.metric("Schedule days", total_days)
            m2.metric("Total RN shifts", total_rn_shifts)
            m3.metric("Avg census/shift", f"{avg_census:.1f}")

            st.markdown("**Daily requirements — first 7 days:**")
            week1_entries = [
                e for e in needs_obj.census_entries
                if e.date <= needs_obj.schedule_start + timedelta(days=6)
            ]
            if week1_entries:
                rows = []
                for e in sorted(week1_entries, key=lambda x: (x.date, x.shift_slot.value)):
                    rows.append({
                        "Date": e.date.strftime("%a %b %d"),
                        "Shift": e.shift_slot.value,
                        "Critical": e.critical_patients,
                        "Stable": e.stable_patients,
                        "Vent": e.vent_patients,
                        "ECMO": e.ecmo_patients,
                        "RNs needed": e.required_bedside_rns,
                        "Occupancy": f"{e.total_census}/{needs_obj.bed_capacity}",
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, height=280)

            if icu_result and icu_result.coverage_gaps:
                st.markdown("### ⚠️ ICU Coverage Gaps")
                for g in icu_result.coverage_gaps:
                    st.markdown(
                        f'<span class="gap-warning">• {g["date"]} {g["shift"]} — '
                        f'short by {g["gap"]} {g["required_role"]}(s)</span>',
                        unsafe_allow_html=True,
                    )

            if icu_result and icu_result.schedule.assignments:
                st.markdown("**Generated assignments — first 7 days:**")
                nurse_map = {n.id: n.name for n in storage.load_nurses()}
                week1_end = needs_obj.schedule_start + timedelta(days=6)
                week1_assigns = [
                    a for a in icu_result.schedule.assignments if a.date <= week1_end
                ]
                if week1_assigns:
                    rows = []
                    for a in sorted(week1_assigns, key=lambda x: (x.date, x.shift_slot.value)):
                        rows.append({
                            "Date": a.date.strftime("%a %b %d"),
                            "Shift": a.shift_slot.value,
                            "Nurse": nurse_map.get(a.nurse_id, a.nurse_id),
                            "Hours": a.hours,
                            "Over Commit": "⚠️" if a.is_over_commitment else "",
                            "Float": f"← {a.float_from_unit}" if a.is_float else "",
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=280)

        else:
            st.info("Fill in census data on the left and click **Generate ICU Schedule**.")

            st.markdown("**ICU staffing ratios applied:**")
            st.markdown(
                """
| Acuity | RN ratio | Notes |
|---|---|---|
| Critical (unstable) | 1:1 | 1 RN per patient |
| Stable | 1:2 | 1 RN per 2 patients |
| On ventilator | — | Vent-trained RN required |
| On ECMO | — | ECMO-trained RN required |
| On CRRT | — | CRRT-trained RN required |
"""
            )

            st.markdown("**Tracked specialty skills:**")
            for skill in [
                "Mechanical Ventilation (vent)",
                "ECMO (extracorporeal membrane oxygenation)",
                "CRRT (continuous renal replacement therapy)",
                "Charge nurse leadership",
                "Resource / supervisory nurse",
            ]:
                st.markdown(f"- {skill}")
