from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AlertSink(Protocol):
    def send(self, level: str, title: str, message: str, payload: dict | None = None) -> None:
        ...


class PrintAlertSink:
    def send(self, level: str, title: str, message: str, payload: dict | None = None) -> None:
        extra = f" payload={payload}" if payload else ""
        print(f"[{level}] {title}: {message}{extra}")


@dataclass
class MemoryAlertSink:
    events: list[dict]

    def __init__(self) -> None:
        self.events = []

    def send(self, level: str, title: str, message: str, payload: dict | None = None) -> None:
        self.events.append(
            {
                "level": level,
                "title": title,
                "message": message,
                "payload": payload or {},
            }
        )
