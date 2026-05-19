"""
JSON-based persistence layer.
Handles read/write for nurses, schedules, requests, and gamification data.
"""

from __future__ import annotations
import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .models import (
    Nurse, SchedulePeriod, TimeOffRequest, ShiftSwapRequest,
    GamificationEvent, ShiftRating,
)


DATA_DIR = Path(__file__).parent.parent / "data"


def _encoder(obj: Any) -> Any:
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj)}")


def _load(path: Path) -> Any:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _save(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, default=_encoder, indent=2)


# ── Nurses ────────────────────────────────────────────────────────────────────

def load_nurses() -> list[Nurse]:
    raw = _load(DATA_DIR / "nurses.json")
    return [Nurse.model_validate(n) for n in raw]


def save_nurses(nurses: list[Nurse]) -> None:
    _save(DATA_DIR / "nurses.json", [n.model_dump() for n in nurses])


def get_nurse(nurse_id: str) -> Nurse | None:
    nurses = load_nurses()
    return next((n for n in nurses if n.id == nurse_id), None)


def upsert_nurse(nurse: Nurse) -> None:
    nurses = load_nurses()
    idx = next((i for i, n in enumerate(nurses) if n.id == nurse.id), None)
    if idx is not None:
        nurses[idx] = nurse
    else:
        nurses.append(nurse)
    save_nurses(nurses)


# ── Schedules ─────────────────────────────────────────────────────────────────

def load_schedules() -> list[SchedulePeriod]:
    raw = _load(DATA_DIR / "schedules.json")
    return [SchedulePeriod.model_validate(s) for s in raw]


def save_schedule(schedule: SchedulePeriod) -> None:
    schedules = load_schedules()
    idx = next((i for i, s in enumerate(schedules) if s.id == schedule.id), None)
    if idx is not None:
        schedules[idx] = schedule
    else:
        schedules.append(schedule)
    _save(DATA_DIR / "schedules.json", [s.model_dump() for s in schedules])


def get_active_schedule(unit: str, for_date: date) -> SchedulePeriod | None:
    schedules = load_schedules()
    return next(
        (s for s in schedules if s.unit == unit
         and s.start_date <= for_date <= s.end_date),
        None,
    )


# ── Time-Off Requests ─────────────────────────────────────────────────────────

def load_time_off_requests() -> list[TimeOffRequest]:
    raw = _load(DATA_DIR / "requests.json")
    return [TimeOffRequest.model_validate(r) for r in raw if "request_type" in r]


def save_time_off_request(req: TimeOffRequest) -> None:
    requests = load_time_off_requests()
    idx = next((i for i, r in enumerate(requests) if r.id == req.id), None)
    if idx is not None:
        requests[idx] = req
    else:
        requests.append(req)
    _save(DATA_DIR / "requests.json",
          [r.model_dump() for r in requests])


def load_swap_requests() -> list[ShiftSwapRequest]:
    raw = _load(DATA_DIR / "swaps.json")
    return [ShiftSwapRequest.model_validate(r) for r in raw]


def save_swap_request(req: ShiftSwapRequest) -> None:
    requests = load_swap_requests()
    idx = next((i for i, r in enumerate(requests) if r.id == req.id), None)
    if idx is not None:
        requests[idx] = req
    else:
        requests.append(req)
    _save(DATA_DIR / "swaps.json", [r.model_dump() for r in requests])


# ── Gamification ──────────────────────────────────────────────────────────────

def load_gamification_events() -> list[GamificationEvent]:
    raw = _load(DATA_DIR / "gamification.json")
    events = []
    for r in raw:
        if "event_type" in r:
            events.append(GamificationEvent.model_validate(r))
    return events


def save_gamification_event(event: GamificationEvent) -> None:
    events = load_gamification_events()
    events.append(event)
    _save(DATA_DIR / "gamification.json", [e.model_dump() for e in events])


def load_shift_ratings() -> list[ShiftRating]:
    raw = _load(DATA_DIR / "ratings.json")
    return [ShiftRating.model_validate(r) for r in raw]


def save_shift_rating(rating: ShiftRating) -> None:
    ratings = load_shift_ratings()
    ratings.append(rating)
    _save(DATA_DIR / "ratings.json", [r.model_dump() for r in ratings])
