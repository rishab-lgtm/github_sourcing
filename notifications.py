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
