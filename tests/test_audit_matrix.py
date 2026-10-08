import copy
import json
from pathlib import Path

from tools.validate_audit_matrix import validate_matrix

MATRIX_PATH = Path(__file__).parents[1] / "audit" / "findings.json"


def load_matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_repository_matrix_is_complete():
    assert validate_matrix(load_matrix()) == []


def test_missing_appendix_identifier_is_named():
    matrix = load_matrix()
    matrix["findings"] = [row for row in matrix["findings"] if row["id"] != "CQ-C1"]

    assert "CQ-C1: required Appendix A Oracle id is missing" in validate_matrix(matrix)


def test_incomplete_finding_is_named():
    matrix = copy.deepcopy(load_matrix())
    finding = next(row for row in matrix["findings"] if row["id"] == "CQ-C1")
    finding["disposition"] = ""

    assert "CQ-C1: missing mandatory field 'disposition'" in validate_matrix(matrix)


def test_accepted_risk_requires_valid_expiry():
    matrix = copy.deepcopy(load_matrix())
    finding = next(row for row in matrix["findings"] if row["id"] == "CQ-C1")
    finding["disposition"] = "accepted"
    finding["expiry"] = ""

    assert "CQ-C1: accepted risk requires 'expiry'" in validate_matrix(matrix)
