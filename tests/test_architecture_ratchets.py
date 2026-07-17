from __future__ import annotations

import runpy
from pathlib import Path


RATCHET_SCRIPT = Path(__file__).parents[1] / "scripts" / "check_architecture_ratchets.py"


def test_p3_architecture_ratchets_remain_exact() -> None:
    namespace = runpy.run_path(str(RATCHET_SCRIPT))
    namespace["assert_ratchets"]()
