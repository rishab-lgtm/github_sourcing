"""
Email notifications for M13 GitHub Sourcing Tool.
"""

import os
import requests
from datetime import datetime

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_URL = "https://api.resend.com/emails"
FROM_EMAIL = "onboarding@resend.dev"


def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set — skipping email")
        return False
    resp = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": FROM_EMAIL, "to": to, "subject": subject, "html": html},
    )
    return resp.ok


def send_new_profiles_email(profiles: list, to_email: str) -> bool:
    if not profiles:
        return False

    rows_html = ""
    for p in profiles:
        score = p.get("signal_score", 0)
        color = "#16a34a" if score >= 60 else "#d97706" if score >= 50 else "#6b7280"
        rows_html += f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a;">
                <a href="{p.get('github_url','')}" style="color:#60a5fa;">{p.get('handle','')}</a>
            </td>
            <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a;">{p.get('name','')}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a;">{p.get('location','')}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a;color:{color};font-weight:600;">{score}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a;color:#9ca3af;font-size:12px;">{(p.get('bio') or '')[:80]}</td>
        </tr>"""

    html = f"""
    <div style="font-family:sans-serif;background:#0e0e0e;color:#e0e0e0;padding:32px;max-width:700px;margin:auto;border-radius:8px;">
        <h2 style="color:#ffffff;margin-bottom:4px;">🔍 M13 GitHub Sourcing — New Profiles</h2>
        <p style="color:#888;margin-top:0;">{datetime.now().strftime('%B %d, %Y')}</p>
        <p>{len(profiles)} new high-signal engineer{'s' if len(profiles) != 1 else ''} found since your last scan:</p>
        <table style="width:100%;border-collapse:collapse;margin-top:16px;">
            <thead>
                <tr style="background:#1a1a1a;">
                    <th style="padding:8px 12px;text-align:left;color:#aaa;">Handle</th>
                    <th style="padding:8px 12px;text-align:left;color:#aaa;">Name</th>
                    <th style="padding:8px 12px;text-align:left;color:#aaa;">Location</th>
                    <th style="padding:8px 12px;text-align:left;color:#aaa;">Score</th>
                    <th style="padding:8px 12px;text-align:left;color:#aaa;">Bio</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        <p style="margin-top:24px;color:#666;font-size:12px;">Sent by M13 GitHub Sourcing Tool</p>
    </div>
    """

    subject = f"M13 Sourcing — {len(profiles)} new engineer{'s' if len(profiles) != 1 else ''} found"
    return send_email(to_email, subject, html)


def send_recap_email(profiles: list, new_profiles: list, removed_handles: list, to_email: str, frequency: str = "Weekly") -> bool:
    if not profiles:
        return False

    def profile_row(p):
        score = p.get("signal_score", 0)
        color = "#16a34a" if score >= 60 else "#d97706" if score >= 50 else "#6b7280"
        badges = p.get("founder_badges") or ""
        return f"""
        <tr>
            <td style="padding:6px 10px;border-bottom:1px solid #2a2a2a;">
                <a href="{p.get('github_url','')}" style="color:#60a5fa;">{p.get('handle','')}</a>
            </td>
            <td style="padding:6px 10px;border-bottom:1px solid #2a2a2a;">{p.get('name','')}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #2a2a2a;">{p.get('location','')}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #2a2a2a;">{p.get('company','')}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #2a2a2a;font-size:12px;">{badges}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #2a2a2a;color:{color};font-weight:600;">{score}</td>
        </tr>"""

    all_rows = "".join(profile_row(p) for p in profiles)

    new_section = ""
    if new_profiles:
        new_rows = "".join(profile_row(p) for p in new_profiles)
        new_section = f"""
        <h3 style="color:#4ade80;margin-top:32px;">🆕 New This Period ({len(new_profiles)})</h3>
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:#1a1a1a;">
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Handle</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Name</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Location</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Company</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Signals</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Score</th>
            </tr></thead>
            <tbody>{new_rows}</tbody>
        </table>"""

    removed_section = ""
    if removed_handles:
        removed_section = f"""
        <h3 style="color:#f87171;margin-top:32px;">👋 Dropped Off ({len(removed_handles)})</h3>
        <p style="color:#9ca3af;">{", ".join(removed_handles)}</p>"""

    html = f"""
    <div style="font-family:sans-serif;background:#0e0e0e;color:#e0e0e0;padding:32px;max-width:800px;margin:auto;border-radius:8px;">
        <h2 style="color:#ffffff;margin-bottom:4px;">📊 M13 GitHub Sourcing — {frequency} Recap</h2>
        <p style="color:#888;margin-top:0;">{datetime.now().strftime('%B %d, %Y')} · {len(profiles)} total profiles</p>

        {new_section}
        {removed_section}

        <h3 style="color:#ffffff;margin-top:32px;">Full List ({len(profiles)})</h3>
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:#1a1a1a;">
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Handle</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Name</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Location</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Company</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Signals</th>
                <th style="padding:6px 10px;text-align:left;color:#aaa;">Score</th>
            </tr></thead>
            <tbody>{all_rows}</tbody>
        </table>
        <p style="margin-top:24px;color:#666;font-size:12px;">Sent by M13 GitHub Sourcing Tool</p>
    </div>
    """

    subject = f"M13 Sourcing {frequency} Recap — {len(profiles)} profiles · {len(new_profiles)} new"
    return send_email(to_email, subject, html)


def send_weekly_digest(new_profiles: list, all_profiles: list, to_email: str) -> bool:
    """Send weekly digest of new candidates to Brent & Thomas."""
    if not new_profiles:
        return False

    def profile_row(p):
        score = p.get("signal_score", 0)
        color = "#16a34a" if score >= 60 else "#d97706" if score >= 50 else "#6b7280"
        badges = p.get("founder_badges") or ""
        return f"""
        <tr>
            <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">
                <a href="{p.get('github_url','')}" style="color:#2563eb;font-weight:500;">{p.get('handle','')}</a>
            </td>
            <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">{p.get('name','') or '—'}</td>
            <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">{p.get('location','') or '—'}</td>
            <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">{p.get('company','') or '—'}</td>
            <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;font-size:12px;">{badges or '—'}</td>
            <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;color:{color};font-weight:600;">{score}</td>
        </tr>"""

    new_rows = "".join(profile_row(p) for p in new_profiles)

    html = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;color:#111827;padding:40px 32px;max-width:700px;margin:auto;">
        <div style="margin-bottom:24px;">
            <span style="background:#0083FF;color:#fff;font-weight:700;font-size:11px;letter-spacing:0.08em;padding:4px 10px;border-radius:4px;">M13</span>
        </div>
        <h2 style="margin:0 0 6px;font-size:22px;">GitHub Sourcing — Weekly Digest</h2>
        <p style="color:#6b7280;margin:0 0 24px;">{datetime.now().strftime('%B %d, %Y')} · {len(new_profiles)} new candidates · {len(all_profiles)} total in database</p>

        <h3 style="font-size:14px;text-transform:uppercase;letter-spacing:0.06em;color:#6b7280;margin-bottom:12px;">New This Week</h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <thead>
                <tr style="border-bottom:2px solid #e5e7eb;">
                    <th style="padding:8px 10px;text-align:left;color:#6b7280;font-size:12px;">Handle</th>
                    <th style="padding:8px 10px;text-align:left;color:#6b7280;font-size:12px;">Name</th>
                    <th style="padding:8px 10px;text-align:left;color:#6b7280;font-size:12px;">Location</th>
                    <th style="padding:8px 10px;text-align:left;color:#6b7280;font-size:12px;">Company</th>
                    <th style="padding:8px 10px;text-align:left;color:#6b7280;font-size:12px;">Signals</th>
                    <th style="padding:8px 10px;text-align:left;color:#6b7280;font-size:12px;">Score</th>
                </tr>
            </thead>
            <tbody>{new_rows}</tbody>
        </table>

        <p style="margin-top:32px;font-size:13px;color:#9ca3af;">
            Sent by M13 GitHub Sourcing · Reply to action a candidate
        </p>
    </div>
    """

    subject = f"M13 GitHub Sourcing — {len(new_profiles)} new candidate{'s' if len(new_profiles) != 1 else ''} this week"
    return send_email(to_email, subject, html)
