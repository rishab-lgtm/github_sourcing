"""
Email notifications for M13 GitHub Sourcing Tool.
All user-supplied values are HTML-escaped before insertion.
"""

import html
import os
import logging
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_URL = "https://api.resend.com/emails"
RESEND_FROM_EMAIL = os.environ.get(
    "RESEND_FROM_EMAIL",
    "M13 GitHub Sourcing <onboarding@resend.dev>",
)
ALLOWED_EMAIL_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "m13.co")
MAX_SEND_ATTEMPTS = 3


def _e(value) -> str:
    """HTML-escape any value for safe insertion into email HTML."""
    return html.escape(str(value or ""), quote=True)


def send_email(to: str, subject: str, body_html: str) -> bool:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping email")
        return False
    recipient = (to or "").strip().lower()
    if not recipient or recipient.count("@") != 1:
        log.warning("Invalid recipient email: %s", to)
        return False
    if not recipient.endswith(f"@{ALLOWED_EMAIL_DOMAIN.lower()}"):
        log.warning("Rejected email to non-M13 address: %s", to)
        return False

    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        try:
            resp = requests.post(
                RESEND_URL,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": RESEND_FROM_EMAIL,
                    "to": recipient,
                    "subject": subject,
                    "html": body_html,
                },
                timeout=10,
            )
            if resp.ok:
                return True
            log.warning(
                "Resend error %d on attempt %d/%d: %s",
                resp.status_code, attempt, MAX_SEND_ATTEMPTS, resp.text[:200],
            )
            # Invalid requests will not improve on retry. Retry only rate limits
            # and server failures.
            if resp.status_code < 500 and resp.status_code != 429:
                return False
        except requests.RequestException as exc:
            log.warning("Resend request failed on attempt %d/%d: %s", attempt, MAX_SEND_ATTEMPTS, exc)
        except Exception as exc:
            log.warning("send_email failed: %s", exc)
            return False

        if attempt < MAX_SEND_ATTEMPTS:
            time.sleep(2 ** (attempt - 1))
    return False


def _score_color(score: int) -> str:
    if score >= 60: return "#16a34a"
    if score >= 50: return "#d97706"
    return "#6b7280"


def _profile_row(p: dict, show_reasons: bool = False) -> str:
    score = p.get("signal_score", 0)
    color = _score_color(score)
    reasons = p.get("match_reasons") or []
    bio = (p.get("bio") or "").strip()
    linkedin_url = p.get("linkedin_url") or ""
    github_url = p.get("github_url") or ""

    reasons_html = ""
    if show_reasons and reasons:
        items = "".join(f"<li>{_e(r)}</li>" for r in reasons[:4])
        reasons_html = f'<ul style="margin:4px 0 0;padding-left:16px;font-size:11px;color:#6b7280;">{items}</ul>'

    bio_html = f'<div style="font-size:11px;color:#6b7280;margin-top:3px;font-style:italic;">{_e(bio[:120])}{"…" if len(bio) > 120 else ""}</div>' if bio else ""

    links_html = f'<a href="{_e(github_url)}" style="color:#0083FF;font-size:11px;margin-right:8px;">GitHub</a>' if github_url else ""
    if linkedin_url:
        links_html += f'<a href="{_e(linkedin_url)}" style="color:#0a66c2;font-size:11px;">LinkedIn</a>'

    return f"""
    <tr>
        <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;vertical-align:top;">
            <a href="{_e(github_url)}" style="color:#0083FF;font-weight:600;text-decoration:none;">
                @{_e(p.get('handle',''))}
            </a><br>
            <span style="font-size:12px;color:#374151;font-weight:500;">{_e(p.get('name',''))}</span>
            {bio_html}
            <div style="margin-top:4px">{links_html}</div>
            {reasons_html}
        </td>
        <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;vertical-align:top;white-space:nowrap;">
            {_e(p.get('location','') or '—')}
        </td>
        <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;vertical-align:top;">
            {_e(p.get('company','') or '—')}
        </td>
        <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;vertical-align:top;">
            {_e(p.get('founder_badges','') or '—')}
        </td>
        <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;color:{color};font-weight:700;vertical-align:top;text-align:center;">
            {score}
        </td>
    </tr>"""


