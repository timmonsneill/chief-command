#!/usr/bin/env python3
"""Weekly memory audit runner.

Scans ~/.claude/projects/-Users-user/memory/ for hygiene issues and appends a
dated entry to audit_log.md.

Hygiene checks performed:
  - Files not modified in >90 days → [stale]
  - Files over 5 KB → [bloated]
  - Duplicate name:/description: values in frontmatter → [duplicate]
  - References in MEMORY.md to files that don't exist → [broken-link]

Usage:
    python3 backend/scripts/audit_runner.py
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR = Path.home() / ".claude" / "projects" / "-Users-user" / "memory"
AUDIT_LOG = MEMORY_DIR / "audit_log.md"

STALE_DAYS = 90
BLOAT_BYTES = 5 * 1024  # 5 KB


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract key: value pairs from YAML-style frontmatter (--- blocks)."""
    fm: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^(\w+):\s*(.+)$", line.strip())
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def find_broken_links(memory_dir: Path) -> list[str]:
    """Find filenames referenced in MEMORY.md that don't exist."""
    memory_index = memory_dir / "MEMORY.md"
    if not memory_index.exists():
        return []

    text = memory_index.read_text(encoding="utf-8")
    # Match markdown links like [label](filename.md) and bare `filename.md` refs
    referenced: set[str] = set()

    # markdown links: (filename.md)
    for m in re.finditer(r"\(([^)]+\.md)\)", text):
        referenced.add(m.group(1))

    # backtick refs: `filename.md`
    for m in re.finditer(r"`([^`]+\.md)`", text):
        referenced.add(m.group(1))

    broken: list[str] = []
    for ref in sorted(referenced):
        candidate = memory_dir / ref
        if not candidate.exists():
            broken.append(ref)

    return broken


def run_audit() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(tz=timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    md_files = sorted(MEMORY_DIR.glob("*.md"))
    # Exclude the audit log itself from scanning
    scannable = [f for f in md_files if f.name != "audit_log.md"]

    findings: list[str] = []

    # --- Stale + bloated ---
    for f in scannable:
        stat = f.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        age_days = (now - mtime).days

        if age_days > STALE_DAYS:
            last_mod = mtime.strftime("%Y-%m-%d")
            findings.append(f"- [stale] `{f.name}` last modified {last_mod}")

        if stat.st_size > BLOAT_BYTES:
            size_kb = stat.st_size / 1024
            findings.append(f"- [bloated] `{f.name}` is {size_kb:.1f}KB")

    # --- Duplicate frontmatter values ---
    name_map: dict[str, list[str]] = {}
    desc_map: dict[str, list[str]] = {}

    for f in scannable:
        text = f.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text)
        if "name" in fm:
            name_map.setdefault(fm["name"], []).append(f.name)
        if "description" in fm:
            desc_map.setdefault(fm["description"], []).append(f.name)

    for value, files in name_map.items():
        if len(files) > 1:
            pair = " and ".join(f"`{fn}`" for fn in files)
            findings.append(f"- [duplicate] {pair} share name: {value!r}")

    for value, files in desc_map.items():
        if len(files) > 1:
            pair = " and ".join(f"`{fn}`" for fn in files)
            findings.append(f"- [duplicate] {pair} share description: {value!r}")

    # --- Broken links in MEMORY.md ---
    broken = find_broken_links(MEMORY_DIR)
    for ref in broken:
        findings.append(
            f"- [broken-link] MEMORY.md references `{ref}` which doesn't exist"
        )

    # --- Build audit entry ---
    scanned_count = len(scannable)
    header = (
        f"## {today_str} — weekly audit\n\n"
        f"**Scanned:** {scanned_count} files in {MEMORY_DIR}/\n\n"
    )

    if findings:
        findings_block = "### Findings\n" + "\n".join(findings) + "\n\n"
        actions_block = "### Actions taken\n(none — manual review required)\n"
    else:
        findings_block = "### Findings\nclean pass, no issues.\n\n"
        actions_block = "### Actions taken\n(none — clean pass)\n"

    entry = header + findings_block + actions_block + "\n---\n\n"

    # Print to stdout for visibility
    print(entry)

    # Append to audit_log.md (create if missing)
    with open(AUDIT_LOG, "a", encoding="utf-8") as log:
        if AUDIT_LOG.stat().st_size == 0 if AUDIT_LOG.exists() else False:
            pass  # file was just created, no preamble needed
        log.write(entry)

    print(f"Appended audit entry to {AUDIT_LOG}", file=sys.stderr)


if __name__ == "__main__":
    run_audit()
