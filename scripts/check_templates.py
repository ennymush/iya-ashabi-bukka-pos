"""Full project audit: template syntax, url_for endpoints, missing includes.

Static checks only - no HTTP calls - so it is fast and side-effect free.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# An audit must never touch the shop's real data folder, so unless the caller
# has already chosen a directory this one gets a throwaway folder.
_workdir = None
if os.environ.get("IAB_DATA_DIR") is None:
    _workdir = tempfile.mkdtemp(prefix="iab-audit-")
    os.environ["IAB_DATA_DIR"] = _workdir
os.environ.setdefault("APP_ENV", "testing")

from app import create_app  # noqa: E402
from jinja2 import TemplateSyntaxError  # noqa: E402

URL_RE = re.compile(r"""url_for\(\s*['"]([A-Za-z_][\w.]*)['"](?!\s*~)""")
# url_for("reports." ~ endpoint) style - dynamic, cannot be checked statically.
DYNAMIC_RE = re.compile(r"""url_for\(\s*['"][\w.]*['"]\s*~""")
INCLUDE_RE = re.compile(r"""\{%-?\s*(?:include|extends|import|from)\s+['"]([^'"]+)['"]""")

app = create_app("testing")
template_dir = BASE / "app" / "templates"

known = {rule.endpoint for rule in app.url_map.iter_rules()}

problems: list[str] = []
dynamic: list[str] = []
includes: set[str] = set()
endpoints_seen: set[str] = set()

# 1. Syntax: let Jinja parse every template.
with app.app_context():
    env = app.jinja_env
    paths = sorted(template_dir.rglob("*.html"))
    for path in paths:
        rel = path.relative_to(template_dir).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            env.parse(source, filename=rel)
        except TemplateSyntaxError as exc:
            problems.append(f"SYNTAX  {rel}:{exc.lineno}: {exc.message}")

        for match in DYNAMIC_RE.finditer(source):
            dynamic.append(f"{rel}: {match.group(0)}")

        for match in INCLUDE_RE.finditer(source):
            ref = match.group(1)
            includes.add(ref)
            candidate = template_dir / ref
            if not candidate.exists() and not (template_dir / (ref + ".html")).exists():
                problems.append(f"MISSING {rel}: referenced template '{ref}' not found")

        for match in URL_RE.finditer(source):
            ep = match.group(1)
            endpoints_seen.add(ep)

missing = sorted(e for e in endpoints_seen if e not in known and "." in e)

print(f"templates parsed      : {len(paths)}")
print(f"url_for endpoints used: {len(endpoints_seen)}")
print(f"dynamic url_for       : {len(dynamic)}")
if dynamic:
    for item in dynamic:
        print(f"   (dynamic) {item}")

if missing:
    print("\nUNKNOWN url_for ENDPOINTS")
    for ep in missing:
        near = sorted(k for k in known if k.split(".")[0] == ep.split(".")[0])
        print(f"  {ep}   (blueprint has: {', '.join(near) or 'nothing'})")

if problems:
    print(f"\n{len(problems)} PROBLEM(S)")
    for item in problems:
        print("  " + item)
else:
    print("\nNo template syntax or include problems.")

print(f"\nRESULT: {'FAIL' if (problems or missing) else 'OK'}")

if _workdir:
    shutil.rmtree(_workdir, ignore_errors=True)
sys.exit(1 if (problems or missing) else 0)
