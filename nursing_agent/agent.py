"""
Claude-powered Scheduling Agent.

Uses the Anthropic API to:
  - Understand natural-language department need submissions
  - Explain scheduling decisions in plain language
  - Reason about complex edge cases (e.g., critical staffing shortages)
  - Draft nurse confirmation messages
  - Answer policy questions

All AI decisions are subject to hard policy-engine constraints.
"""

from __future__ import annotations
import json
import os
from datetime import date, datetime
from typing import Any

import anthropic

from .models import (
    DepartmentNeedsInput, Nurse, SchedulePeriod, ShiftRequirement,
    ShiftSlot, NurseRole, TimeOffRequest, ShiftSwapRequest,
    RequestType, RequestStatus, AssignedShift,
)
from .scheduler import Scheduler, SchedulingResult
from .request_handler import RequestHandler, RequestDecision
from .gamification import GamificationEngine
from . import storage


MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an expert nursing scheduling agent for Stanford Health Care's inpatient nursing units.
You operate under the SHC/CRONA Collective Bargaining Agreement (2025–2028) and SHC staffing policies.

Your responsibilities:
1. Generate fair, policy-compliant schedules that meet FTE commitments
2. Process PTO, A-day, and shift swap requests with clear explanations
3. Manage float and cancellation decisions per CRONA contract order
4. Track gamification events and celebrate staff achievements
5. Alert managers to coverage gaps and suggest resolutions

Key policy rules you always enforce:
- Schedule request priority: pre-approved vacation > pre-approved education > skill mix > seniority > PTO
- Cancellation order: voluntary > traveler > relief over commitment > regular over commitment > relief > regular (inverse seniority)
- Float order: voluntary > relief over commitment > regular over commitment > registry > traveler > relief > regular
- A-day requests: up to 4 weeks in advance, cutoff 8 hrs before shift; check 75 min before; 15 min to accept
- Mandatory A-day: CRONA order; notify ≥60 min before; callback after 1 hr = 1.5x pay
- Shift swap: submit ≥3 days prior, manager approval required
- Max 5 red days per schedule period (not counting pre-approved or designated weekends)
- FTE compliance is non-negotiable; nurses must meet biweekly hour commitments
- Union contract always takes precedence over hospital policy
- Schedules must be posted ≥2 weeks in advance in 4-week periods

When generating a schedule or making decisions, always explain:
- Which rules you applied
- Why a request was approved or denied
- What alternatives exist when denying a request

