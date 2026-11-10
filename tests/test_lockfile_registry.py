"""Registry policy guard for frontend/package-lock.json and CI config.

Regression background (2026-11-10): npm-mirror.gitverse.ru served HTTP 500
for every tarball for 5+ hours while metadata still answered 200, killing
`npm ci` in CI (builds #49/#50) and stalling the pipeline for hours.
The whole system was switched to registry.npmjs.org (0.10–0.43s per request
from both LAN hosts vs 3.8–13s for alternatives reachable from RU).

These tests keep dead/slow mirrors from creeping back in through the
lockfile or pipeline config.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED_REGISTRY = "https://registry.npmjs.org/"

# Mirrors that proved unreliable or slow from this network.
BANNED_HOSTS = (
    "npm-mirror.gitverse.ru",
    "mirrors.cloud.tencent.com",
    "registry.npmmirror.com",
)

# Files that must never reference a banned mirror host.
POLICY_FILES = (
    "frontend/package-lock.json",
    "frontend/package.json",
    "Jenkinsfile",
)


def _read(name: str) -> str:
    path = REPO_ROOT / name
    assert path.exists(), f"policy file missing: {path}"
    return path.read_text(encoding="utf-8")


class TestLockfileRegistry:
    def test_every_resolved_url_uses_npmjs(self):
        lock = json.loads(_read("frontend/package-lock.json"))
        bad: list[str] = []
        for package_name, package in lock.get("packages", {}).items():
            resolved = package.get("resolved")
            if not resolved:
                continue
            if not resolved.startswith(ALLOWED_REGISTRY):
                bad.append(f"{package_name or '<root>'} -> {resolved}")
        assert not bad, (
            "package-lock.json contains non-npmjs resolved URLs "
            "(npm ci downloads tarballs from these — a dead mirror breaks CI):\n"
            + "\n".join(bad[:10])
        )

    def test_no_banned_mirror_hosts_anywhere(self):
        offenders: list[str] = []
        for name in POLICY_FILES:
            content = _read(name)
            for host in BANNED_HOSTS:
                if host in content:
                    offenders.append(f"{name}: {host}")
        assert not offenders, (
            "banned npm mirror host(s) found (gitverse served 500 on all "
            "tarballs for 5h+ on 2026-11-10; see PR #41):\n" + "\n".join(offenders)
        )

    def test_lockfile_intact_after_registry_rewrites(self):
        """The 2026-11-10 rewrite once left a mirror path prefix behind
        (registry.npmjs.org/repository/registry_npmjs_org/... → 404, build
        #50). Guard against that mangled form specifically."""
        lock = _read("frontend/package-lock.json")
        assert "registry.npmjs.org/repository/" not in lock, (
            "mangled registry URL: npmjs path carries a mirror path prefix "
            "(…/repository/registry_npmjs_org/…) — npmjs answers 404"
        )

    def test_dist_tarball_urls_also_npmjs(self):
        """Older lock sections may carry packages/<name>/dist/tarball URLs —
        they are downloaded just like resolved URLs."""
        lock = _read("frontend/package-lock.json")
        urls = re.findall(r'"tarball":\s*"([^"]+)"', lock)
        bad = [u for u in urls if u and not u.startswith(ALLOWED_REGISTRY)]
        assert not bad, f"non-npmjs tarball URLs in lockfile:\n" + "\n".join(bad[:10])
