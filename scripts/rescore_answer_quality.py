"""Create a separate offline report from saved answers; never call providers."""

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

from investigation_engine.evaluation.answer_quality import export_answer_quality
from investigation_engine.evaluation.rescore import rescore_saved_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--suite", type=Path, default=Path("data/samples/evaluation/suite.json"))
    parser.add_argument("--corpus", type=Path, default=Path("data/samples/evaluation/corpus.json"))
    args = parser.parse_args()
    if args.output.resolve() == args.source_report.resolve().parent or (args.output / "answer-quality.json").exists():
        parser.error("Choose a fresh output directory; historical reports cannot be overwritten")
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    report = rescore_saved_report(read(args.source_report), read(args.suite), read(args.corpus),
                                 reviews=read(args.reviews) if args.reviews else None)
    report["offline_rescoring"] = {"timestamp_utc": datetime.now(UTC).isoformat(),
        "source_report": str(args.source_report), "source_sha256": sha256(args.source_report.read_bytes()).hexdigest(),
        "reviews": str(args.reviews) if args.reviews else None,
        "new_generation_requests": 0, "new_embedding_requests": 0,
        "saved_response_generated_before_guidance_change": True}
    export_answer_quality(report, args.output)
    print(f"Saved separate offline rescore: {args.output}")


if __name__ == "__main__":
    main()
