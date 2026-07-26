# SPDX-License-Identifier: Apache-2.0
"""Run the canonical ``hwpx`` task CLI with ``python -m hwpx_automation``."""

from .office.agent.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
