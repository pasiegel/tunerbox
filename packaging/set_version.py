"""Set the version everywhere it's written down.

    python packaging/set_version.py 1.4.1        (or v1.4.1, as in a git tag)
    python packaging/set_version.py --check      prints the current version

Updates tunerbox/__init__.py, which the Windows, macOS and Linux builds read.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    "tunerbox/__init__.py": [(r'__version__ = "[^"]+"', '__version__ = "{v}"')],
}


def current():
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "tunerbox/__init__.py").read_text()).group(1)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if arg == "--check":
        print(current())
        return
    v = arg.lstrip("vV")
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        sys.exit(f"Version must look like 1.4.1 (got {arg!r})")
    major, minor, patch = map(int, m.groups())
    code = major * 10000 + minor * 100 + patch
    for rel, subs in FILES.items():
        p = ROOT / rel
        if not p.exists():          # the public repo has only the desktop builds
            continue
        s = p.read_text(encoding="utf-8")
        for pat, rep in subs:
            s, n = re.subn(pat, rep.format(v=v, code=code), s)
            if not n:
                sys.exit(f"{rel}: pattern {pat!r} not found")
        p.write_text(s, encoding="utf-8")
    print(f"Version {v} (build {code})")


if __name__ == "__main__":
    main()
