#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the repository-QA conformance suite without installing a public CLI."""

from __future__ import annotations

from conformance.runner import main


if __name__ == "__main__":
    raise SystemExit(main())
