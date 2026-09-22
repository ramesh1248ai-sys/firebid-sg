"""Notifying people inside the company.

These adapters reach staff only. Nothing here sends to a client or an authority: content leaves
the platform by a named user's own action (guardrail 7).
"""

from __future__ import annotations

import smtplib
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import structlog

from firebid.settings import Settings, get_settings

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Notification:
    recipient_email: str
    subject: str
    body: str
    kind: str
    bid_id: uuid.UUID | None = None


class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...


class ConsoleNotifier:
    """The development default: log that a message would go out, without its body."""

    def send(self, notification: Notification) -> None:
        log.info(
            "notification",
            kind=notification.kind,
            subject=notification.subject,
            bid_id=str(notification.bid_id) if notification.bid_id else None,
        )


class EmailNotifier:
    def __init__(self, host: str, port: int, sender: str, use_tls: bool = True) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.use_tls = use_tls

    def send(self, notification: Notification) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = notification.recipient_email
        message["Subject"] = notification.subject
        message.set_content(notification.body)
        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            if self.use_tls:
                smtp.starttls()
            smtp.send_message(message)
        log.info("notification_sent", kind=notification.kind)


@dataclass
class RecordingNotifier:
    """Keeps what it was asked to send. Used by tests."""

    sent: list[Notification] = field(default_factory=list)

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)


def get_notifier(settings: Settings | None = None) -> Notifier:
    settings = settings or get_settings()
    if settings.smtp_host:
        return EmailNotifier(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.smtp_sender,
            use_tls=settings.smtp_use_tls,
        )
    return ConsoleNotifier()
