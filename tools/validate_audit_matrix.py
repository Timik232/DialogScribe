#!/usr/bin/env python3
"""Validate the audit finding matrix used by remediation CI."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

EXPECTED_ORACLE_IDS = {
    *(f"CQ-C{i}" for i in range(1, 4)),
    *(f"CQ-H{i}" for i in range(1, 9)),
    *(f"CQ-M{i}" for i in range(1, 15)),
    *(f"CQ-L{i}" for i in range(1, 4)),
    "SEC-C1",
    *(f"SEC-H{i}" for i in range(1, 5)),
    *(f"SEC-M{i}" for i in range(1, 6)),
    "SEC-L1",
}

EXPECTED_SCANNER_SEED_IDS = {
    "SONAR-B1",
    *(f"SONAR-HS{i}" for i in range(1, 5)),
    *(f"SONAR-CS{i}" for i in range(1, 7)),
    *(f"TRIVY-FS{i}" for i in range(1, 6)),
    "TRIVY-SEC1",
    *(f"TRIVY-IMG{i}" for i in range(1, 4)),
}

REQUIRED_FIELDS = (
    "id",
    "source",
    "issue_key",
    "affected_artifact",
    "disposition",
    "owner",
    "target",
    "verification_command",
    "evidence_path",
)
ALLOWED_DISPOSITIONS = {"open", "planned", "fixed", "not-reachable", "accepted"}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_matrix(document: Any) -> list[str]:
    """Return deterministic, human-readable validation errors."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["matrix: root must be a JSON object"]

    findings = document.get("findings")
    if not isinstance(findings, list):
        return ["matrix: 'findings' must be a JSON array"]

    seen: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"row {index}: finding must be a JSON object")
            continue
        finding_id = str(finding.get("id") or f"row {index}")
        if finding_id in seen:
            errors.append(f"{finding_id}: duplicate id")
        seen.add(finding_id)

        for field in REQUIRED_FIELDS:
            if _is_blank(finding.get(field)):
                errors.append(f"{finding_id}: missing mandatory field '{field}'")

        disposition = finding.get("disposition")
        if not _is_blank(disposition) and disposition not in ALLOWED_DISPOSITIONS:
            errors.append(f"{finding_id}: invalid disposition '{disposition}'")

        expiry = finding.get("expiry")
        if disposition == "accepted":
            if _is_blank(expiry):
                errors.append(f"{finding_id}: accepted risk requires 'expiry'")
            else:
                try:
                    date.fromisoformat(str(expiry))
                except ValueError:
                    errors.append(f"{finding_id}: expiry must use ISO date YYYY-MM-DD")

    for missing in sorted(EXPECTED_ORACLE_IDS - seen):
        errors.append(f"{missing}: required Appendix A Oracle id is missing")
    for missing in sorted(EXPECTED_SCANNER_SEED_IDS - seen):
        errors.append(f"{missing}: required Appendix A scanner seed id is missing")
    return errors


def load_matrix(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", nargs="?", type=Path, default=Path("audit/findings.json"))
    args = parser.parse_args()
    try:
        document = load_matrix(args.matrix)
    except ValueError as exc:
        print(exc)
        return 1

    errors = validate_matrix(document)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"audit matrix valid: {len(document['findings'])} findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