def _email_wrapper(content: str) -> str:
    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Poppins,sans-serif;
                background:#f7f7f8;padding:40px 24px;max-width:800px;margin:auto;">
        <div style="background:#150F3A;border-radius:12px;padding:20px 28px;margin-bottom:24px;
                    display:flex;align-items:center;gap:12px;">
            <span style="background:#0083FF;color:#fff;font-weight:800;font-size:11px;
                         letter-spacing:0.08em;padding:4px 10px;border-radius:4px;">M13</span>
            <span style="color:rgba(255,255,255,0.8);font-size:14px;font-weight:500;">
                GitHub Sourcing
            </span>
        </div>
        <div style="background:#ffffff;border-radius:12px;padding:28px 32px;
                    border:1px solid #e8e8ec;box-shadow:0 2px 8px rgba(21,15,58,0.05);">
            {content}
        </div>
        <p style="margin-top:20px;text-align:center;color:#9ca3af;font-size:12px;">
            Sent by M13 GitHub Sourcing · m13.co
        </p>
    </div>"""


def _table_html(rows_html: str) -> str:
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:16px;">
        <thead>
            <tr style="border-bottom:2px solid #e5e7eb;">
                <th style="padding:8px 12px;text-align:left;color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Engineer</th>
                <th style="padding:8px 12px;text-align:left;color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Location</th>
                <th style="padding:8px 12px;text-align:left;color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Company</th>
                <th style="padding:8px 12px;text-align:left;color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Signals</th>
                <th style="padding:8px 12px;text-align:left;color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Score</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>"""


def send_test_email(to_email: str) -> bool:
    """Send a small end-to-end delivery check from the Settings screen."""
    content = """
    <h2 style="margin:0 0 8px;font-size:20px;font-weight:700;color:#150F3A;">
        Notifications are connected
    </h2>
    <p style="color:#6b7280;margin:0;font-size:14px;line-height:1.6;">
        This test confirms that the GitHub Sourcing app can deliver email through
        its production notification provider.
    </p>"""
    return send_email(
        to_email,
        "M13 GitHub Sourcing — notification test",
        _email_wrapper(content),
    )


def send_watched_person_alert(profile: dict, changes: list[str], to_email: str) -> bool:
    """Notify a user about meaningful new activity from a monitored person."""
    if not changes:
        return False
    handle = _e(profile.get("handle", ""))
    name = _e(profile.get("name") or profile.get("handle", ""))
    github_url = _e(
        profile.get("github_url") or f"https://github.com/{profile.get('handle', '')}"
    )
    items = "".join(f"<li style='margin-bottom:6px'>{_e(change)}</li>" for change in changes)
    content = f"""
    <h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#150F3A;">
        New activity from {name}
    </h2>
    <p style="color:#6b7280;margin:0 0 18px;font-size:14px;">
        You are monitoring <a href="{github_url}" style="color:#0083FF;">@{handle}</a>
        in your sourcing pipeline.
    </p>
    <ul style="padding-left:20px;color:#374151;font-size:14px;line-height:1.5;">
        {items}
    </ul>"""
    return send_email(
        to_email,
        f"M13 Sourcing — new activity from @{profile.get('handle', '')}",
        _email_wrapper(content),
    )


def send_new_profiles_email(profiles: list, to_email: str) -> bool:
    if not profiles:
        return False
    rows = "".join(_profile_row(p, show_reasons=True) for p in profiles)
    content = f"""
    <h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#150F3A;">
        {len(profiles)} New Engineer{'s' if len(profiles) != 1 else ''} Found
    </h2>
    <p style="color:#6b7280;margin:0 0 20px;font-size:14px;">
        {datetime.now().strftime('%B %d, %Y')} · High-signal candidates from your last scan
    </p>
    {_table_html(rows)}"""
    subject = f"M13 Sourcing — {len(profiles)} new engineer{'s' if len(profiles) != 1 else ''} found"
    return send_email(to_email, subject, _email_wrapper(content))


def send_recap_email(
    profiles: list,
    new_profiles: list,
    removed_handles: list,
    to_email: str,
    frequency: str = "Weekly",
) -> bool:
    if not profiles:
        return False

    new_section = ""
    if new_profiles:
        new_rows = "".join(_profile_row(p, show_reasons=True) for p in new_profiles)
        new_section = f"""
        <h3 style="font-size:14px;text-transform:uppercase;letter-spacing:0.06em;color:#16a34a;margin:28px 0 8px;">
            New This Period ({len(new_profiles)})
        </h3>
        {_table_html(new_rows)}"""

    removed_section = ""
    if removed_handles:
        handles_safe = ", ".join(_e(h) for h in removed_handles)
        removed_section = f"""
        <h3 style="font-size:14px;text-transform:uppercase;letter-spacing:0.06em;color:#dc2626;margin:28px 0 8px;">
            Dropped Off ({len(removed_handles)})
        </h3>
        <p style="color:#6b7280;font-size:13px;">{handles_safe}</p>"""

    all_rows = "".join(_profile_row(p) for p in profiles)
    content = f"""
    <h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#150F3A;">
        {frequency} Recap
    </h2>
    <p style="color:#6b7280;margin:0 0 20px;font-size:14px;">
        {datetime.now().strftime('%B %d, %Y')} · {len(profiles)} total profiles
    </p>
    {new_section}
    {removed_section}
    <h3 style="font-size:14px;text-transform:uppercase;letter-spacing:0.06em;color:#150F3A;margin:28px 0 8px;">
        Full List ({len(profiles)})
    </h3>
    {_table_html(all_rows)}"""
    subject = f"M13 Sourcing {frequency} Recap — {len(profiles)} profiles · {len(new_profiles)} new"
    return send_email(to_email, subject, _email_wrapper(content))


