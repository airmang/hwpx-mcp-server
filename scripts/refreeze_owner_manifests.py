#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""소유자 원장의 동결값을 실제 트리에서 다시 계산한다.

각 정본 소유자는 파일 수·LOC·매니페스트 SHA를 원장에 얼려두고 테스트가 그걸
확인한다. 드리프트를 잡는 좋은 장치인데, 소유권이 **정당하게** 움직이면 —
모듈이 옮겨 오거나 이름이 바뀌면 — 다섯 군데를 손으로 고쳐야 하고 그러다
어서션을 지우고 싶어진다.

지우는 대신 다시 언다. 옛 값을 ``refrozenFrom``에 남겨서 무엇이 언제 왜
바뀌었는지 원장만 보고도 알 수 있게 한다.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: (동결값을 계산하는 테스트, 그 값을 담은 원장)
PAIRS = [
    ("tests/test_visual_runtime_owner.py", "docs/architecture/visual-runtime-owner.json"),
    ("tests/test_exam_runtime_owner.py", "docs/architecture/exam-runtime-owner.json"),
    ("tests/test_form_fill_runtime_owner.py", "docs/architecture/form-fill-runtime-owner.json"),
    ("tests/test_evalplan_runtime_owner.py", "docs/architecture/evalplan-runtime-owner.json"),
    ("tests/test_policy_runtime_owner.py",
     "docs/architecture/compliance-quality-utilities-owner.json"),
    ("tests/test_document_operations_owner_boundary.py",
     "docs/architecture/document-operations-owner.json"),
]


def _manifest_of(test_path: Path):
    """테스트가 쓰는 _manifest()를 그대로 불러 같은 값을 얻는다."""
    spec = importlib.util.spec_from_file_location("_owner_test", test_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        pass  # 수집 단계 실패는 무시 — _manifest만 있으면 된다
    fn = getattr(module, "_manifest", None)
    return fn() if fn else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", required=True, help="왜 다시 얼리는지")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    for test_rel, ledger_rel in PAIRS:
        test_path, ledger_path = ROOT / test_rel, ROOT / ledger_rel
        if not (test_path.is_file() and ledger_path.is_file()):
            print(f"  {ledger_rel}: 없음, 건너뜀")
            continue
        result = _manifest_of(test_path)
        if result is None:
            print(f"  {ledger_rel}: _manifest 없음, 건너뜀")
            continue
        rows = result[0] if isinstance(result, tuple) else result
        sha = (
            result[1]
            if isinstance(result, tuple)
            else hashlib.sha256(
                json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        loc = sum(int(r["loc"]) for r in rows)
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"),
                            object_pairs_hook=collections.OrderedDict)
        canonical = ledger.get("canonical")
        if canonical is None:
            print(f"  {ledger_rel}: canonical 없음, 건너뜀")
            continue
        old_sha, old_loc = canonical.get("manifestSha256"), canonical.get("loc")
        if old_sha == sha and old_loc == loc:
            print(f"  {ledger_rel}: 이미 일치")
            continue
        print(f"  {ledger_rel}: loc {old_loc}→{loc}  sha {str(old_sha)[:12]}…→{sha[:12]}…")
        if not args.apply:
            continue
        canonical["manifestSha256"] = sha
        if "loc" in canonical:
            canonical["loc"] = loc
        if "pythonFiles" in canonical:
            canonical["pythonFiles"] = len(rows)
        canonical["refrozenFrom"] = {"loc": old_loc, "manifestSha256": old_sha,
                                     "reason": args.reason}
        if "packageFiles" in ledger:
            ledger["packageFiles"] = [Path(str(r["path"])).name for r in rows]
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    # 일부 소유자 테스트는 같은 해시를 **테스트 안에도** 리터럴로 박아둔다.
    # 원장만 고치면 통과하는 일을 막으려는 이중 잠금이다 — 이 도구가 조용히
    # 무력화하면 안 되므로, 그런 자리를 찾아 사람에게 넘긴다.
    import re

    literals: list[str] = []
    for test_rel, _ in PAIRS:
        path = ROOT / test_rel
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'"[0-9a-f]{64}"', line):
                literals.append(f"{test_rel}:{number}")
    if literals:
        print("\n  테스트에 박힌 해시 리터럴 — 손으로 확인하고 갱신할 것:")
        for item in literals:
            print(f"    {item}")

    if not args.apply:
        print("\n  실제 적용하려면 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
