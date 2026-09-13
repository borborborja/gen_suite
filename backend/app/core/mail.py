"""Small SMTP transport for security-critical transactional mail.

Delivery is intentionally configured by environment instead of binding the product to a
provider.  Development may leave SMTP unset; production startup rejects invitation-only
operation without it.
"""
from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from ..settings import settings


async def send_transactional(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        if settings.environment.lower() == "development":
            return
        raise RuntimeError("SMTP no está configurado")

    def _send() -> None:
        message = EmailMessage()
        message["To"] = to
        message["From"] = settings.smtp_from
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)

    await asyncio.to_thread(_send)