def send_breakout_alert(breakouts: list, to_email: str) -> bool:
    """Email a list of breakout candidates — people who 3x'd followers or stars in 30 days."""
    if not breakouts:
        return False

    def _breakout_row(b: dict) -> str:
        handle = _e(b["handle"])
        url = _e(b["github_url"])
        f_mult = _e(f"{b['follower_mult']}x")
        s_mult = _e(f"{b['star_mult']}x")
        f_gain = _e(f"+{b['follower_gain']:,}")
        s_gain = _e(f"+{b['star_gain']:,}")
        score_delta = b["score_delta"]
        delta_str = f"+{score_delta}" if score_delta >= 0 else str(score_delta)
        velocity_label = "🚀 Follower surge" if b["follower_mult"] >= b["star_mult"] else "⭐ Star surge"
        return f"""
        <tr>
            <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;vertical-align:top;">
                <a href="{url}" style="color:#0083FF;font-weight:600;text-decoration:none;">@{handle}</a>
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;color:#150F3A;font-size:13px;">
                {velocity_label}
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;text-align:center;">
                <span style="font-weight:700;color:#16a34a;">{f_mult}</span>
                <div style="font-size:11px;color:#6b7280;">{f_gain} followers</div>
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;text-align:center;">
                <span style="font-weight:700;color:#0083FF;">{s_mult}</span>
                <div style="font-size:11px;color:#6b7280;">{s_gain} stars</div>
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;text-align:center;font-size:13px;color:#150F3A;">
                {_e(delta_str)} pts
            </td>
        </tr>"""

    rows = "".join(_breakout_row(b) for b in breakouts)
    table = f"""
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
            <tr style="border-bottom:2px solid #150F3A;">
                <th style="padding:8px;text-align:left;font-size:11px;text-transform:uppercase;
                            letter-spacing:0.06em;color:#6b7280;">Handle</th>
                <th style="padding:8px;text-align:left;font-size:11px;text-transform:uppercase;
                            letter-spacing:0.06em;color:#6b7280;">Signal</th>
                <th style="padding:8px;text-align:center;font-size:11px;text-transform:uppercase;
                            letter-spacing:0.06em;color:#6b7280;">Followers</th>
                <th style="padding:8px;text-align:center;font-size:11px;text-transform:uppercase;
                            letter-spacing:0.06em;color:#6b7280;">Stars</th>
                <th style="padding:8px;text-align:center;font-size:11px;text-transform:uppercase;
                            letter-spacing:0.06em;color:#6b7280;">Score Δ</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>"""

    content = f"""
    <h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#150F3A;">
        🚀 Breakout Alert — {len(breakouts)} candidate{'s' if len(breakouts) != 1 else ''} accelerating
    </h2>
    <p style="color:#6b7280;margin:0 0 20px;font-size:14px;">
        {datetime.now().strftime('%B %d, %Y')} ·
        These GitHub profiles 3x'd followers or stars in the last 30 days.
        Worth reaching out now — before everyone else notices.
    </p>
    {table}"""
    subject = f"🚀 M13 Breakout Alert — {len(breakouts)} profile{'s' if len(breakouts) != 1 else ''} accelerating on GitHub"
    return send_email(to_email, subject, _email_wrapper(content))


def send_weekly_digest(new_profiles: list, all_profiles: list, to_email: str) -> bool:
    if not new_profiles:
        return False
    rows = "".join(_profile_row(p, show_reasons=True) for p in new_profiles)
    content = f"""
    <h2 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#150F3A;">
        Weekly Digest
    </h2>
    <p style="color:#6b7280;margin:0 0 20px;font-size:14px;">
        {datetime.now().strftime('%B %d, %Y')} ·
        {len(new_profiles)} new candidate{'s' if len(new_profiles) != 1 else ''} ·
        {len(all_profiles)} total in database
    </p>
    {_table_html(rows)}"""
    subject = f"M13 GitHub Sourcing — {len(new_profiles)} new candidate{'s' if len(new_profiles) != 1 else ''} this week"
    return send_email(to_email, subject, _email_wrapper(content))
