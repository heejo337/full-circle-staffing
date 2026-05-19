"""
Gamification Engine — awards points, badges, and tracks shift ratings.

Incentivizes: no call-outs, punctuality, shift pickups, swaps, volunteering.
"""

from __future__ import annotations
import uuid
from datetime import date, datetime
from typing import Optional

from .models import (
    Badge, GamificationEvent, Nurse, ShiftRating, ShiftSlot,
    BADGES, POINTS,
)


LEADERBOARD_CATEGORIES = [
    "total_points",
    "no_call_outs",
    "shifts_picked_up",
    "swaps_completed",
    "avg_shift_rating",
]


class GamificationEngine:
    """
    Central engine for all gamification actions.
    Attach to the scheduler/request handler to call award_* methods.
    """

    def __init__(self) -> None:
        self.events: list[GamificationEvent] = []
        self.ratings: list[ShiftRating] = []

    # ── Point Awards ─────────────────────────────────────────────────────────

    def award_no_call_out(self, nurse: Nurse, shift_date: date) -> GamificationEvent:
        """Nurse completed their shift without calling out."""
        nurse.total_no_call_outs += 1
        nurse.current_streak_days += 1
        pts = POINTS["no_call_out"]

        # Streak bonuses
        bonus_desc = ""
        if nurse.current_streak_days == 7:
            pts += POINTS["streak_7_days"]
            bonus_desc = f" +{POINTS['streak_7_days']} streak bonus (7 days)!"
        elif nurse.current_streak_days == 30:
            pts += POINTS["streak_30_days"]
            bonus_desc = f" +{POINTS['streak_30_days']} streak bonus (30 days)!"

        nurse.gamification_points += pts
        event = GamificationEvent(
            id=str(uuid.uuid4()),
            nurse_id=nurse.id,
            event_type="no_call_out",
            points_awarded=pts,
            description=f"No call-out on {shift_date}.{bonus_desc}",
        )
        self.events.append(event)
        self._check_and_award_badges(nurse)
        return event

    def award_on_time(self, nurse: Nurse, shift_date: date) -> GamificationEvent:
        """Nurse clocked in on time (at or before designated shift start)."""
        nurse.total_on_time += 1
        pts = POINTS["on_time"]
        nurse.gamification_points += pts
        event = GamificationEvent(
            id=str(uuid.uuid4()),
            nurse_id=nurse.id,
            event_type="on_time",
            points_awarded=pts,
            description=f"On-time arrival on {shift_date}.",
        )
        self.events.append(event)
        self._check_and_award_badges(nurse)
        return event

    def award_shift_pickup(
        self,
        nurse: Nurse,
        shift_date: date,
        shift_slot: ShiftSlot,
        is_short_notice: bool = False,
    ) -> GamificationEvent:
        """Nurse picked up an open shift (voluntarily)."""
        nurse.total_shifts_picked_up += 1
        key = "shift_pickup_short_notice" if is_short_notice else "shift_pickup"
        pts = POINTS[key]
        nurse.gamification_points += pts
        notice_str = " (short notice <4 hrs)" if is_short_notice else ""
        event = GamificationEvent(
            id=str(uuid.uuid4()),
            nurse_id=nurse.id,
            event_type="shift_pickup",
            points_awarded=pts,
            description=f"Picked up {shift_slot.value} shift on {shift_date}{notice_str}.",
        )
        self.events.append(event)
        self._check_and_award_badges(nurse)
        return event

    def award_swap_completed(
        self,
        nurse: Nurse,
        swap_date: date,
        with_nurse_name: str,
    ) -> GamificationEvent:
        """Nurse successfully completed a shift swap."""
        nurse.total_swaps_completed += 1
        pts = POINTS["swap_completed"]
        nurse.gamification_points += pts
        event = GamificationEvent(
            id=str(uuid.uuid4()),
            nurse_id=nurse.id,
            event_type="swap_completed",
            points_awarded=pts,
            description=f"Completed shift swap with {with_nurse_name} on {swap_date}.",
        )
        self.events.append(event)
        self._check_and_award_badges(nurse)
        return event

    def award_volunteer_float(
        self,
        nurse: Nurse,
        float_date: date,
        to_unit: str,
    ) -> GamificationEvent:
        """Nurse volunteered to float to another unit."""
        pts = POINTS["volunteer_float"]
        nurse.gamification_points += pts
        event = GamificationEvent(
            id=str(uuid.uuid4()),
            nurse_id=nurse.id,
            event_type="volunteer_float",
            points_awarded=pts,
            description=f"Volunteered to float to {to_unit} on {float_date}.",
        )
        self.events.append(event)
        self._check_and_award_badges(nurse)
        return event

    def award_perfect_attendance_pay_period(self, nurse: Nurse) -> GamificationEvent:
        """No call-outs for the entire biweekly pay period."""
        pts = POINTS["perfect_attendance_pp"]
        nurse.gamification_points += pts
        event = GamificationEvent(
            id=str(uuid.uuid4()),
            nurse_id=nurse.id,
            event_type="perfect_attendance",
            points_awarded=pts,
            description="Perfect attendance for the pay period! No call-outs.",
        )
        self.events.append(event)
        self._check_and_award_badges(nurse)
        return event

    def record_call_out(self, nurse: Nurse) -> None:
        """Reset streak on call-out."""
        nurse.current_streak_days = 0

    # ── Shift Rating ──────────────────────────────────────────────────────────

    def submit_shift_rating(
        self,
        nurse_id: str,
        shift_date: date,
        shift_slot: ShiftSlot,
        unit: str,
        rating: int,
        comments: str = "",
    ) -> tuple[ShiftRating, Optional[GamificationEvent]]:
        """
        Nurse rates their completed shift 0–5 stars.
        High ratings earn bonus points for the nursing team context tracking.
        """
        if rating < 0 or rating > 5:
            raise ValueError("Rating must be 0–5 stars.")

        sr = ShiftRating(
            nurse_id=nurse_id,
            shift_date=shift_date,
            shift_slot=shift_slot,
            unit=unit,
            rating=rating,
            comments=comments,
        )
        self.ratings.append(sr)

        # Rating does not award points directly to nurse (it's feedback on the shift),
        # but we track it for unit quality metrics. Managers can see trends.
        return sr, None

    # ── Badge System ──────────────────────────────────────────────────────────

    def _check_and_award_badges(self, nurse: Nurse) -> list[Badge]:
        awarded = []

        checks = [
            ("reliable_rn",    nurse.total_no_call_outs >= 3 * 26),  # ~3 months shifts
            ("team_player",    nurse.total_swaps_completed >= 10),
            ("shift_hero",     nurse.total_shifts_picked_up >= 5),
            ("early_bird",     nurse.total_on_time >= 30),
            ("float_champ",    self._float_count(nurse) >= 10),
            ("5_star_nurse",   self._five_star_count(nurse.id) >= 10),
            ("veteran",        nurse.seniority_years >= 10),
            ("century_points", nurse.gamification_points >= 1000),
        ]

        for badge_id, condition in checks:
            if condition and not self._nurse_has_badge(nurse, badge_id):
                badge = next((b for b in BADGES if b.id == badge_id), None)
                if badge:
                    awarded.append(badge)

        return awarded

    def _nurse_has_badge(self, nurse: Nurse, badge_id: str) -> bool:
        return any(e.event_type == f"badge_{badge_id}" for e in self.events if e.nurse_id == nurse.id)

    def _float_count(self, nurse: Nurse) -> int:
        return sum(1 for e in self.events if e.nurse_id == nurse.id and e.event_type == "volunteer_float")

    def _five_star_count(self, nurse_id: str) -> int:
        return sum(1 for r in self.ratings if r.nurse_id == nurse_id and r.rating == 5)

    # ── Leaderboard ───────────────────────────────────────────────────────────

    def leaderboard(
        self,
        nurses: list[Nurse],
        category: str = "total_points",
        top_n: int = 10,
    ) -> list[dict]:
        """Returns top-N nurses for a given category."""
        if category == "total_points":
            ranked = sorted(nurses, key=lambda n: n.gamification_points, reverse=True)
            return [
                {
                    "rank": i + 1,
                    "name": n.name,
                    "points": n.gamification_points,
                    "badges": self._badge_count(n.id),
                }
                for i, n in enumerate(ranked[:top_n])
            ]

        if category == "no_call_outs":
            ranked = sorted(nurses, key=lambda n: n.total_no_call_outs, reverse=True)
            return [{"rank": i + 1, "name": n.name, "count": n.total_no_call_outs}
                    for i, n in enumerate(ranked[:top_n])]

        if category == "shifts_picked_up":
            ranked = sorted(nurses, key=lambda n: n.total_shifts_picked_up, reverse=True)
            return [{"rank": i + 1, "name": n.name, "count": n.total_shifts_picked_up}
                    for i, n in enumerate(ranked[:top_n])]

        if category == "swaps_completed":
            ranked = sorted(nurses, key=lambda n: n.total_swaps_completed, reverse=True)
            return [{"rank": i + 1, "name": n.name, "count": n.total_swaps_completed}
                    for i, n in enumerate(ranked[:top_n])]

        if category == "avg_shift_rating":
            def avg_rating(n: Nurse) -> float:
                nurse_ratings = [r.rating for r in self.ratings if r.nurse_id == n.id]
                return sum(nurse_ratings) / len(nurse_ratings) if nurse_ratings else 0.0
            ranked = sorted(nurses, key=avg_rating, reverse=True)
            return [{"rank": i + 1, "name": n.name, "avg_rating": round(avg_rating(n), 2)}
                    for i, n in enumerate(ranked[:top_n])]

        return []

    def nurse_stats(self, nurse: Nurse) -> dict:
        """Full gamification profile for a single nurse."""
        my_ratings = [r.rating for r in self.ratings if r.nurse_id == nurse.id]
        my_events = [e for e in self.events if e.nurse_id == nurse.id]
        my_badges = self._check_and_award_badges(nurse)

        return {
            "name": nurse.name,
            "total_points": nurse.gamification_points,
            "current_streak_days": nurse.current_streak_days,
            "no_call_outs": nurse.total_no_call_outs,
            "on_time_count": nurse.total_on_time,
            "shifts_picked_up": nurse.total_shifts_picked_up,
            "swaps_completed": nurse.total_swaps_completed,
            "avg_shift_rating": round(sum(my_ratings) / len(my_ratings), 2) if my_ratings else 0.0,
            "shift_ratings_count": len(my_ratings),
            "badges": [{"name": b.name, "icon": b.icon, "desc": b.description} for b in my_badges],
            "recent_events": [
                {"type": e.event_type, "pts": e.points_awarded, "desc": e.description}
                for e in sorted(my_events, key=lambda e: e.occurred_at, reverse=True)[:5]
            ],
        }

    def _badge_count(self, nurse_id: str) -> int:
        badge_events = [e for e in self.events if e.nurse_id == nurse_id and e.event_type.startswith("badge_")]
        return len(badge_events)