Tone: professional, supportive, clear. Nurses are colleagues, not resources."""


class NursingSchedulingAgent:
    """
    Main agent that orchestrates scheduling, requests, and communications.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.gamification = GamificationEngine()

    # ── Schedule Generation ───────────────────────────────────────────────────

    def generate_schedule_from_needs(
        self,
        needs: DepartmentNeedsInput,
    ) -> tuple[SchedulingResult, str]:
        """
        Full pipeline: load nurses → apply policy constraints → generate schedule
        → ask Claude to review and explain → return result + narrative.
        """
        nurses = storage.load_nurses()
        if not nurses:
            return None, "No nurses found in roster. Please add nurses first."

        approved_time_off = [
            r for r in storage.load_time_off_requests()
            if r.status == RequestStatus.APPROVED
        ]

        scheduler = Scheduler(nurses)
        result = scheduler.generate_schedule(needs, approved_time_off)

        if result.schedule:
            storage.save_schedule(result.schedule)

        # Ask Claude to produce a human-readable schedule summary
        try:
            narrative = self._explain_schedule(result, nurses, needs)
        except Exception as e:
            narrative = (
                f"Schedule generated: {len(result.schedule.assignments)} assignments across "
                f"{needs.unit} ({needs.schedule_start} – {needs.schedule_end}).\n\n"
                f"⚠️ Could not generate AI narrative: {e}"
            )
        return result, narrative

    def _explain_schedule(
        self,
        result: SchedulingResult,
        nurses: list[Nurse],
        needs: DepartmentNeedsInput,
    ) -> str:
        nurse_map = {n.id: n.name for n in nurses}

        # Build compact summary for Claude
        summary = {
            "unit": needs.unit,
            "period": f"{needs.schedule_start} to {needs.schedule_end}",
            "total_assignments": len(result.schedule.assignments) if result.schedule else 0,
            "coverage_gaps": result.coverage_gaps,
            "warnings": result.warnings,
            "fte_issues": [
                v for v in result.fte_report.values() if not v["compliant"]
            ],
            "sample_assignments": [
                {
                    "date": a.date.isoformat(),
                    "shift": a.shift_slot.value,
                    "nurse": nurse_map.get(a.nurse_id, a.nurse_id),
                    "unit": a.unit,
                    "over_commitment": a.is_over_commitment,
                }
                for a in (result.schedule.assignments[:10] if result.schedule else [])
            ],
        }

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"A schedule was just generated. Here is the summary:\n\n"
                    f"{json.dumps(summary, indent=2)}\n\n"
                    "Please provide:\n"
                    "1. A brief manager summary (2-3 sentences)\n"
                    "2. Any critical warnings that need immediate attention\n"
                    "3. Recommended next steps for coverage gaps\n"
                    "Keep it concise and actionable."
                ),
            }],
        )
        return response.content[0].text

    # ── Request Processing ────────────────────────────────────────────────────

    def process_time_off_request(
        self,
        nurse_id: str,
        request_type_str: str,
        dates: list[date],
        pto_hours: float = 0.0,
        education_activity: str = "",
    ) -> tuple[RequestDecision, str]:
        """
        Reviews a time-off request, applies policy, saves result,
        and returns a nurse-facing explanation from Claude.
        """
        nurses = storage.load_nurses()
        nurse_map = {n.id: n for n in nurses}
        nurse = nurse_map.get(nurse_id)
        if not nurse:
            return RequestDecision(False, "Nurse not found.", ""), "Nurse not found in roster."

        request_type = RequestType(request_type_str)
        handler = RequestHandler(nurse_map)

        req = handler.create_time_off_request(
            nurse_id=nurse_id,
            request_type=request_type,
            dates=dates,
            pto_hours=pto_hours,
            education_activity=education_activity,
        )

        existing = storage.load_time_off_requests()
        active_schedule = storage.get_active_schedule(
            unit=nurse.float_regions[0] if nurse.float_regions else "",
            for_date=dates[0] if dates else date.today(),
        )

        decision = handler.review_pto_request(req, existing, active_schedule)
        req.status = RequestStatus.APPROVED if decision.approved else RequestStatus.DENIED
        req.decision_reason = decision.reason
        req.decided_at = datetime.now()
        storage.save_time_off_request(req)

        # Generate nurse-facing message from Claude
        message = self._draft_request_response(nurse, req, decision)
        return decision, message

    def process_shift_swap(
        self,
        requesting_nurse_id: str,
        accepting_nurse_id: str,
        original_shift_id: str,
        swap_shift_id: str,
        trade_date: date,
    ) -> tuple[RequestDecision, str]:
        nurses = storage.load_nurses()
        nurse_map = {n.id: n for n in nurses}
        handler = RequestHandler(nurse_map)

        swap = handler.create_swap_request(
            requesting_nurse_id=requesting_nurse_id,
            accepting_nurse_id=accepting_nurse_id,
            original_shift_id=original_shift_id,
            swap_shift_id=swap_shift_id,
            trade_date=trade_date,
        )

        nurse1 = nurse_map.get(requesting_nurse_id)
        nurse2 = nurse_map.get(accepting_nurse_id)
        if not nurse1 or not nurse2:
            return RequestDecision(False, "One or both nurses not found.", swap.id), ""

        decision = handler.review_shift_swap(swap, nurse1, nurse2)
        swap.status = RequestStatus.APPROVED if decision.approved else RequestStatus.DENIED
        storage.save_swap_request(swap)

        # Award gamification if approved
        if decision.approved:
            event1 = self.gamification.award_swap_completed(nurse1, trade_date, nurse2.name)
            event2 = self.gamification.award_swap_completed(nurse2, trade_date, nurse1.name)
            storage.save_gamification_event(event1)
            storage.save_gamification_event(event2)
            storage.upsert_nurse(nurse1)
            storage.upsert_nurse(nurse2)

        message = self._draft_swap_response(nurse1, nurse2, swap, decision)
        return decision, message

    def process_a_day_request(
        self,
        nurse_id: str,
        requested_date: date,
        shift_slot: ShiftSlot,
        unit: str,
    ) -> tuple[RequestDecision, str]:
        nurses = storage.load_nurses()
        nurse_map = {n.id: n for n in nurses}
        nurse = nurse_map.get(nurse_id)
        if not nurse:
            return RequestDecision(False, "Nurse not found.", ""), ""

        handler = RequestHandler(nurse_map)
        req = handler.create_time_off_request(
            nurse_id=nurse_id,
            request_type=RequestType.A_DAY,
            dates=[requested_date],
        )

        # Get nurses on same shift for equity check
        schedule = storage.get_active_schedule(unit, requested_date)
        nurses_on_shift: list[Nurse] = []
        if schedule:
            nurses_on_shift = [
                nurse_map[a.nurse_id]
                for a in schedule.assignments
                if a.date == requested_date
                and a.shift_slot == shift_slot
                and a.nurse_id in nurse_map
            ]

        decision = handler.review_a_day_request(req, nurse, nurses_on_shift)
        req.status = RequestStatus.APPROVED if decision.approved else RequestStatus.DENIED
        req.decision_reason = decision.reason
        req.decided_at = datetime.now()

        if decision.approved:
            nurse.a_days_this_pay_period += 1
            storage.upsert_nurse(nurse)

        storage.save_time_off_request(req)
        message = self._draft_request_response(nurse, req, decision)
        return decision, message

    # ── Natural-Language Queries ──────────────────────────────────────────────

    def answer_policy_question(self, question: str, context: dict | None = None) -> str:
        """
        Answers nurse/manager policy questions using the embedded knowledge.
        """
        ctx = ""
        if context:
            ctx = f"\n\nContext: {json.dumps(context, indent=2, default=str)}"

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"{question}{ctx}",
            }],
        )
        return response.content[0].text

    def parse_department_needs(self, natural_language_input: str, unit: str) -> DepartmentNeedsInput | None:
        """
        Converts free-text department need description into structured DepartmentNeedsInput.
        Example input: "Next 4 weeks I need 3 RNs on days and 2 on nights every day,
        plus 1 charge nurse each shift on K5."
        """
        today = date.today()

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Convert this department staffing need into a structured JSON object. "
                    f"Today is {today}. The unit is '{unit}'.\n\n"
                    f"Input: {natural_language_input}\n\n"
                    "Output a JSON object with this structure:\n"
                    "{\n"
                    '  "unit": "<unit>",\n'
                    '  "schedule_start": "<YYYY-MM-DD>",\n'
                    '  "schedule_end": "<YYYY-MM-DD>",\n'
                    '  "notes": "<any notes>",\n'
                    '  "daily_requirements": [\n'
                    "    {\n"
                    '      "date": "<YYYY-MM-DD>",\n'
                    '      "shift_slot": "<day|evening|night_8|night_12>",\n'
                    '      "unit": "<unit>",\n'
                    '      "count": <int>,\n'
                    '      "required_role": "<rn|charge|resource|na|us>",\n'
                    '      "required_specialties": [],\n'
                    '      "charge_needed": <bool>,\n'
                    '      "resource_needed": <bool>\n'
                    "    }\n"
                    "  ]\n"
                    "}\n\n"
                    "Generate one requirement entry per date per shift slot. "
                    "Return ONLY valid JSON, no other text."
                ),
            }],
        )

        try:
            raw_text = response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = "\n".join(raw_text.split("\n")[1:])
            if raw_text.endswith("```"):
                raw_text = "\n".join(raw_text.split("\n")[:-1])
            data = json.loads(raw_text)
            return DepartmentNeedsInput.model_validate(data)
        except Exception as e:
            return None

    # ── Confirmation Messages ─────────────────────────────────────────────────

    def draft_schedule_confirmation(
        self,
        nurse: Nurse,
        assignments: list[AssignedShift],
    ) -> str:
        """Generates a personalized schedule confirmation message for a nurse."""
        shift_list = "\n".join(
            f"  • {a.date} {a.shift_slot.value} shift on unit {a.unit} "
            f"({a.start_time.strftime('%H:%M')}–{a.end_time.strftime('%H:%M')}, {a.hours} hrs)"
            for a in sorted(assignments, key=lambda x: x.date)
        )

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Draft a friendly schedule confirmation message for nurse {nurse.name} "
                    f"(FTE {nurse.fte}, {nurse.shift_type.value} shifts). "
                    f"Their upcoming shifts are:\n{shift_list}\n\n"
                    "Include: total hours, how this aligns with their FTE commitment, "
                    "reminder to submit any time-off requests before the cutoff, "
                    "and how to request shift swaps. Keep it warm and concise."
                ),
            }],
        )
        return response.content[0].text

    # ── Internal Draft Helpers ────────────────────────────────────────────────

    def _draft_request_response(
        self,
        nurse: Nurse,
        req: TimeOffRequest,
        decision: RequestDecision,
    ) -> str:
        status = "APPROVED" if decision.approved else "DENIED"
        refs = ", ".join(decision.policy_references) if decision.policy_references else "SHC policy"

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=384,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Draft a brief, professional message to nurse {nurse.name} regarding their "
                    f"{req.request_type.value} request for {', '.join(str(d) for d in req.dates[:3])} "
                    f"({'and more dates' if len(req.dates) > 3 else ''}).\n"
                    f"Decision: {status}\n"
                    f"Reason: {decision.reason}\n"
                    f"Policy basis: {refs}\n\n"
                    "If denied, suggest one alternative. Keep it under 100 words."
                ),
            }],
        )
        return response.content[0].text

    def _draft_swap_response(
        self,
        nurse1: Nurse,
        nurse2: Nurse,
        swap: ShiftSwapRequest,
        decision: RequestDecision,
    ) -> str:
        status = "APPROVED" if decision.approved else "DENIED"
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Draft a brief confirmation to {nurse1.name} and {nurse2.name} "
                    f"about their shift swap request for {swap.trade_date}.\n"
                    f"Decision: {status}\n"
                    f"Reason: {decision.reason}\n"
                    "Keep it under 80 words. If approved, remind them the trade must be "
                    "in the system ≥3 days prior and requires manager approval."
                ),
            }],
        )
        return response.content[0].text

    # ── Gamification Integration ──────────────────────────────────────────────

    def record_shift_completed(
        self,
        nurse_id: str,
        shift_date: date,
        shift_slot: ShiftSlot,
        was_on_time: bool,
        was_call_out: bool,
    ) -> list[GamificationEvent]:
        nurses = storage.load_nurses()
        nurse = next((n for n in nurses if n.id == nurse_id), None)
        if not nurse:
            return []

        events = []
        if was_call_out:
            self.gamification.record_call_out(nurse)
        else:
            e1 = self.gamification.award_no_call_out(nurse, shift_date)
            events.append(e1)
            storage.save_gamification_event(e1)

            if was_on_time:
                e2 = self.gamification.award_on_time(nurse, shift_date)
                events.append(e2)
                storage.save_gamification_event(e2)

        storage.upsert_nurse(nurse)
        return events

    def record_shift_rating(
        self,
        nurse_id: str,
        shift_date: date,
        shift_slot: ShiftSlot,
        unit: str,
        rating: int,
        comments: str = "",
    ) -> ShiftRating:
        sr, _ = self.gamification.submit_shift_rating(
            nurse_id, shift_date, shift_slot, unit, rating, comments
        )
        storage.save_shift_rating(sr)
        return sr

    def get_leaderboard(self, category: str = "total_points", top_n: int = 10) -> list[dict]:
        nurses = storage.load_nurses()
        return self.gamification.leaderboard(nurses, category, top_n)

    def get_nurse_gamification_profile(self, nurse_id: str) -> dict:
        nurses = storage.load_nurses()
        nurse = next((n for n in nurses if n.id == nurse_id), None)
        if not nurse:
            return {}
        return self.gamification.nurse_stats(nurse)
