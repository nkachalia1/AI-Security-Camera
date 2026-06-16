from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import Settings


@dataclass
class IncidentReport:
    title: str
    body: str
    event_ids: list[int]
    created_at: str


class IncidentSummarizer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def summarize(self, events: list[dict[str, Any]]) -> IncidentReport:
        event_ids = [int(event["id"]) for event in events if "id" in event]
        created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        if not events:
            return IncidentReport(
                title="No activity detected",
                body="No recent events were available for summarization.",
                event_ids=[],
                created_at=created_at,
            )

        prompt = self._build_prompt(events)
        provider = self.settings.llm_provider
        if provider == "ollama":
            body = self._summarize_with_ollama(prompt) or self._rules_summary(events)
        elif provider == "openai":
            body = self._summarize_with_openai(prompt) or self._rules_summary(events)
        else:
            body = self._rules_summary(events)

        title = self._title_from_events(events)
        return IncidentReport(title=title, body=body, event_ids=event_ids, created_at=created_at)

    def _build_prompt(self, events: list[dict[str, Any]]) -> str:
        lines = [
            "You are an edge security camera incident analyst.",
            "Write a concise incident report for a room-monitoring appliance.",
            "Mention timeline, notable actors, unusual behavior, and recommended follow-up.",
            "Use plain language and avoid inventing details not present in the events.",
            "",
            "Events:",
        ]
        for event in events:
            lines.append(
                f"- {event['timestamp']} | {event['severity']} | {event['event_type']} | "
                f"{event['summary']} | label={event.get('label')} | zone={event.get('zone')}"
            )
        return "\n".join(lines)

    def _rules_summary(self, events: list[dict[str, Any]]) -> str:
        first = events[0]["timestamp"]
        last = events[-1]["timestamp"]
        warnings = [event for event in events if event["severity"] in {"warning", "critical"}]
        labels = sorted({event.get("label") for event in events if event.get("label")})
        zones = sorted({event.get("zone") for event in events if event.get("zone")})
        lines = [
            f"Activity window: {first} to {last}.",
            f"Observed entities: {', '.join(labels) if labels else 'unclassified motion'}.",
            f"Locations involved: {', '.join(zones) if zones else 'general room area'}.",
            "",
            "Timeline:",
        ]
        for event in events:
            lines.append(f"- {event['timestamp']} - {self._event_line(event)}")
        lines.append("")
        if warnings:
            lines.append("Notable concerns:")
            for event in warnings:
                lines.append(f"- {event['summary']}")
        else:
            lines.append("No high-severity anomalies were detected in this window.")
        lines.append("")
        lines.append("Recommended follow-up: review the attached clips for context before escalating.")
        return "\n".join(lines)

    @staticmethod
    def _event_line(event: dict[str, Any]) -> str:
        metadata = event.get("metadata") or {}
        summary = event["summary"]
        summary_lower = summary.lower()
        details = []
        if metadata.get("movement") and metadata["movement"] != "movement not yet established":
            detail = str(metadata["movement"])
            if detail.lower() not in summary_lower:
                details.append(detail)
        if metadata.get("size"):
            detail = str(metadata["size"])
            if detail.lower() not in summary_lower:
                details.append(detail)
        if metadata.get("confidence"):
            detail = str(metadata["confidence"])
            if detail.lower() not in summary_lower:
                details.append(detail)
        if not details:
            return summary
        return f"{summary} ({'; '.join(details)})"

    def _summarize_with_ollama(self, prompt: str) -> str | None:
        payload = json.dumps(
            {
                "model": self.settings.ollama_model,
                "prompt": prompt,
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.settings.ollama_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return None
        return str(data.get("response", "")).strip() or None

    def _summarize_with_openai(self, prompt: str) -> str | None:
        if not os.getenv("OPENAI_API_KEY"):
            return None
        try:
            from openai import OpenAI
        except ImportError:
            return None

        client = OpenAI()
        try:
            response = client.responses.create(
                model=self.settings.openai_model,
                input=prompt,
                max_output_tokens=450,
            )
        except Exception:
            return None
        return getattr(response, "output_text", "").strip() or None

    @staticmethod
    def _title_from_events(events: list[dict[str, Any]]) -> str:
        if any(event["event_type"] == "unattended_object" for event in events):
            return "Unattended Object Incident"
        if any(event["event_type"] == "person_entered" for event in events):
            return "Room Activity Detected"
        return "Vision Monitoring Summary"
