"""Preview/apply the simplified review wording as new versions; never edit old rows.

Default: uv run python scripts/update_final_review_flow.py
Apply:   uv run python scripts/update_final_review_flow.py --apply --expected zh=8 --expected en=9
Expected versions must come from a fresh preview on the target database.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json

from app.db.session import SessionLocal
from app.services.final_review_scripts import (
    FINAL_REVIEW_NODES, Q_NEXT_GOAL, Q_SERVICE_INTENT, Q_CONTACT_PREFERENCE,
    Q_RESULT_CHANGES, get_active_script, normalize_nodes, save_script_version, seed_nodes,
)


def updated_nodes(nodes: dict, language: str) -> dict:
    result = copy.deepcopy(nodes)
    seeds = {
        q["id"]: q for node in seed_nodes(language).values() for q in node["questions"]
    }
    for node in result.values():
        for question in node["questions"]:
            qid = question["id"]
            if qid in {Q_NEXT_GOAL, Q_SERVICE_INTENT, Q_CONTACT_PREFERENCE, Q_RESULT_CHANGES}:
                question["text"] = seeds[qid]["text"]
            if qid == Q_NEXT_GOAL:
                question["options"] = [q for q in question["options"] if q["key"] != "daily_product"]
            if qid == Q_SERVICE_INTENT and not any(q["key"] == "explore_other" for q in question["options"]):
                option = next(q for q in seeds[qid]["options"] if q["key"] == "explore_other")
                question["options"].insert(1, copy.deepcopy(option))
    return normalize_nodes(result)


async def run(*, apply: bool, expected: dict[str, int]) -> None:
    async with SessionLocal() as session:
        plans = []
        for language in ("zh", "en"):
            current = await get_active_script(session, language=language)
            version = current.version_no or 0
            if apply and expected.get(language) != version:
                raise ValueError(f"{language}: expected {expected.get(language)}, current {version}; preview again")
            nodes = updated_nodes(current.nodes, language)
            changes = []
            for node in FINAL_REVIEW_NODES:
                before = {q["id"]: q for q in current.questions_for(node)}
                for q in nodes[node]["questions"]:
                    if q != before[q["id"]]:
                        changes.append({"id": q["id"], "before": before[q["id"]], "after": q})
            plans.append((current, nodes))
            print(json.dumps({"language": language, "current_version": version,
                              "changes": changes, "needs_seed": current.version_no is None}, ensure_ascii=False))
        if apply:
            for current, nodes in plans:
                if current.version_no is None or nodes != current.nodes:
                    saved = await save_script_version(session, language=current.language, nodes=nodes,
                        changed_by_name="codex-cli", note="27天目标/29天安排/30天联系；权益独立、原因按需")
                    print(f"saved {saved.language} v{saved.version_no}")
            await session.commit()
        else:
            print("PREVIEW ONLY: no changes written")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected", action="append", default=[], metavar="LANG=VERSION")
    args = parser.parse_args()
    expected = dict((k, int(v)) for k, v in (item.split("=", 1) for item in args.expected))
    asyncio.run(run(apply=args.apply, expected=expected))
