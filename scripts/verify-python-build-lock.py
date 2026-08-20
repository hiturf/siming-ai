"""Verify that a packaging environment exactly matches the committed lock file."""
from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re


EXACT_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s;]+)$")


def canonicalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path) -> dict[str, tuple[str, str]]:
    locked: dict[str, tuple[str, str]] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = EXACT_REQUIREMENT.fullmatch(line)
        if not match:
            raise SystemExit(
                f"{path}:{line_number}: every build dependency must use one exact 'name==version' pin"
            )
        display_name = match.group("name")
        key = canonicalize(display_name)
        if key in locked:
            raise SystemExit(f"{path}:{line_number}: duplicate package pin for {display_name}")
        locked[key] = (display_name, match.group("version"))
    if not locked:
        raise SystemExit(f"Build dependency lock is empty: {path}")
    return locked


def installed_packages() -> dict[str, tuple[str, str]]:
    installed: dict[str, tuple[str, str]] = {}
    for distribution in metadata.distributions():
        display_name = distribution.metadata.get("Name")
        if not display_name:
            continue
        installed[canonicalize(display_name)] = (display_name, distribution.version)
    return installed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--pyinstaller-version", required=True)
    args = parser.parse_args()

    locked = read_lock(args.lock)
    installed = installed_packages()
    problems: list[str] = []

    for key in sorted(locked.keys() - installed.keys()):
        problems.append(f"missing: {locked[key][0]}=={locked[key][1]}")
    for key in sorted(installed.keys() - locked.keys()):
        problems.append(f"unexpected: {installed[key][0]}=={installed[key][1]}")
    for key in sorted(locked.keys() & installed.keys()):
        expected_name, expected_version = locked[key]
        _, actual_version = installed[key]
        if actual_version != expected_version:
            problems.append(
                f"version mismatch: {expected_name} expected {expected_version}, got {actual_version}"
            )

    pyinstaller = locked.get("pyinstaller")
    if pyinstaller is None:
        problems.append("missing required PyInstaller pin")
    elif pyinstaller[1] != args.pyinstaller_version:
        problems.append(
            "PyInstaller lock does not match build-toolchain.json: "
            f"{pyinstaller[1]} != {args.pyinstaller_version}"
        )

    if problems:
        raise SystemExit("Locked packaging environment verification failed:\n- " + "\n- ".join(problems))

    print(
        f"Locked packaging environment verified: {len(locked)} packages, "
        f"PyInstaller {args.pyinstaller_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
