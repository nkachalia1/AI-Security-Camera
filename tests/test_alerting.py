from datetime import datetime, timezone
from threading import Event

from vision_appliance.alerting import AlertManager
from vision_appliance.config import Settings
from vision_appliance.models import IncidentEvent


def _warning_event():
    return IncidentEvent(
        event_type="unattended_object",
        summary="Backpack remained unattended near workbench.",
        severity="warning",
        timestamp=datetime.now(timezone.utc),
        track_id=7,
        label="backpack",
        zone="workbench",
        clip_path="/var/lib/vision-appliance/clips/example.mp4",
        frame_path="/var/lib/vision-appliance/frames/example.jpg",
    )


def _alert_settings(**overrides):
    values = {
        "alerts_enabled": True,
        "alert_min_severity": "warning",
        "alert_email_enabled": True,
        "alert_email_smtp_host": "smtp.example.com",
        "alert_email_from": "camera@example.com",
        "alert_email_to": "owner@example.com",
        "alert_sms_enabled": True,
        "alert_sms_twilio_account_sid": "AC123",
        "alert_sms_twilio_auth_token": "secret-token",
        "alert_sms_twilio_from": "+15550001111",
        "alert_sms_to": "+15550002222",
        "public_base_url": "http://pi5.local:8080",
    }
    values.update(overrides)
    return Settings(**values)


def test_alert_manager_sends_email_and_sms_for_warning_event():
    delivered = Event()
    emails = []
    sms_messages = []

    def send_email(message):
        emails.append(message)

    def send_sms(message):
        sms_messages.append(message)
        delivered.set()

    manager = AlertManager(_alert_settings(), send_email=send_email, send_sms=send_sms)

    try:
        assert manager.notify(_warning_event(), event_id=42) is True
        assert delivered.wait(timeout=2)
    finally:
        manager.close()

    assert emails[0].subject == "[AI Security Camera] WARNING - unattended_object"
    assert "Backpack remained unattended near workbench." in emails[0].body
    assert "Clip: http://pi5.local:8080/clips/example.mp4" in emails[0].body
    assert "Frame: http://pi5.local:8080/frames/example.jpg" in emails[0].body
    assert "AI Camera WARNING" in sms_messages[0].sms_body
    assert "http://pi5.local:8080/clips/example.mp4" in sms_messages[0].sms_body


def test_alert_manager_supports_email_gateway_sms_without_twilio_number():
    delivered = Event()
    sms_messages = []
    settings = _alert_settings(
        alert_sms_provider="email_gateway",
        alert_sms_twilio_account_sid=None,
        alert_sms_twilio_auth_token=None,
        alert_sms_twilio_from=None,
        alert_sms_to=None,
        alert_sms_email_gateway_to="15550002222@carrier-gateway.example",
    )

    def send_sms(message):
        sms_messages.append(message)
        delivered.set()

    manager = AlertManager(settings, send_email=lambda _message: None, send_sms=send_sms)

    try:
        assert manager.sms_configured is True
        assert manager.notify(_warning_event(), event_id=43) is True
        assert delivered.wait(timeout=2)
    finally:
        manager.close()

    assert "Backpack remained unattended" in sms_messages[0].sms_body


def test_alert_manager_filters_low_severity_events():
    manager = AlertManager(_alert_settings(), send_email=lambda _message: None)
    event = IncidentEvent(event_type="person_entered", summary="Person entered.", severity="info")

    try:
        assert manager.notify(event, event_id=1) is False
    finally:
        manager.close()


def test_alert_manager_applies_cooldown_per_event_key():
    manager = AlertManager(
        _alert_settings(alert_cooldown_seconds=60),
        send_email=lambda _message: None,
        send_sms=lambda _message: None,
    )
    event = _warning_event()

    try:
        assert manager.notify(event, event_id=1) is True
        assert manager.notify(event, event_id=2) is False
    finally:
        manager.close()


def test_alert_status_does_not_expose_contact_details_or_secrets():
    manager = AlertManager(_alert_settings())

    try:
        status = manager.as_dict()
    finally:
        manager.close()

    assert status["enabled"] is True
    assert status["email_configured"] is True
    assert status["sms_configured"] is True
    assert "owner@example.com" not in str(status)
    assert "+15550002222" not in str(status)
    assert "secret-token" not in str(status)


def test_alert_status_does_not_expose_email_gateway_target():
    manager = AlertManager(
        _alert_settings(
            alert_sms_provider="email_gateway",
            alert_sms_twilio_account_sid=None,
            alert_sms_twilio_auth_token=None,
            alert_sms_twilio_from=None,
            alert_sms_to=None,
            alert_sms_email_gateway_to="15550002222@carrier-gateway.example",
        )
    )

    try:
        status = manager.as_dict()
    finally:
        manager.close()

    assert status["sms_provider"] == "email_gateway"
    assert status["sms_configured"] is True
    assert "carrier-gateway.example" not in str(status)
