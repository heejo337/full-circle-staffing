"""
NurseScheduler — Marketing Landing Page
readyon.com-style B2B SaaS design
"""

import streamlit as st

st.set_page_config(
    page_title="NurseScheduler — Frontline scheduling that runs itself",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Hide Streamlit chrome ──────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
[data-testid="stSidebar"] {display: none;}
[data-testid="stAppViewBlockContainer"] {
    padding: 0 !important;
    max-width: 100% !important;
}
.block-container {
    padding: 0 !important;
    max-width: 100% !important;
}
.stApp { background: #FFFFFF; }
</style>
""", unsafe_allow_html=True)

# ── Full landing page HTML ─────────────────────────────────────────────────────
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }

/* ── NAV ── */
.ns-nav {
    position: sticky; top: 0; z-index: 100;
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid #E2E8F0;
    padding: 0 48px;
    height: 64px;
    display: flex; align-items: center; justify-content: space-between;
}
.ns-logo {
    font-size: 18px; font-weight: 800; color: #0F172A;
    display: flex; align-items: center; gap: 8px; text-decoration: none;
}
.ns-logo-dot { color: #2563EB; }
.ns-nav-links {
    display: flex; gap: 32px; list-style: none;
}
.ns-nav-links a {
    font-size: 14px; font-weight: 500; color: #64748B;
    text-decoration: none; transition: color 0.2s;
}
.ns-nav-links a:hover { color: #0F172A; }
.ns-nav-cta {
    background: #0F172A; color: #FFFFFF !important;
    padding: 10px 20px; border-radius: 8px;
    font-size: 14px; font-weight: 600;
    text-decoration: none; transition: background 0.2s;
}
.ns-nav-cta:hover { background: #1E293B !important; }

/* ── HERO ── */
.ns-hero {
    background: #0F172A;
    padding: 100px 48px 80px;
    text-align: center;
}
.ns-hero-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(37,99,235,0.15);
    border: 1px solid rgba(37,99,235,0.3);
    color: #93C5FD; font-size: 13px; font-weight: 500;
    padding: 6px 14px; border-radius: 100px; margin-bottom: 32px;
}
.ns-hero-badge-dot {
    width: 6px; height: 6px; background: #3B82F6;
    border-radius: 50%; display: inline-block;
}
.ns-hero h1 {
    font-size: clamp(40px, 6vw, 72px);
    font-weight: 900; color: #FFFFFF;
    line-height: 1.08; letter-spacing: -2px;
    max-width: 900px; margin: 0 auto 24px;
}
.ns-hero h1 em { color: #3B82F6; font-style: normal; }
.ns-hero-sub {
    font-size: 18px; color: #94A3B8; line-height: 1.6;
    max-width: 560px; margin: 0 auto 40px; font-weight: 400;
}
.ns-hero-actions {
    display: flex; gap: 12px; justify-content: center; flex-wrap: wrap;
    margin-bottom: 72px;
}
.ns-btn-primary {
    background: #2563EB; color: #FFFFFF;
    padding: 14px 28px; border-radius: 10px;
    font-size: 15px; font-weight: 600;
    text-decoration: none; transition: background 0.2s;
    display: inline-flex; align-items: center; gap: 8px;
}
.ns-btn-primary:hover { background: #1D4ED8; }
.ns-btn-ghost {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.15);
    color: #CBD5E1;
    padding: 14px 28px; border-radius: 10px;
    font-size: 15px; font-weight: 500;
    text-decoration: none; transition: all 0.2s;
    display: inline-flex; align-items: center; gap: 8px;
}
.ns-btn-ghost:hover { background: rgba(255,255,255,0.12); color: #fff; }

/* ── STATS BAR ── */
.ns-stats {
    border-top: 1px solid rgba(255,255,255,0.08);
    padding-top: 48px;
    display: grid; grid-template-columns: repeat(4, 1fr);
    max-width: 800px; margin: 0 auto; gap: 0;
}
.ns-stat {
    text-align: center;
    padding: 0 24px;
    border-right: 1px solid rgba(255,255,255,0.08);
}
.ns-stat:last-child { border-right: none; }
.ns-stat-val {
    font-size: 36px; font-weight: 800; color: #FFFFFF;
    letter-spacing: -1px; display: block;
}
.ns-stat-label {
    font-size: 13px; color: #64748B; margin-top: 4px;
    font-weight: 400;
}

/* ── SECTION SHARED ── */
.ns-section {
    padding: 96px 48px;
    max-width: 1100px; margin: 0 auto;
}
.ns-section-full {
    padding: 96px 48px;
    background: #F8FAFC;
}
.ns-section-full-inner {
    max-width: 1100px; margin: 0 auto;
}
.ns-section-dark {
    padding: 96px 48px;
    background: #0F172A;
}
.ns-section-dark-inner {
    max-width: 1100px; margin: 0 auto;
}

.ns-section-num {
    font-size: 12px; font-weight: 700; letter-spacing: 3px;
    color: #94A3B8; text-transform: uppercase; margin-bottom: 16px;
}
.ns-section-num-dark {
    font-size: 12px; font-weight: 700; letter-spacing: 3px;
    color: #475569; text-transform: uppercase; margin-bottom: 16px;
}
.ns-section h2 {
    font-size: clamp(28px, 4vw, 44px); font-weight: 800;
    color: #0F172A; letter-spacing: -1.5px; line-height: 1.1;
    margin-bottom: 16px;
}
.ns-section-full-inner h2 {
    font-size: clamp(28px, 4vw, 44px); font-weight: 800;
    color: #0F172A; letter-spacing: -1.5px; line-height: 1.1;
    margin-bottom: 16px;
}
.ns-section-dark-inner h2 {
    font-size: clamp(28px, 4vw, 44px); font-weight: 800;
    color: #FFFFFF; letter-spacing: -1.5px; line-height: 1.1;
    margin-bottom: 16px;
}
.ns-lead { font-size: 18px; color: #64748B; line-height: 1.6; max-width: 560px; }
.ns-lead-dark { font-size: 18px; color: #94A3B8; line-height: 1.6; max-width: 560px; }

/* ── PROBLEM ── */
.ns-problem-grid {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 24px; margin-top: 56px;
}
.ns-problem-card {
    background: #FFF8F8;
    border: 1px solid #FECACA;
    border-radius: 16px; padding: 32px;
}
.ns-problem-icon { font-size: 28px; margin-bottom: 16px; }
.ns-problem-card h3 {
    font-size: 17px; font-weight: 700; color: #0F172A; margin-bottom: 8px;
}
.ns-problem-card p { font-size: 14px; color: #64748B; line-height: 1.6; }

/* ── HOW IT WORKS ── */
.ns-steps {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0; margin-top: 56px;
    position: relative;
}
.ns-step {
    padding: 0 24px;
    border-right: 1px solid #E2E8F0;
    position: relative;
}
.ns-step:last-child { border-right: none; }
.ns-step-num {
    font-size: 48px; font-weight: 900; color: #E2E8F0;
    line-height: 1; margin-bottom: 16px;
    font-variant-numeric: tabular-nums;
}
.ns-step h3 {
    font-size: 16px; font-weight: 700; color: #0F172A; margin-bottom: 8px;
}
.ns-step p { font-size: 14px; color: #64748B; line-height: 1.6; }

/* ── FEATURES ── */
.ns-features-grid {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 2px; margin-top: 56px;
    background: #E2E8F0; border-radius: 16px; overflow: hidden;
}
.ns-feature {
    background: #FFFFFF;
    padding: 36px 32px;
}
.ns-feature-icon {
    width: 44px; height: 44px; border-radius: 10px;
    background: #EFF6FF; display: flex; align-items: center;
    justify-content: center; font-size: 20px; margin-bottom: 20px;
}
.ns-feature h3 {
    font-size: 16px; font-weight: 700; color: #0F172A; margin-bottom: 8px;
}
.ns-feature p { font-size: 14px; color: #64748B; line-height: 1.6; }

/* ── COMPLIANCE ── */
.ns-compliance-grid {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 16px; margin-top: 56px;
}
.ns-compliance-card {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px; padding: 24px;
    display: flex; align-items: flex-start; gap: 14px;
}
.ns-compliance-check {
    width: 20px; height: 20px; background: #22C55E;
    border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font-size: 11px; flex-shrink: 0;
    margin-top: 2px;
}
.ns-compliance-card h4 {
    font-size: 14px; font-weight: 600; color: #F1F5F9; margin-bottom: 4px;
}
.ns-compliance-card p { font-size: 13px; color: #64748B; }

/* ── GAMIFICATION ── */
.ns-gami-grid {
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 48px; margin-top: 56px; align-items: center;
}
.ns-gami-points {
    background: #0F172A;
    border-radius: 16px; padding: 32px; color: #FFF;
}
.ns-gami-points h4 {
    font-size: 13px; font-weight: 600; color: #64748B;
    text-transform: uppercase; letter-spacing: 2px; margin-bottom: 20px;
}
.ns-point-row {
    display: flex; justify-content: space-between;
    padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.06);
    font-size: 14px;
}
.ns-point-row:last-child { border-bottom: none; }
.ns-point-label { color: #CBD5E1; }
.ns-point-val { color: #3B82F6; font-weight: 700; }
.ns-badges { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }
.ns-badge {
    background: rgba(37,99,235,0.1); border: 1px solid rgba(37,99,235,0.2);
    color: #93C5FD; padding: 6px 12px; border-radius: 100px; font-size: 13px;
}

.ns-gami-text h3 {
    font-size: 28px; font-weight: 800; color: #0F172A;
    letter-spacing: -1px; margin-bottom: 16px;
}
.ns-gami-text p { font-size: 16px; color: #64748B; line-height: 1.7; margin-bottom: 24px; }
.ns-gami-bullets { list-style: none; }
.ns-gami-bullets li {
    display: flex; align-items: flex-start; gap: 10px;
    font-size: 14px; color: #475569; margin-bottom: 12px; line-height: 1.5;
}
.ns-bullet-icon { color: #22C55E; font-weight: 700; flex-shrink: 0; }

/* ── CTA ── */
.ns-cta-section {
    background: #2563EB;
    padding: 96px 48px;
    text-align: center;
}
.ns-cta-section h2 {
    font-size: clamp(28px, 4vw, 48px);
    font-weight: 900; color: #FFFFFF;
    letter-spacing: -1.5px; margin-bottom: 16px;
}
.ns-cta-section p {
    font-size: 18px; color: rgba(255,255,255,0.75);
    margin-bottom: 40px; max-width: 500px; margin-left: auto; margin-right: auto;
}
.ns-btn-white {
    background: #FFFFFF; color: #2563EB;
    padding: 16px 32px; border-radius: 10px;
    font-size: 16px; font-weight: 700;
    text-decoration: none; transition: all 0.2s;
    display: inline-flex; align-items: center; gap: 8px;
}
.ns-btn-white:hover { background: #F8FAFC; }

/* ── FOOTER ── */
.ns-footer {
    background: #0F172A;
    padding: 48px;
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 16px;
}
.ns-footer-logo {
    font-size: 16px; font-weight: 800; color: #FFFFFF;
}
.ns-footer-copy {
    font-size: 13px; color: #475569;
}
.ns-footer-links { display: flex; gap: 24px; }
.ns-footer-links a {
    font-size: 13px; color: #475569; text-decoration: none;
}
.ns-footer-links a:hover { color: #94A3B8; }

/* Responsive */
@media (max-width: 768px) {
    .ns-nav { padding: 0 20px; }
    .ns-nav-links { display: none; }
    .ns-hero { padding: 60px 20px; }
    .ns-stats { grid-template-columns: repeat(2, 1fr); gap: 24px; }
    .ns-stat { border-right: none; }
    .ns-section, .ns-section-full, .ns-section-dark { padding: 60px 20px; }
    .ns-problem-grid, .ns-compliance-grid { grid-template-columns: 1fr; }
    .ns-steps { grid-template-columns: repeat(2, 1fr); }
    .ns-step { border-right: none; margin-bottom: 32px; }
    .ns-features-grid { grid-template-columns: 1fr; }
    .ns-gami-grid { grid-template-columns: 1fr; }
    .ns-footer { flex-direction: column; text-align: center; }
}
</style>

<!-- NAV -->
<nav class="ns-nav">
    <a href="#" class="ns-logo">🏥 NurseScheduler<span class="ns-logo-dot">.</span></a>
    <ul class="ns-nav-links">
        <li><a href="#how-it-works">How it works</a></li>
        <li><a href="#features">Features</a></li>
        <li><a href="#compliance">Compliance</a></li>
        <li><a href="#gamification">Engagement</a></li>
    </ul>
    <a href="/Scheduling_Tool" class="ns-nav-cta">Launch App →</a>
</nav>

<!-- HERO -->
<div class="ns-hero">
    <div class="ns-hero-badge">
        <span class="ns-hero-badge-dot"></span>
        SHC / CRONA CBA 2025–2028 Compliant
    </div>
    <h1>Frontline nursing scheduling<br>that <em>runs itself.</em></h1>
    <p class="ns-hero-sub">AI-powered, policy-compliant scheduling for inpatient nursing units. Generate 4-week schedules in seconds, automate PTO and A-day requests, and keep your unit fully staffed.</p>
    <div class="ns-hero-actions">
        <a href="/Scheduling_Tool" class="ns-btn-primary">Launch App →</a>
        <a href="#how-it-works" class="ns-btn-ghost">See how it works ↓</a>
    </div>
    <div class="ns-stats">
        <div class="ns-stat">
            <span class="ns-stat-val">&lt;30s</span>
            <span class="ns-stat-label">4-week schedule generated</span>
        </div>
        <div class="ns-stat">
            <span class="ns-stat-val">10+</span>
            <span class="ns-stat-label">CRONA rules enforced</span>
        </div>
        <div class="ns-stat">
            <span class="ns-stat-val">6</span>
            <span class="ns-stat-label">Policy sources encoded</span>
        </div>
        <div class="ns-stat">
            <span class="ns-stat-val">100%</span>
            <span class="ns-stat-label">FTE compliant</span>
        </div>
    </div>
</div>

<!-- SECTION 01 — THE PROBLEM -->
<div>
<div class="ns-section">
    <div class="ns-section-num">01 — THE PROBLEM</div>
    <h2>Manual scheduling is<br>breaking your team.</h2>
    <p class="ns-lead">Nursing managers spend 8+ hours per week on scheduling. Errors lead to policy violations, union grievances, and staff burnout.</p>
    <div class="ns-problem-grid">
        <div class="ns-problem-card">
            <div class="ns-problem-icon">⏱️</div>
            <h3>Hours wasted every week</h3>
            <p>Manual schedule-building is tedious, error-prone, and consumes time that should go to patient care and team support.</p>
        </div>
        <div class="ns-problem-card">
            <div class="ns-problem-icon">⚠️</div>
            <h3>CRONA violations cost you</h3>
            <p>Missing float order rules, cancellation sequence, or vacation limits exposes the unit to grievances and remediation costs.</p>
        </div>
        <div class="ns-problem-card">
            <div class="ns-problem-icon">🔥</div>
            <h3>Burnout drives turnover</h3>
            <p>Unfair scheduling—real or perceived—erodes trust. Nurses leave units where they feel the schedule isn't managed equitably.</p>
        </div>
    </div>
</div>
</div>

<!-- SECTION 02 — HOW IT WORKS -->
<div class="ns-section-full" id="how-it-works">
<div class="ns-section-full-inner">
    <div class="ns-section-num">02 — HOW IT WORKS</div>
    <h2>From needs to schedule<br>in four steps.</h2>
    <p class="ns-lead">Describe your staffing needs in plain English. The agent handles the rest — assignment, validation, and delivery.</p>
    <div class="ns-steps">
        <div class="ns-step">
            <div class="ns-step-num">01</div>
            <h3>Submit staffing needs</h3>
            <p>Enter daily RN counts, shift types, charge nurse requirements, and special skills — in plain English or structured form.</p>
        </div>
        <div class="ns-step">
            <div class="ns-step-num">02</div>
            <h3>AI assigns nurses</h3>
            <p>The scheduler fills requirements in priority order — FTE compliance first, then seniority, then availability and specialty match.</p>
        </div>
        <div class="ns-step">
            <div class="ns-step-num">03</div>
            <h3>Policy engine validates</h3>
            <p>Every assignment is checked against CRONA rules, float order, cancellation sequence, weekend commitments, and PTO locks.</p>
        </div>
        <div class="ns-step">
            <div class="ns-step-num">04</div>
            <h3>Schedule delivered</h3>
            <p>A complete 4-week schedule with FTE compliance report, coverage gap warnings, and a plain-language narrative summary.</p>
        </div>
    </div>
</div>
</div>

<!-- SECTION 03 — FEATURES -->
<div id="features">
<div class="ns-section">
    <div class="ns-section-num">03 — FEATURES</div>
    <h2>Everything your unit needs,<br>nothing it doesn't.</h2>
    <p class="ns-lead">Built specifically for CRONA-covered inpatient nursing units at Stanford Health Care.</p>
    <div class="ns-features-grid">
        <div class="ns-feature">
            <div class="ns-feature-icon">📅</div>
            <h3>Smart Schedule Generation</h3>
            <p>Constraint-based 4-week scheduling that meets every nurse's FTE commitment while respecting approved time off and skill requirements.</p>
        </div>
        <div class="ns-feature">
            <div class="ns-feature-icon">✅</div>
            <h3>PTO &amp; Request Processing</h3>
            <p>Automatic approve/deny for PTO, pre-approved vacation, education days, and A-days — with policy citations in every decision.</p>
        </div>
        <div class="ns-feature">
            <div class="ns-feature-icon">🔄</div>
            <h3>Shift Swap Management</h3>
            <p>Validates ≥3-day lead time, confirms both nurses, flags for manager approval, and executes swaps in the schedule automatically.</p>
        </div>
        <div class="ns-feature">
            <div class="ns-feature-icon">🌊</div>
            <h3>Float &amp; Cancellation Orders</h3>
            <p>When units are over or under staffed, the system applies the exact CRONA-mandated float and cancellation order automatically.</p>
        </div>
        <div class="ns-feature">
            <div class="ns-feature-icon">💬</div>
            <h3>Natural Language Interface</h3>
            <p>Ask any scheduling or policy question in plain English. Claude parses your needs and explains every decision in plain language.</p>
        </div>
        <div class="ns-feature">
            <div class="ns-feature-icon">📊</div>
            <h3>FTE Compliance Reports</h3>
            <p>Per-nurse reports showing required vs. assigned hours, deficit tracking, and under-scheduling warnings for every pay period.</p>
        </div>
    </div>
</div>
</div>

<!-- SECTION 04 — COMPLIANCE -->
<div class="ns-section-dark" id="compliance">
<div class="ns-section-dark-inner">
    <div class="ns-section-num-dark">04 — POLICY COMPLIANCE</div>
    <h2>Every rule. Every time.</h2>
    <p class="ns-lead-dark">Six SHC policy sources are encoded directly into the scheduling engine. The union contract always takes precedence.</p>
    <div class="ns-compliance-grid">
        <div class="ns-compliance-card">
            <div class="ns-compliance-check">✓</div>
            <div>
                <h4>SHC/CRONA CBA 2025–2028</h4>
                <p>Full union contract: cancellation order, float order, A-day equity, seniority rules, weekend commitments.</p>
            </div>
        </div>
        <div class="ns-compliance-card">
            <div class="ns-compliance-check">✓</div>
            <div>
                <h4>Staffing &amp; Scheduling Policy</h4>
                <p>June 2019 policy: shift assignment priority, charge nurse requirements, relief staff rules.</p>
            </div>
        </div>
        <div class="ns-compliance-card">
            <div class="ns-compliance-check">✓</div>
            <div>
                <h4>Floating Policy (Aug 2024)</h4>
                <p>Updated float order: voluntary → relief over commitment → regular over commitment → registry → travelers.</p>
            </div>
        </div>
        <div class="ns-compliance-card">
            <div class="ns-compliance-check">✓</div>
            <div>
                <h4>Vacation &amp; Education Policy</h4>
                <p>Jan 2024 update: seniority-based vacation allotments, summer caps, education day limits.</p>
            </div>
        </div>
        <div class="ns-compliance-card">
            <div class="ns-compliance-check">✓</div>
            <div>
                <h4>Absent Day Procedure (Apr 2020)</h4>
                <p>A-day equity rule, 4-week advance max, 75-min website check, 15-min acceptance window.</p>
            </div>
        </div>
        <div class="ns-compliance-card">
            <div class="ns-compliance-check">✓</div>
            <div>
                <h4>WMS Timekeeping (Aug 2025)</h4>
                <p>Shift time definitions, punch rules, overtime triggers, and pay period boundaries.</p>
            </div>
        </div>
    </div>
</div>
</div>

<!-- SECTION 05 — GAMIFICATION -->
<div id="gamification">
<div class="ns-section">
    <div class="ns-section-num">05 — STAFF ENGAGEMENT</div>
    <h2>Reward the behaviors<br>that matter most.</h2>
    <p class="ns-lead">A built-in points and badge system that recognizes reliability, teamwork, and flexibility — driving retention and morale.</p>
    <div class="ns-gami-grid">
        <div class="ns-gami-points">
            <h4>Point Values</h4>
            <div class="ns-point-row"><span class="ns-point-label">No call-out</span><span class="ns-point-val">+50 pts</span></div>
            <div class="ns-point-row"><span class="ns-point-label">Short-notice shift pickup (&lt;4 hrs)</span><span class="ns-point-val">+100 pts</span></div>
            <div class="ns-point-row"><span class="ns-point-label">Shift pickup</span><span class="ns-point-val">+75 pts</span></div>
            <div class="ns-point-row"><span class="ns-point-label">Volunteer float</span><span class="ns-point-val">+30 pts</span></div>
            <div class="ns-point-row"><span class="ns-point-label">Shift swap completed</span><span class="ns-point-val">+25 pts</span></div>
            <div class="ns-point-row"><span class="ns-point-label">On-time arrival</span><span class="ns-point-val">+10 pts</span></div>
            <div class="ns-point-row"><span class="ns-point-label">30-day no-call-out streak</span><span class="ns-point-val">+200 pts</span></div>
            <div style="margin-top: 20px;">
                <div style="font-size:13px; font-weight:600; color:#64748B; text-transform:uppercase; letter-spacing:2px; margin-bottom:12px;">Badges</div>
                <div class="ns-badges">
                    <span class="ns-badge">🏆 Reliable RN</span>
                    <span class="ns-badge">🤝 Team Player</span>
                    <span class="ns-badge">🦸 Shift Hero</span>
                    <span class="ns-badge">🌊 Float Champion</span>
                    <span class="ns-badge">⭐ 5-Star Nurse</span>
                    <span class="ns-badge">💯 Century Club</span>
                </div>
            </div>
        </div>
        <div class="ns-gami-text">
            <h3>Turn good nursing<br>into recognition.</h3>
            <p>The gamification system runs automatically alongside scheduling — no extra work for managers.</p>
            <ul class="ns-gami-bullets">
                <li><span class="ns-bullet-icon">✓</span> Points awarded automatically for schedule events</li>
                <li><span class="ns-bullet-icon">✓</span> 0–5 star shift ratings with comments</li>
                <li><span class="ns-bullet-icon">✓</span> Multi-category leaderboards across the unit</li>
                <li><span class="ns-bullet-icon">✓</span> Streak bonuses for consistent attendance</li>
                <li><span class="ns-bullet-icon">✓</span> Individual nurse profiles with full stats</li>
            </ul>
        </div>
    </div>
</div>
</div>

<!-- SECTION 06 — CTA -->
<div class="ns-cta-section">
    <h2>Ready to run your unit<br>on autopilot?</h2>
    <p>Launch the scheduling agent and generate your first compliant 4-week schedule in under a minute.</p>
    <a href="/Scheduling_Tool" class="ns-btn-white">Launch the App →</a>
</div>

<!-- FOOTER -->
<div class="ns-footer">
    <div class="ns-footer-logo">🏥 NurseScheduler</div>
    <div class="ns-footer-links">
        <a href="#how-it-works">How it works</a>
        <a href="#features">Features</a>
        <a href="#compliance">Compliance</a>
        <a href="/Scheduling_Tool">Launch App</a>
    </div>
    <div class="ns-footer-copy">Built for Stanford Health Care · SHC/CRONA CBA 2025–2028</div>
</div>
""", unsafe_allow_html=True)
