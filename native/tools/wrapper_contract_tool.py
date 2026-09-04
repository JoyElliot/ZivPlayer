# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wrapper_contract_core import ContractError, validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the declared source libmpv JNI ABI.")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--cpp", type=Path, required=True)
    parser.add_argument("--kotlin", type=Path, required=True)
    parser.add_argument("--cmake", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        method_count = validate(
            arguments.contract,
            arguments.cpp,
            arguments.kotlin,
            arguments.cmake,
        )
    except ContractError as failure:
        print(f"wrapper contract invalid: {failure}", file=sys.stderr)
        return 4
    print(f"wrapper contract valid: {method_count} declared native methods")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
