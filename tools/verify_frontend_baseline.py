#!/usr/bin/env python3
"""Reject new Svelte errors while keeping the inherited baseline visible."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

KNOWN_ERRORS = Counter(
    {
        (
            "src/lib/components/MindmapViewer.svelte",
            "Property 'getData' does not exist on type 'Markmap'. Did you mean 'setData'?",
        ): 1,
        (
            "src/routes/autoflow/+page.svelte",
            "Argument of type 'string | undefined' is not assignable to parameter of type 'string'.",
        ): 3,
        (
            "src/routes/live-hints/+page.svelte",
            "Type 'Record<string, unknown> | { text: string; speaker: string; timestamp: number; }' is not assignable to type '{ text: string; speaker: string; timestamp: number; }'.",
        ): 1,
        (
            "src/routes/live-hints/+page.svelte",
            "Type 'Record<string, unknown>' is missing the following properties from type '{ text: string; speaker: string; timestamp: number; }': text, speaker, timestamp",
        ): 1,
        (
            "src/routes/live-hints/+page.svelte",
            'Type \'(Record<string, unknown> | { hint_type: "tactical" | "strategic" | "warning" | "analytical" | "argumentative" | "navigational"; text: string; priority: "critical" | "high" | "medium" | "low"; hint_id?: string | undefined; rationale?: string | undefined; })[]\' is not assignable to type \'{ hint_type: "tactical" | "strategic" | "warning" | "analytical" | "argumentative" | "navigational"; text: string; priority: "critical" | "high" | "medium" | "low"; hint_id?: string | undefined; rationale?: string | undefined; }[]\'.',
        ): 1,
        (
            "src/routes/live-hints/+page.svelte",
            "Type '{ text: string; speaker: string; timestamp: number; }[]' is not assignable to type 'Segment[]'.",
        ): 1,
        (
            "src/routes/templates/+page.svelte",
            "This import uses a '.ts' extension to resolve to an input TypeScript file, but will not be rewritten during emit because it is not a relative path.",
        ): 1,
        (
            "src/routes/transcribe/+page.svelte",
            "Argument of type 'string | undefined' is not assignable to parameter of type 'string'.",
        ): 3,
        (
            "src/routes/transcriptions/+page.svelte",
            "'selectedDetail' is possibly 'null'.",
        ): 1,
    }
)

MACHINE_ERROR = re.compile(
    r'^\d+ ERROR ("(?:[^"\\]|\\.)*") \d+:\d+ ("(?:[^"\\]|\\.)*")$'
)


def parse_errors(output: str) -> Counter[tuple[str, str]]:
    errors: Counter[tuple[str, str]] = Counter()
    for line in output.splitlines():
        match = MACHINE_ERROR.match(line)
        if match:
            path = json.loads(match.group(1))
            message = json.loads(match.group(2)).splitlines()[0]
            errors[(path, message)] += 1
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    observed = parse_errors(args.output.read_text(encoding="utf-8"))

    unexpected = observed - KNOWN_ERRORS
    if unexpected:
        for (path, message), count in sorted(unexpected.items()):
            print(f"UNEXPECTED FRONTEND ERROR ({count}): {path}: {message}")
        return 1

    for (path, message), count in sorted(observed.items()):
        print(f"KNOWN FRONTEND BASELINE ({count}): {path}: {message}")
    for (path, message), count in sorted((KNOWN_ERRORS - observed).items()):
        print(f"FRONTEND BASELINE NOW PASSES ({count}): {path}: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
