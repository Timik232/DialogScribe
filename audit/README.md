# Audit baseline

`findings.json` is the machine-readable traceability matrix for Appendix A of the
audit-remediation plan and the 2026-09-04 scanner baselines. Validate it with:

```sh
python tools/validate_audit_matrix.py audit/findings.json
python -m pytest -q tests/test_audit_matrix.py
```

All baseline findings remain `planned`; scanner prose alone is not evidence of a
fix. Accepted risks must use an ISO `expiry` date. Sonar seed rows are retained
because the dedicated historical Sonar instance was removed. The available
historical export returned zero issues, so exact Sonar keys were not invented.

The normalized Trivy rows were regenerated with Trivy 0.74.0. They contain
identifiers and package metadata, never matched secret values. The filesystem
scan found three JWT log occurrences and one ignored local Vault token file;
only rule IDs and locations are recorded.

## Test prerequisites

Collection requires an explicit non-production `JWT_SECRET`; the six previously
reported collection errors all came from importing authentication without it:

```sh
JWT_SECRET=ci-test-only python -m pytest --collect-only -q
```

Tests marked `requires_gpu`, `requires_hf_token`, or `requires_model` are visible
but excluded from the default CI run. Dispatch the Jenkins model stage only on a
GPU Docker host with the `dialogscribe-hf-token` Jenkins credential configured.
The baseline runner permits only the two named pre-existing failures and fails
on every collection error or new failure.
