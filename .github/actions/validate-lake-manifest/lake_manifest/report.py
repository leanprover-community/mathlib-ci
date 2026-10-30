"""The level of each finding, and the workflow commands that report it.

A field finding is always an error. A pin finding is an error on a strict
run and a warning on other runs. With `allow_older_pins`, an older pin is
a warning on every run.
"""

from __future__ import annotations

from dataclasses import dataclass

from .manifest import FILE
from .rules import Finding, Kind

OVERRIDE_VARIABLE = "MATHLIB_ALLOW_OLDER_DEPENDENCY_PINS"
STRICT_NOTE = "Note: This check fails the Build job on bors runs."
ADMIN_NOTE = f"Note: For an intentional revert, a repository admin can set the variable `{OVERRIDE_VARIABLE}` to `true`."
OVERRIDE_NOTE = f"Note: The variable `{OVERRIDE_VARIABLE}` is `true`, so this check only warns."


@dataclass(frozen=True)
class Policy:
    strict: bool
    allow_older_pins: bool


@dataclass(frozen=True)
class Annotation:
    level: str  # "error" or "warning"
    title: str
    message: str

    def command(self) -> str:
        """The workflow command, with GitHub's escaping for properties and data."""
        return f"::{self.level} file={escape_property(FILE)},title={escape_property(self.title)}::{escape_data(self.message)}"


def annotate(finding: Finding, policy: Policy) -> Annotation:
    if finding.kind is Kind.FIELD:
        return Annotation("error", finding.title, message(finding.problem, finding.notes, finding.hints))
    if finding.kind is Kind.OLDER_PIN and policy.allow_older_pins:
        return Annotation("warning", finding.title, message(finding.problem, (OVERRIDE_NOTE,)))
    notes = (STRICT_NOTE, *finding.notes, *((ADMIN_NOTE,) if finding.kind is Kind.OLDER_PIN else ()))
    level = "error" if policy.strict else "warning"
    return Annotation(level, finding.title, message(finding.problem, notes, finding.hints))


def message(problem: str, notes: tuple[str, ...] = (), hints: tuple[str, ...] = ()) -> str:
    return "\n".join((problem, *notes, *hints))


def escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")
