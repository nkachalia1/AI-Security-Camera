from __future__ import annotations

import base64
import logging
import queue
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

from .config import Settings
from .models import IncidentEvent

LOGGER = logging.getLogger(__name__)

SEVERITY_ORDER = {"info": 0, "notice": 1, "warning": 2, "critical": 3}


@dataclass(frozen=True)
class AlertMessage:
    event_id: int
    severity: str
    event_type: str
    subject: str
    body: str
    sms_body: str


class AlertManager:
    def __init__(
        self,
        settings: Settings,
        send_email: Callable[[AlertMessage], None] | None = None,
        send_sms: Callable[[AlertMessage], None] | None = None,
    ):
        self.settings = settings
        self.send_email = send_email or (lambda message: _send_email(settings, message))
        self.send_sms = send_sms or (lambda message: _send_sms(settings, message))
        self._queue: queue.Queue[AlertMessage | None] = queue.Queue(maxsize=25)
        self._stop = threading.Event()
        self._last_sent_by_key: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        if self.enabled:
            self._thread = threading.Thread(target=self._worker, name="alert-manager", daemon=True)
            self._thread.start()

    @property
    def email_configured(self) -> bool:
        return bool(
            self.settings.alert_email_enabled
            and self._smtp_configured
            and self.settings.alert_email_to
        )

    @property
    def sms_configured(self) -> bool:
        if not self.settings.alert_sms_enabled:
            return False
        if self.settings.alert_sms_provider == "email_gateway":
            return bool(self._smtp_configured and self.settings.alert_sms_email_gateway_to)
        if self.settings.alert_sms_provider != "twilio":
            return False
        return bool(
            self.settings.alert_sms_twilio_account_sid
            and self.settings.alert_sms_twilio_auth_token
            and self.settings.alert_sms_twilio_from
            and self.settings.alert_sms_to
        )

    @property
    def _smtp_configured(self) -> bool:
        return bool(self.settings.alert_email_smtp_host and self.settings.alert_email_from)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.alerts_enabled and (self.email_configured or self.sms_configured))

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "email_configured": self.email_configured,
            "sms_configured": self.sms_configured,
            "sms_provider": self.settings.alert_sms_provider,
            "min_severity": self.settings.alert_min_severity,
            "cooldown_seconds": self.settings.alert_cooldown_seconds,
            "public_base_url": self.settings.public_base_url,
        }

    def notify(self, event: IncidentEvent, event_id: int) -> bool:
        if not self._should_alert(event):
            return False

        message = self._message(event, event_id)
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            LOGGER.warning("Alert queue is full; dropped alert for event %s", event_id)
            return False
        return True

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout=3)

    def _should_alert(self, event: IncidentEvent) -> bool:
        if not self.enabled:
            return False
        severity = _severity_rank(event.severity)
        minimum = _severity_rank(self.settings.alert_min_severity)
        if severity < minimum:
            return False
        key = f"{event.event_type}:{event.track_id}:{event.label}:{event.zone}"
        now = time.monotonic()
        last_sent = self._last_sent_by_key.get(key)
        if last_sent is not None and now - last_sent < self.settings.alert_cooldown_seconds:
            return False
        self._last_sent_by_key[key] = now
        return True

    def _message(self, event: IncidentEvent, event_id: int) -> AlertMessage:
        clip_url = self._media_url(event.clip_path, "clips")
        frame_url = self._media_url(event.frame_path, "frames")
        subject = f"[AI Security Camera] {event.severity.upper()} - {event.event_type}"
        lines = [
            "AI Security Camera alert",
            "",
            f"Summary: {event.summary}",
            f"Severity: {event.severity}",
            f"Event: {event.event_type}",
            f"Time: {event.timestamp.isoformat(timespec='seconds')}",
            f"Label: {event.label or 'unknown'}",
            f"Zone: {event.zone or 'general room area'}",
            f"Track ID: {event.track_id if event.track_id is not None else 'n/a'}",
        ]
        if clip_url:
            lines.append(f"Clip: {clip_url}")
        if frame_url:
            lines.append(f"Frame: {frame_url}")

        sms_parts = [
            f"AI Camera {event.severity.upper()}: {event.summary}",
            f"Zone: {event.zone or 'general'}",
        ]
        if clip_url:
            sms_parts.append(f"Clip: {clip_url}")

        return AlertMessage(
            event_id=event_id,
            severity=event.severity,
            event_type=event.event_type,
            subject=subject,
            body="\n".join(lines),
            sms_body=_truncate(" | ".join(sms_parts), 320),
        )

    def _media_url(self, raw_path: str | None, route: str) -> str | None:
        if not raw_path or not self.settings.public_base_url:
            return None
        filename = Path(raw_path).name
        if not filename:
            return None
        return f"{self.settings.public_base_url}/{route}/{filename}"

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if message is None:
                return
            if self.email_configured:
                try:
                    self.send_email(message)
                except Exception as exc:
                    LOGGER.warning("Could not send email alert: %s", exc)
            if self.sms_configured:
                try:
                    self.send_sms(message)
                except Exception as exc:
                    LOGGER.warning("Could not send SMS alert: %s", exc)


def _severity_rank(severity: str | None) -> int:
    return SEVERITY_ORDER.get((severity or "").lower(), 0)


def _send_email(settings: Settings, message: AlertMessage) -> None:
    if not settings.alert_email_smtp_host or not settings.alert_email_from or not settings.alert_email_to:
        return

    email = EmailMessage()
    email["Subject"] = message.subject
    email["From"] = settings.alert_email_from
    email["To"] = settings.alert_email_to
    email.set_content(message.body)

    _send_email_message(settings, email)


def _send_email_message(settings: Settings, email: EmailMessage) -> None:
    with smtplib.SMTP(
        settings.alert_email_smtp_host,
        settings.alert_email_smtp_port,
        timeout=8,
    ) as smtp:
        if settings.alert_email_use_tls:
            smtp.starttls()
        if settings.alert_email_smtp_user and settings.alert_email_smtp_password:
            smtp.login(settings.alert_email_smtp_user, settings.alert_email_smtp_password)
        smtp.send_message(email)


def _send_sms(settings: Settings, message: AlertMessage) -> None:
    if settings.alert_sms_provider == "email_gateway":
        _send_sms_email_gateway(settings, message)
        return
    if settings.alert_sms_provider != "twilio":
        raise RuntimeError(f"Unsupported SMS provider: {settings.alert_sms_provider}")
    if not (
        settings.alert_sms_twilio_account_sid
        and settings.alert_sms_twilio_auth_token
        and settings.alert_sms_twilio_from
        and settings.alert_sms_to
    ):
        return

    url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.alert_sms_twilio_account_sid}/Messages.json"
    )
    data = urllib.parse.urlencode(
        {
            "From": settings.alert_sms_twilio_from,
            "To": settings.alert_sms_to,
            "Body": message.sms_body,
        }
    ).encode("utf-8")
    token = base64.b64encode(
        f"{settings.alert_sms_twilio_account_sid}:{settings.alert_sms_twilio_auth_token}".encode(
            "utf-8"
        )
    ).decode("ascii")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(str(exc)) from exc


def _send_sms_email_gateway(settings: Settings, message: AlertMessage) -> None:
    if (
        not settings.alert_email_smtp_host
        or not settings.alert_email_from
        or not settings.alert_sms_email_gateway_to
    ):
        return

    email = EmailMessage()
    email["Subject"] = "AI Camera Alert"
    email["From"] = settings.alert_email_from
    email["To"] = settings.alert_sms_email_gateway_to
    email.set_content(message.sms_body)
    _send_email_message(settings, email)


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."
