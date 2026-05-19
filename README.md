# Nursing Scheduling Agent
### Stanford Health Care — SHC/CRONA Compliant Automated Scheduling System

An AI-powered scheduling agent that generates compliant 4-week nurse schedules, processes PTO/swap/A-day requests, enforces CRONA contract rules, and gamifies staff engagement — all from the command line.

---

## Features

| Feature | Description |
|---|---|
| **Schedule Generation** | Submit department needs; agent auto-assigns nurses meeting FTE commitments, skill requirements, and policy rules |
| **Natural-Language Input** | Describe needs in plain English — Claude parses them into structured requirements |
| **FTE Compliance** | Every assignment validated against each nurse's biweekly hour commitment |
| **Policy Engine** | Hardcoded CRONA/SHC rules: cancellation order, float order, vacation limits, red-day caps |
| **PTO / Vacation Requests** | Auto-approve/deny with policy citations; checks PTO balance, seniority, summer limits |
| **A-Day Processing** | Voluntary/mandatory absent days with CRONA equity rules and 15-min acceptance window |
| **Shift Swaps** | Validates ≥3-day lead time, requires accepting nurse, flags for manager approval |
| **Gamification** | Points, badges, streaks for no call-outs, on-time, pickups, swaps, volunteer floats |
| **Shift Ratings** | 0–5 star shift rating with comments for unit quality tracking |
| **Leaderboard** | Multi-category rankings across the unit |
| **Policy Q&A** | Ask any CRONA/SHC policy question in plain language |

---

## Policy Sources Encoded

- SHC/CRONA CBA (April 2025 – March 2028)
- Staffing and Scheduling Policy (June 2019)
- Floating Policy (August 2024)
- Staffing Absent Day Procedure (April 2020)
- Pre-Approved Vacation & Education Policy (July 2022, updated Jan 2024)
- Timekeeping System WMS Policy (August 2025)

**Union contract always takes precedence over hospital policy** (as per all documents).

---

## Setup

### 1. Install dependencies
```bash
pip3 install -r requirements.txt
```

### 2. Set your Anthropic API key
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### 3. Verify the roster loaded
```bash
python3 main.py roster list
```

---

## Quick Start

### Generate a 4-week schedule
```bash
# Using plain-English needs
python3 main.py schedule generate \
  --unit K5 \
  --needs "3 RNs on day shifts, 2 on evenings, 2 on nights every day. Need 1 charge nurse each day shift."

# Default template (3 day / 2 evening / 2 night-12, charge on days)
python3 main.py schedule generate --unit K5

# From a JSON needs file
python3 main.py schedule generate --unit K5 --needs-file my_needs.json
```

### View the schedule
```bash
python3 main.py schedule view --unit K5
python3 main.py schedule view --unit K5 --date 2026-06-15
```

---

## Request Commands

### PTO request
```bash
# Single day
python3 main.py request pto --nurse-id N001 --dates 2026-06-10

# Date range
python3 main.py request pto --nurse-id N001 --dates 2026-06-10..2026-06-16

# Pre-approved vacation (annual process)
python3 main.py request pto --nurse-id N001 \
  --dates 2026-07-04..2026-07-10 \
  --type pre_approved_vacation

# Pre-approved education day
python3 main.py request pto --nurse-id N001 \
  --dates 2026-06-20 \
  --type pre_approved_education \
  --education-activity "ACLS Recertification"
```

### Shift swap
```bash
python3 main.py request swap \
  --from N001 --to N003 \
  --date 2026-06-15 \
  --shift-a <shift-id-from-schedule> \
  --shift-b <shift-id-from-schedule>
```

### Voluntary A-day (absent day)
```bash
python3 main.py request aday \
  --nurse-id N001 --date 2026-06-15 \
  --shift day --unit K5
```

### List all requests
```bash
python3 main.py request list
python3 main.py request list --nurse-id N001
python3 main.py request list --status pending
```

---

## Roster Management

```bash
# List all nurses
python3 main.py roster list

# Show one nurse's full profile + gamification stats
python3 main.py roster show --nurse-id N001

# Add a new nurse
python3 main.py roster add \
  --id N011 --name "Jordan Lee" \
  --fte 1.0 --shift-type 12hr --shift-slot day \
  --role rn --employee-type regular \
  --hire-date 2023-09-01 \
  --float-regions K5,K6 \
  --specialties oncology,telemetry \
  --can-charge \
  --pto-balance 80
```

---

## Gamification

