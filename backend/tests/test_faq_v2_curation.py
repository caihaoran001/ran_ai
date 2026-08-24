from __future__ import annotations

import json
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CURATION_PATH = (
    BACKEND_ROOT
    / "src"
    / "app"
    / "services"
    / "knowledge_base"
    / "faq_v2_curation.json"
)


def _curation() -> dict:
    return json.loads(CURATION_PATH.read_text(encoding="utf-8"))


def test_faq_v2_curation_covers_the_approved_production_snapshot() -> None:
    payload = _curation()
    archive_ids = {item["id"] for item in payload["archive"]}
    rewrite_ids = {item["id"] for item in payload["rewrite"]}
    promote_ids = set(payload["promote_current_to_v2"])
    keep_ids = set(payload["keep_current_v2"])

    assert payload["status"] == "approved"
    assert payload["curation_version"] == "v2"
    assert payload["expected_active_before"] == 86
    assert payload["expected_active_after"] == 45
    assert len(archive_ids) == len(payload["archive"]) == 41
    assert len(rewrite_ids) == len(payload["rewrite"]) == 25
    assert len(promote_ids) == len(payload["promote_current_to_v2"]) == 14
    assert len(keep_ids) == len(payload["keep_current_v2"]) == 6
    assert not (archive_ids & rewrite_ids)
    assert not (archive_ids & promote_ids)
    assert not (archive_ids & keep_ids)
    assert not (rewrite_ids & promote_ids)
    assert not (rewrite_ids & keep_ids)
    assert not (promote_ids & keep_ids)
    assert len(archive_ids | rewrite_ids | promote_ids | keep_ids) == 86
    assert len(rewrite_ids | promote_ids | keep_ids) == payload["expected_active_after"]
    assert all(item["expected_version"] == 1 for item in payload["rewrite"])
    assert promote_ids == set(range(91, 105))
    assert keep_ids == {3, 11, 15, 22, 24, 87}


def test_rewritten_faq_answers_are_dense_and_free_of_scripted_chatter() -> None:
    answers = [item["answer"] for item in _curation()["rewrite"]]
    forbidden = (
        "宝宝",
        "能理解",
        "理解你",
        "这份自律",
        "说明你",
        "这是好事",
        "加油",
        "我们一起",
        "明天看看",
        "随时问我",
        "～",
        "~",
    )

    assert all(20 <= len(answer) <= 220 for answer in answers)
    assert all(not answer.endswith(("？", "?")) for answer in answers)
    for phrase in forbidden:
        assert all(phrase not in answer for answer in answers)


def test_archivist_faq_prompt_no_longer_forces_empathy_praise_or_followup() -> None:
    extract_source = (
        BACKEND_ROOT / "src" / "app" / "services" / "archivist" / "extract_agent.py"
    ).read_text(encoding="utf-8")
    tool_source = (
        BACKEND_ROOT / "src" / "app" / "services" / "archivist" / "report_tools.py"
    ).read_text(encoding="utf-8")

    for source in (extract_source, tool_source):
        assert "直接给结论" in source
        assert "必要事实" in source
        assert "最小完整" in source
        assert "独立业务问题" in source
        assert "分别调用多次" in source
        assert "先承接情绪、找亮点" not in source
