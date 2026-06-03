"""CI guard: the shippable hub must not import proprietary captain internals (Codex #8).

Wraps scripts/check_boundaries.py so the boundary is enforced by the test suite. Includes a
negative control proving the detector actually catches proprietary imports (so a green result
means something).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "check_boundaries.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_boundaries", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hub_has_no_proprietary_coupling():
    mod = _load()
    violations = mod.find_violations()
    assert violations == [], f"hub boundary violated:\n" + "\n".join(violations)


def test_detector_catches_proprietary_imports():
    """Negative control — the matcher recognizes proprietary import names."""
    mod = _load()
    assert mod._proprietary_hit("captain_core")
    assert mod._proprietary_hit("captain_core.actions")
    assert mod._proprietary_hit("chad_captain")
    assert not mod._proprietary_hit("state_aggregator")
    assert not mod._proprietary_hit("captain")  # bare 'captain' is not a proprietary pkg name

    # the python-import regex extracts the imported module name
    sample = "from captain_core.actions import foo\nimport os\n"
    mods = [m.group("f") or m.group("i") for m in mod._PY_IMPORT.finditer(sample)]
    assert "captain_core.actions" in mods
