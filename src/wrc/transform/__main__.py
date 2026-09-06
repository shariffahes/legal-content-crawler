from __future__ import annotations

import argparse
import sys
from datetime import date

from wrc.config import get_settings
from wrc.logging import configure
from wrc.partitions import PartitionSize
from wrc.source import SourceSpec
from wrc.transform.job import TransformJob


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--source", help="source spec path; defaults to SOURCE_SPEC_PATH")
    parser.add_argument("--partition-size", help="defaults to PARTITION_SIZE")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure(settings.log_level)
    spec = SourceSpec.load(args.source or settings.source_spec_path)
    size = PartitionSize(args.partition_size or settings.partition_size)

    summary = TransformJob(settings, spec, args.start, args.end, size).run()
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
