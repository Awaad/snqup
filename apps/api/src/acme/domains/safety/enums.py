"""Enum types owned by the safety domain."""

from enum import StrEnum


class ReportStatus(StrEnum):
    OPEN = "open"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"
