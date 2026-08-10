from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.rag_cases import load_rag_eval_suite, summarize_rag_eval_suite


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the versioned RAG evaluation case contract without calling AI APIs."
    )
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    suite = load_rag_eval_suite(args.path)
    print(json.dumps(summarize_rag_eval_suite(suite), ensure_ascii=False, indent=2))
    print("STRUCTURE_ONLY: no LLM, embedding, database, or message-channel calls were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