```bash
# Record events
python3 main.py gamification record --nurse-id N001 --event no_call_out
python3 main.py gamification record --nurse-id N001 --event on_time
python3 main.py gamification record --nurse-id N001 --event shift_pickup --short-notice
python3 main.py gamification record --nurse-id N001 --event float --unit K6
python3 main.py gamification record --nurse-id N001 --event swap --with-nurse "Maria Santos"

# Rate a shift (0–5 stars)
python3 main.py gamification rate \
  --nurse-id N001 \
  --date 2026-06-15 --shift day --unit K5 \
  --rating 4 --comments "Smooth shift, good team support"

# Leaderboards
python3 main.py gamification leaderboard                          # Points
python3 main.py gamification leaderboard --category no_call_outs
python3 main.py gamification leaderboard --category shifts_picked_up
python3 main.py gamification leaderboard --category avg_shift_rating
python3 main.py gamification leaderboard --category swaps_completed
```

### Point Values
| Event | Points |
|---|---|
| No call-out | 50 |
| On time | 10 |
| Shift pickup | 75 |
| Short-notice pickup (<4 hrs) | 100 |
| Shift swap completed | 25 |
| Volunteer float | 30 |
| 7-day no-call-out streak | +50 bonus |
| 30-day streak | +200 bonus |
| Perfect pay-period attendance | 150 |

### Badges
| Badge | Criteria |
|---|---|
| 🏆 Reliable RN | Zero call-outs for ~3 months |
| 🤝 Team Player | 10 completed swaps |
| 🦸 Shift Hero | 5 short-notice pickups |
| ⏰ Early Bird | 30 on-time arrivals in a row |
| 🌊 Float Champion | Volunteered to float 10 times |
| ⭐ 5-Star Nurse | 10 five-star shift ratings |
| 🎖️ Veteran | 10+ years seniority |
| 💯 Century Club | Earned 1,000+ points |

---

## Policy Q&A

```bash
python3 main.py policy "How many vacation weeks does a nurse with 7 years get?"
python3 main.py policy "What is the float order when a unit is short-staffed?"
python3 main.py policy "Can I request an A-day 5 weeks in advance?"
python3 main.py policy "What is the cancellation order for CRONA nurses when overstaffed?"
```

---

## Key Policy Rules (Quick Reference)

### Schedule Request Priority
1. Pre-approved vacation
2. Pre-approved education days
3. Skill mix / specialty roles
4. Seniority
5. Isolated PTO or education days

### Cancellation Order (Overstaffed)
1. Voluntary requests
2. Travelers
3. Relief staff working over commitment
4. Regular staff working over commitment
5. Relief staff
6. Regular staff (inverse seniority, fewest cancelled hours this pay period)

### Float Order (Understaffed)
1. Voluntary
2. Relief over commitment
3. Regular over commitment
4. Registry
5. Travelers
6. Relief staff
7. Regular staff (including specialty roles if someone else can cover)

### Vacation Allotment (effective Jan 1, 2024)
| Seniority | Max Weeks/Year | Education Hours |
|---|---|---|
| 0–3 years | 3 weeks | 40 hours |
| 4–9 years | 4 weeks | 40 hours |
| 10+ years | 5 weeks | 40 hours |

### A-Day Rules
- Request up to **4 weeks** in advance, cutoff **8 hours** before shift
- Check website **75 minutes** before shift; **15 minutes** to accept/deny
- Equity rule: nurses with no A-day this pay period take priority
- Mandatory A-day: notify **≥60 minutes** before shift; CRONA callback after 1 hr = **1.5x pay**

### Shift Swaps
- Submit **≥3 days** before trade date
- Both nurse names and dates required in system
- Manager approval required

---

## Data Files
```
data/
  nurses.json       — Nurse roster (edit to add/update staff)
  schedules.json    — Generated schedules
  requests.json     — PTO/A-day requests
  swaps.json        — Shift swap requests
  gamification.json — Gamification events
  ratings.json      — Shift ratings
```

---

## Architecture
```
nursing_agent/
  models.py          — Pydantic data models (Nurse, Shift, Request, etc.)
  policy_engine.py   — CRONA/SHC rule enforcement (pure functions)
  scheduler.py       — Constraint-based schedule generator
  request_handler.py — PTO/swap/A-day request processing
  gamification.py    — Points, badges, leaderboards
  storage.py         — JSON persistence layer
  agent.py           — Claude API integration (natural language + explanations)
main.py              — Rich CLI interface
```

---

*Policy basis: SHC/CRONA CBA 2025–2028. Union contract takes precedence over all hospital policies.*
