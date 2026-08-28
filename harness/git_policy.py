"""Shared git-diff safety policy (task #9's GO version + the gatekeeper's merge
check for 'diff'-kind jobs).

The same rules are enforced in TWO places for two different questions:
  - executor.py, on STAGED changes, before a builder's commit is allowed to exist
    at all ("should this ever become a commit?").
  - gatekeeper.py, on a COMMITTED range at merge time, re-applying the same list —
    a builder that got past the first check is still a builder, and the second
    check is the one that can't be bypassed by any path that skips the first (a
    hand-crafted commit, a future retry path, etc).

One function, two callers, so the list of forbidden shapes cannot drift between
where it's enforced at commit time and where it's enforced at merge time.
"""

from __future__ import annotations


def disallowed_paths(raw_lines: list[str], numstat_lines: list[str]) -> list[str]:
    """Read `git diff --raw` and `git diff --numstat` output (already split into
    lines) and return every path that isn't a plain text file with an unchanged
    file mode: binaries, symlinks (120000), submodules (160000), any file that IS
    or BECOMES executable (100755) — new files included, renames included — plus
    `.gitattributes`, anything under a `.git`-prefixed path component (also catches
    `.github`, `.gitignore`, `.gitmodules`), and anything under a `scripts/hooks/`
    directory at any depth.

    `git diff --raw` line shape: `:<old_mode> <new_mode> <old_sha> <new_sha>
    <status>\t<path>` (renames/copies carry a second tab-separated path).
    `git diff --numstat` line shape: `<added>\t<deleted>\t<path>`, where a binary
    file reports `-\t-\t<path>` instead of numbers.
    """
    bad: list[str] = []
    seen: set[str] = set()

    def _flag(path: str, why: str) -> None:
        path = path.strip()
        if not path or path in seen:
            return
        seen.add(path)
        bad.append(f"{path} ({why})")

    for line in raw_lines:
        line = line.strip()
        if not line.startswith(":"):
            continue
        meta, _, paths = line.partition("\t")
        fields = meta.split()
        if len(fields) < 5:
            continue
        old_mode, new_mode, _old_sha, _new_sha, _status = fields[:5]
        old_mode = old_mode.lstrip(":")
        for p in paths.split("\t"):
            p = p.strip()
            if not p:
                continue
            parts = p.split("/")
            if any(part.startswith(".git") for part in parts):
                _flag(p, "inside a .git-prefixed path")
            elif any(parts[i] == "scripts" and parts[i + 1] == "hooks"
                     for i in range(len(parts) - 1)):
                _flag(p, "inside a hooks directory")
            elif new_mode == "120000":
                _flag(p, "a symlink")
            elif new_mode == "160000":
                _flag(p, "a submodule")
            elif p == ".gitattributes" or p.endswith("/.gitattributes"):
                _flag(p, "can silently change how future diffs are rendered")
            # DROPPED the old `status[:1] in ("M", "T")` filter — that silently
            # let a BRAND NEW file committed with the executable bit set through
            # (a new file's old_mode is "000000", so the old check's
            # `old_mode not in ("000000", new_mode)` was False and skipped it
            # entirely), and skipped renames (status "R") the same way.
            elif new_mode == "100755" and old_mode != "100755":
                _flag(p, "its executable bit changed")

    for line in numstat_lines:
        parts = line.strip().split("\t")
        if len(parts) >= 3 and parts[0] == "-" and parts[1] == "-":
            _flag(parts[2], "a binary file")

    return bad
