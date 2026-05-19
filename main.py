#!/usr/bin/env python3
"""
Nursing Scheduling Agent — CLI
Stanford Health Care / CRONA-compliant automated scheduling system.

Usage:
  python main.py schedule generate --unit K5
  python main.py schedule view --unit K5
  python main.py request pto --nurse-id N001 --dates 2026-06-01,2026-06-07
  python main.py request swap --from N001 --to N002 --date 2026-06-15
  python main.py request aday --nurse-id N001 --date 2026-06-01 --shift day
  python main.py roster list
  python main.py roster add
  python main.py gamification leaderboard
  python main.py gamification profile --nurse-id N001
  python main.py policy ask "How many vacation weeks does a 5-year nurse get?"
"""

import json
import sys
from datetime import date, datetime, timedelta
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from nursing_agent.agent import NursingSchedulingAgent
from nursing_agent.models import (
    DepartmentNeedsInput, Nurse, NurseRole, ShiftSlot, ShiftType,
    EmployeeType, WeekendPattern, RequestType, SHIFT_TIMES,
)
from nursing_agent import storage

console = Console()
agent = NursingSchedulingAgent()


# ── Utilities ─────────────────────────────────────────────────────────────────

def success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")

def warn(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow]  {msg}")

def error(msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")

def info(msg: str) -> None:
    console.print(f"[bold blue]ℹ[/bold blue]  {msg}")


def parse_dates(dates_str: str) -> list[date]:
    """Accept comma-separated dates or a range: 2026-06-01..2026-06-07"""
    if ".." in dates_str:
        parts = dates_str.split("..")
        start = date.fromisoformat(parts[0].strip())
        end = date.fromisoformat(parts[1].strip())
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [date.fromisoformat(d.strip()) for d in dates_str.split(",")]


# ── CLI Root ──────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Nursing Scheduling Agent — SHC/CRONA Compliant"""
    pass


# ── Schedule Commands ─────────────────────────────────────────────────────────

@cli.group()
def schedule():
    """Schedule generation and viewing."""
    pass


@schedule.command("generate")
@click.option("--unit", required=True, help="Unit name, e.g. K5")
@click.option("--start", default=None, help="Start date YYYY-MM-DD (default: next Monday)")
@click.option("--weeks", default=4, show_default=True, help="Number of weeks (default: 4)")
@click.option(
    "--needs", default=None,
    help='Natural-language needs, e.g. "3 RNs on days, 2 on nights, 1 charge each shift"'
)
@click.option("--needs-file", default=None, type=click.Path(exists=True), help="JSON file with DepartmentNeedsInput")
def schedule_generate(unit: str, start: Optional[str], weeks: int, needs: Optional[str], needs_file: Optional[str]):
    """Generate a new schedule for a unit."""

    # Determine schedule window
    if start:
        start_date = date.fromisoformat(start)
    else:
        today = date.today()
        days_ahead = (7 - today.weekday()) % 7 or 7  # next Monday
        start_date = today + timedelta(days=days_ahead)
    end_date = start_date + timedelta(weeks=weeks) - timedelta(days=1)

    console.print(Panel(
        f"[bold]Generating schedule for unit [cyan]{unit}[/cyan][/bold]\n"
        f"Period: {start_date} → {end_date} ({weeks} weeks)",
        title="Schedule Generation",
        border_style="blue",
    ))

    # Load or parse needs
    if needs_file:
        with open(needs_file) as f:
            data = json.load(f)
        dept_needs = DepartmentNeedsInput.model_validate(data)
    elif needs:
        info("Parsing natural-language needs with Claude...")
        dept_needs = agent.parse_department_needs(needs, unit)
        if not dept_needs:
            error("Could not parse department needs. Try using --needs-file with a JSON file.")
            sys.exit(1)
        success(f"Parsed {len(dept_needs.daily_requirements)} shift requirements.")
    else:
        # Interactive mode — build default requirements
        dept_needs = _build_interactive_needs(unit, start_date, end_date)

    with console.status("[bold blue]Running scheduling engine...[/bold blue]"):
        result, narrative = agent.generate_schedule_from_needs(dept_needs)

    if not result:
        error("Schedule generation failed.")
        sys.exit(1)

    # Display result
    console.print("\n")
    console.print(Panel(narrative, title="[bold]Schedule Summary[/bold]", border_style="green"))

    if result.coverage_gaps:
        console.print("\n[bold red]Coverage Gaps:[/bold red]")
        gap_table = Table(box=box.SIMPLE)
        gap_table.add_column("Date", style="red")
        gap_table.add_column("Shift")
        gap_table.add_column("Unit")
        gap_table.add_column("Short By", style="red bold")
        gap_table.add_column("Role")
        for g in result.coverage_gaps:
            gap_table.add_row(g["date"], g["shift"], g["unit"], str(g["gap"]), g["required_role"])
        console.print(gap_table)

    if result.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for w in result.warnings[:5]:
            warn(w)

    # FTE report
    fte_issues = [v for v in result.fte_report.values() if not v["compliant"]]
    if fte_issues:
        console.print("\n[bold yellow]FTE Compliance Issues:[/bold yellow]")
        ft = Table(box=box.SIMPLE)
        ft.add_column("Nurse")
        ft.add_column("FTE")
        ft.add_column("Required Hrs")
        ft.add_column("Assigned Hrs")
        ft.add_column("Deficit", style="yellow")
        for v in fte_issues:
            ft.add_row(
                v["name"], str(v["fte"]),
                str(v["required_hours"]), str(v["assigned_hours"]),
                f"{v['deficit']:+.1f} hrs",
            )
        console.print(ft)

    success(f"Schedule saved. ID: {result.schedule.id}")


@schedule.command("view")
@click.option("--unit", required=True, help="Unit name")
@click.option("--date", "for_date", default=None, help="Date within schedule period YYYY-MM-DD")
def schedule_view(unit: str, for_date: Optional[str]):
    """View the active schedule for a unit."""
    d = date.fromisoformat(for_date) if for_date else date.today()
    sched = storage.get_active_schedule(unit, d)

    if not sched:
        warn(f"No active schedule found for unit {unit} on {d}.")
        return

    nurses = {n.id: n.name for n in storage.load_nurses()}

    console.print(Panel(
        f"[bold]Unit: [cyan]{unit}[/cyan][/bold]\n"
        f"Period: {sched.start_date} → {sched.end_date}\n"
        f"Published: {'Yes' if sched.published else '[yellow]No[/yellow]'}",
        title="Active Schedule",
        border_style="blue",
    ))

    table = Table(box=box.ROUNDED, title="Assignments")
    table.add_column("Date", style="cyan")
    table.add_column("Shift")
    table.add_column("Nurse")
    table.add_column("Unit")
    table.add_column("Hours")
    table.add_column("Notes")

    for a in sorted(sched.assignments, key=lambda x: (x.date, x.shift_slot.value)):
        notes = []
        if a.is_over_commitment:
            notes.append("[yellow]over commit[/yellow]")
        if a.is_float:
            notes.append(f"[blue]float from {a.float_from_unit}[/blue]")
        table.add_row(
            str(a.date),
            a.shift_slot.value,
            nurses.get(a.nurse_id, a.nurse_id),
            a.unit,
            str(a.hours),
            ", ".join(notes) or "—",
        )
    console.print(table)


# ── Request Commands ──────────────────────────────────────────────────────────

@cli.group()
def request():
    """Submit and review schedule requests."""
    pass


@request.command("pto")
@click.option("--nurse-id", required=True, help="Nurse ID")
@click.option("--dates", required=True, help="Dates: comma-separated or range 2026-06-01..2026-06-07")
@click.option("--type", "req_type", default="pto",
              type=click.Choice(["pto", "pre_approved_vacation", "pre_approved_education"]),
              help="Request type")
@click.option("--education-activity", default="", help="For education requests: activity name")
def request_pto(nurse_id: str, dates: str, req_type: str, education_activity: str):
    """Submit a PTO / vacation / education request."""
    date_list = parse_dates(dates)

    nurse = storage.get_nurse(nurse_id)
    if not nurse:
        error(f"Nurse {nurse_id} not found.")
        sys.exit(1)

    info(f"Processing {req_type} request for {nurse.name} on {len(date_list)} day(s)...")

    with console.status("[bold blue]Reviewing request against policy...[/bold blue]"):
        decision, message = agent.process_time_off_request(
            nurse_id=nurse_id,
            request_type_str=req_type,
            dates=date_list,
            education_activity=education_activity,
        )

    _display_decision(decision)
    console.print(Panel(message, title="Message to Nurse", border_style="blue"))


@request.command("swap")
@click.option("--from", "from_id", required=True, help="Requesting nurse ID")
@click.option("--to", "to_id", required=True, help="Accepting nurse ID")
@click.option("--date", "trade_date", required=True, help="Trade date YYYY-MM-DD")
@click.option("--shift-a", required=True, help="Requesting nurse's shift ID")
@click.option("--shift-b", required=True, help="Accepting nurse's shift ID")
def request_swap(from_id: str, to_id: str, trade_date: str, shift_a: str, shift_b: str):
    """Request a shift swap between two nurses."""
    td = date.fromisoformat(trade_date)

    n1 = storage.get_nurse(from_id)
    n2 = storage.get_nurse(to_id)
    if not n1 or not n2:
        error("One or both nurses not found.")
        sys.exit(1)

    info(f"Processing swap request: {n1.name} ↔ {n2.name} on {td}...")

    with console.status("[bold blue]Reviewing swap request...[/bold blue]"):
        decision, message = agent.process_shift_swap(
            requesting_nurse_id=from_id,
            accepting_nurse_id=to_id,
            original_shift_id=shift_a,
            swap_shift_id=shift_b,
            trade_date=td,
        )

    _display_decision(decision)
    console.print(Panel(message, title="Swap Decision", border_style="blue"))


@request.command("aday")
@click.option("--nurse-id", required=True, help="Nurse ID")
@click.option("--date", "req_date", required=True, help="Requested date YYYY-MM-DD")
@click.option("--shift", "shift_slot", required=True,
              type=click.Choice(["day", "evening", "night_8", "night_12"]),
              help="Shift slot")
@click.option("--unit", required=True, help="Unit")
def request_aday(nurse_id: str, req_date: str, shift_slot: str, unit: str):
    """Request a voluntary Absent (A) day."""
    d = date.fromisoformat(req_date)
    slot = ShiftSlot(shift_slot)

    nurse = storage.get_nurse(nurse_id)
    if not nurse:
        error(f"Nurse {nurse_id} not found.")
        sys.exit(1)

    info(f"Processing A-day request for {nurse.name} on {d} ({shift_slot} shift)...")

    with console.status("[bold blue]Evaluating A-day request...[/bold blue]"):
        decision, message = agent.process_a_day_request(
            nurse_id=nurse_id,
            requested_date=d,
            shift_slot=slot,
            unit=unit,
        )

    _display_decision(decision)
    console.print(Panel(message, title="A-Day Decision", border_style="blue"))


@request.command("list")
@click.option("--nurse-id", default=None, help="Filter by nurse ID")
@click.option("--status", default=None,
              type=click.Choice(["pending", "approved", "denied"]),
              help="Filter by status")
def request_list(nurse_id: Optional[str], status: Optional[str]):
    """List time-off requests."""
    requests = storage.load_time_off_requests()
    nurses = {n.id: n.name for n in storage.load_nurses()}

    if nurse_id:
        requests = [r for r in requests if r.nurse_id == nurse_id]
    if status:
        requests = [r for r in requests if r.status.value == status]

    if not requests:
        info("No requests found.")
        return

    table = Table(box=box.ROUNDED, title="Time-Off Requests")
    table.add_column("ID", style="dim")
    table.add_column("Nurse")
    table.add_column("Type")
    table.add_column("Dates")
    table.add_column("Status")
    table.add_column("Reason")

    status_colors = {"approved": "green", "denied": "red", "pending": "yellow"}
    for r in sorted(requests, key=lambda x: x.submitted_at, reverse=True)[:20]:
        color = status_colors.get(r.status.value, "white")
        table.add_row(
            r.id[:8],
            nurses.get(r.nurse_id, r.nurse_id),
            r.request_type.value,
            f"{min(r.dates)} – {max(r.dates)}" if len(r.dates) > 1 else str(r.dates[0]),
            f"[{color}]{r.status.value}[/{color}]",
            r.decision_reason[:50] + ("…" if len(r.decision_reason) > 50 else ""),
        )
    console.print(table)


# ── Roster Commands ───────────────────────────────────────────────────────────

@cli.group()
def roster():
    """Manage the nurse roster."""
    pass


@roster.command("list")
def roster_list():
    """Display all nurses on the roster."""
    nurses = storage.load_nurses()
    if not nurses:
        warn("No nurses in roster. Use 'roster add' to add nurses.")
        return

    table = Table(box=box.ROUNDED, title=f"Nurse Roster ({len(nurses)} staff)")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Role")
    table.add_column("Type")
    table.add_column("FTE")
    table.add_column("Shift")
    table.add_column("Seniority")
    table.add_column("Float Regions")
    table.add_column("Points", style="yellow")

    for n in sorted(nurses, key=lambda x: x.seniority_years, reverse=True):
        table.add_row(
            n.id,
            n.name,
            n.role.value,
            n.employee_type.value,
            str(n.fte),
            n.shift_type.value,
            f"{n.seniority_years:.1f} yrs",
            ", ".join(n.float_regions) or "—",
            str(n.gamification_points),
        )
    console.print(table)


@roster.command("add")
@click.option("--id", "nurse_id", required=True, help="Unique nurse ID, e.g. N015")
@click.option("--name", required=True, help="Full name")
@click.option("--fte", default=1.0, show_default=True, type=float, help="FTE commitment")
@click.option("--shift-type", default="12hr", type=click.Choice(["8hr", "12hr"]), help="Shift type")
@click.option("--shift-slot", default="day", type=click.Choice(["day", "evening", "night_8", "night_12"]))
@click.option("--role", default="rn", type=click.Choice(["rn", "charge", "resource", "na", "us"]))
@click.option("--employee-type", default="regular",
              type=click.Choice(["regular", "relief", "traveler", "registry"]))
@click.option("--hire-date", required=True, help="Hire date YYYY-MM-DD")
@click.option("--float-regions", default="", help="Comma-separated float regions, e.g. K5,K6,K7")
@click.option("--specialties", default="", help="Comma-separated specialties")
@click.option("--can-charge/--no-charge", default=False)
@click.option("--pto-balance", default=80.0, type=float, help="Initial PTO hours balance")
def roster_add(
    nurse_id, name, fte, shift_type, shift_slot, role, employee_type,
    hire_date, float_regions, specialties, can_charge, pto_balance,
):
    """Add a new nurse to the roster."""
    existing = storage.get_nurse(nurse_id)
    if existing:
        error(f"Nurse {nurse_id} already exists: {existing.name}")
        sys.exit(1)

    nurse = Nurse(
        id=nurse_id,
        name=name,
        fte=fte,
        shift_type=ShiftType(shift_type),
        shift_slot=ShiftSlot(shift_slot),
        role=NurseRole(role),
        employee_type=EmployeeType(employee_type),
        hire_date=date.fromisoformat(hire_date),
        float_regions=[r.strip() for r in float_regions.split(",") if r.strip()],
        specialties=[s.strip() for s in specialties.split(",") if s.strip()],
        can_charge=can_charge,
        pto_hours_balance=pto_balance,
    )
    storage.upsert_nurse(nurse)
    success(f"Added nurse {nurse.name} ({nurse_id}) — FTE {fte}, {shift_type} {shift_slot} shift.")


@roster.command("show")
@click.option("--nurse-id", required=True)
def roster_show(nurse_id: str):
    """Show detailed profile for one nurse."""
    nurse = storage.get_nurse(nurse_id)
    if not nurse:
        error(f"Nurse {nurse_id} not found.")
        sys.exit(1)

    profile = agent.get_nurse_gamification_profile(nurse_id)

    console.print(Panel(
        f"[bold]{nurse.name}[/bold] ({nurse.id})\n"
        f"Role: {nurse.role.value}  |  Type: {nurse.employee_type.value}\n"
        f"FTE: {nurse.fte}  |  Shift: {nurse.shift_type.value} {nurse.shift_slot.value}\n"
        f"Seniority: {nurse.seniority_years:.1f} yrs (hired {nurse.hire_date})\n"
        f"PTO Balance: {nurse.pto_hours_balance:.0f} hrs  |  Edu Balance: {nurse.education_hours_balance:.0f} hrs\n"
        f"Float Regions: {', '.join(nurse.float_regions) or 'none'}\n"
        f"Specialties: {', '.join(nurse.specialties) or 'none'}\n"
        f"Can Charge: {'Yes' if nurse.can_charge else 'No'}  |  "
        f"Float Exempt: {'Yes' if nurse.is_float_exempt else 'No'}\n"
        f"Max Vacation Weeks: {nurse.max_pre_approved_vacation_weeks}",
        title="Nurse Profile",
        border_style="blue",
    ))

    if profile:
        console.print(Panel(
            f"Points: [yellow bold]{profile['total_points']}[/yellow bold]  |  "
            f"Streak: {profile['current_streak_days']} days\n"
            f"No Call-Outs: {profile['no_call_outs']}  |  "
            f"On-Time: {profile['on_time_count']}  |  "
            f"Pickups: {profile['shifts_picked_up']}  |  "
            f"Swaps: {profile['swaps_completed']}\n"
            f"Avg Shift Rating: {'⭐' * round(profile['avg_shift_rating'])} ({profile['avg_shift_rating']})\n"
            f"Badges: {' '.join(b['icon'] + ' ' + b['name'] for b in profile['badges']) or 'None yet'}",
            title="Gamification Profile",
            border_style="yellow",
        ))


# ── Gamification Commands ─────────────────────────────────────────────────────

@cli.group()
def gamification():
    """Points, badges, and leaderboards."""
    pass


@gamification.command("leaderboard")
@click.option("--category", default="total_points",
              type=click.Choice(["total_points", "no_call_outs", "shifts_picked_up",
                                  "swaps_completed", "avg_shift_rating"]),
              help="Leaderboard category")
@click.option("--top", default=10, help="Number of entries to show")
def gamification_leaderboard(category: str, top: int):
    """Show the gamification leaderboard."""
    board = agent.get_leaderboard(category, top)
    if not board:
        warn("No data available for leaderboard.")
        return

    medals = ["🥇", "🥈", "🥉"]
    table = Table(box=box.ROUNDED, title=f"Leaderboard: {category.replace('_', ' ').title()}")
    table.add_column("Rank")
    table.add_column("Nurse", style="bold")
    # All metric columns (everything except rank and name)
    metric_keys = [k for k in board[0].keys() if k not in ("rank", "name")]
    for k in metric_keys:
        table.add_column(k.replace("_", " ").title(), style="yellow")

    for entry in board:
        rank = entry["rank"]
        medal = medals[rank - 1] if rank <= 3 else f"#{rank}"
        name = entry["name"]
        row_vals = [str(entry[k]) for k in metric_keys]
        table.add_row(medal, name, *row_vals)

    console.print(table)


@gamification.command("record")
@click.option("--nurse-id", required=True)
@click.option("--event", required=True,
              type=click.Choice(["no_call_out", "on_time", "shift_pickup", "swap", "float"]))
@click.option("--date", "event_date", default=None, help="YYYY-MM-DD (default: today)")
@click.option("--shift", "shift_slot", default="day",
              type=click.Choice(["day", "evening", "night_8", "night_12"]))
@click.option("--short-notice", is_flag=True, default=False, help="For shift_pickup: was it short notice?")
@click.option("--with-nurse", default="", help="For swap: other nurse name")
@click.option("--unit", default="", help="For float: target unit")
def gamification_record(nurse_id, event, event_date, shift_slot, short_notice, with_nurse, unit):
    """Record a gamification event for a nurse."""
    d = date.fromisoformat(event_date) if event_date else date.today()
    slot = ShiftSlot(shift_slot)

    nurses = storage.load_nurses()
    nurse = next((n for n in nurses if n.id == nurse_id), None)
    if not nurse:
        error(f"Nurse {nurse_id} not found.")
        sys.exit(1)

    from nursing_agent.gamification import GamificationEngine
    gam = GamificationEngine()
    ev = None

    if event == "no_call_out":
        ev = gam.award_no_call_out(nurse, d)
    elif event == "on_time":
        ev = gam.award_on_time(nurse, d)
    elif event == "shift_pickup":
        ev = gam.award_shift_pickup(nurse, d, slot, is_short_notice=short_notice)
    elif event == "swap":
        ev = gam.award_swap_completed(nurse, d, with_nurse or "a colleague")
    elif event == "float":
        ev = gam.award_volunteer_float(nurse, d, unit or "another unit")

    if ev:
        storage.save_gamification_event(ev)
        storage.upsert_nurse(nurse)
        success(f"+{ev.points_awarded} pts for {nurse.name}: {ev.description}")


@gamification.command("rate")
@click.option("--nurse-id", required=True)
@click.option("--date", "shift_date", required=True, help="YYYY-MM-DD")
@click.option("--shift", required=True, type=click.Choice(["day", "evening", "night_8", "night_12"]))
@click.option("--unit", required=True)
@click.option("--rating", required=True, type=click.IntRange(0, 5))
@click.option("--comments", default="")
def gamification_rate(nurse_id, shift_date, shift, unit, rating, comments):
    """Rate how a shift went (0–5 stars)."""
    d = date.fromisoformat(shift_date)
    slot = ShiftSlot(shift)
    sr = agent.record_shift_rating(nurse_id, d, slot, unit, rating, comments)
    stars = "⭐" * rating or "☆☆☆☆☆"
    success(f"Shift rating recorded: {stars} ({rating}/5) for nurse {nurse_id} on {d}.")


# ── Policy Query ──────────────────────────────────────────────────────────────

@cli.command("policy")
@click.argument("question")
def policy_question(question: str):
    """Ask a policy or contract question."""
    info("Consulting SHC/CRONA policy knowledge...")
    with console.status("[bold blue]Thinking...[/bold blue]"):
        answer = agent.answer_policy_question(question)
    console.print(Panel(answer, title="[bold]Policy Answer[/bold]", border_style="green"))


# ── Internal Helpers ──────────────────────────────────────────────────────────

def _display_decision(decision) -> None:
    if decision.approved:
        console.print(Panel(
            f"[bold green]APPROVED[/bold green]\n{decision.reason}",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold red]DENIED[/bold red]\n{decision.reason}",
            border_style="red",
        ))
    if decision.policy_references:
        info(f"Policy references: {', '.join(decision.policy_references)}")


def _build_interactive_needs(unit: str, start: date, end: date) -> DepartmentNeedsInput:
    """Build a default set of daily requirements when no --needs provided."""
    from nursing_agent.models import ShiftRequirement

    reqs = []
    current = start
    while current <= end:
        # Default: 3 RNs day, 2 evening, 2 night; 1 charge each shift
        for slot, count, charge in [
            (ShiftSlot.DAY, 3, True),
            (ShiftSlot.EVENING, 2, False),
            (ShiftSlot.NIGHT_12, 2, False),
        ]:
            reqs.append(ShiftRequirement(
                date=current,
                shift_slot=slot,
                unit=unit,
                count=count,
                required_role=NurseRole.RN,
                charge_needed=charge,
            ))
        current += timedelta(days=1)

    return DepartmentNeedsInput(
        unit=unit,
        schedule_start=start,
        schedule_end=end,
        daily_requirements=reqs,
        notes="Default: 3 day / 2 evening / 2 night-12 RNs, charge on days.",
    )


if __name__ == "__main__":
    cli()
