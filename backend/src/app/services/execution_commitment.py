from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Literal


STANDARD_PATH = "standard"
HIGH_COMMITMENT_PATH = "high_commitment"

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_RECONFIRMING = "reconfirming"
STATUS_PAUSED = "paused"

STANDARD_COMPLETE_RATE = 0.60
HIGH_COMMITMENT_COMPLETE_RATE = 0.85
REVIEW_CHECKPOINTS = (7, 14, 21, 28)

CommitmentChoice = Literal["high_commitment", "standard", "current_pace"]


def minimum_complete_days(service_day: int, threshold: float) -> int:
    """门槛天数统一向上取整，避免百分比显示与天数判定分叉。"""

    return math.ceil(service_day * threshold)


def initialize_execution_commitment(
    intake_facts: dict[str, object],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用最新确定性目标初始化/刷新承诺；不抹掉已发生的确认与复评证据。"""

    state = deepcopy(existing) if isinstance(existing, dict) else {}
    candidate = intake_facts.get("high_commitment_candidate") is True
    standard_target = str(intake_facts.get("basic_target_jin_or_range") or "").strip()
    high_target = str(
        intake_facts.get("challenge_target_jin_or_review_text") or ""
    ).strip()
    if not candidate:
        high_target = ""

    state.update(
        {
            "version": 1,
            "high_commitment_candidate": candidate,
            "high_commitment_basis": list(
                intake_facts.get("high_commitment_basis")
                if isinstance(intake_facts.get("high_commitment_basis"), list)
                else []
            ),
            "standard_target": standard_target,
            "high_commitment_target": high_target or None,
            "standard_complete_rate": STANDARD_COMPLETE_RATE,
            "high_commitment_complete_rate": HIGH_COMMITMENT_COMPLETE_RATE,
        }
    )
    state.setdefault("execution_path", STANDARD_PATH)
    state.setdefault("status", STATUS_PENDING)
    state.setdefault("reviews", {})
    return state


def effective_target(state: dict[str, Any] | None) -> str | None:
    data = state if isinstance(state, dict) else {}
    if data.get("status") != STATUS_ACTIVE:
        return None
    if data.get("execution_path") == HIGH_COMMITMENT_PATH:
        return str(data.get("high_commitment_target") or "").strip() or None
    return str(data.get("standard_target") or "").strip() or None


def render_commitment_invitation(
    state: dict[str, Any],
    *,
    checkin_example_lines: list[str] | None = None,
) -> str:
    """两份交付文件已发送后的固定邀请；不向用户暴露内部 D0 名称。

    若传入生成时保存的个性化打卡原题与示例，合并为单条引导发送，避免连续多条长消息刷屏。
    """

    standard_target = str(state.get("standard_target") or "当前标准目标").strip()
    lines = ["两份专属文件已经发到群里了。"]
    if checkin_example_lines:
        lines.extend(
            [
                "",
                "每天直接复制下面这段，只把“｜”后面的内容改成当天实际情况：",
                "",
                "【每日打卡】",
                "",
                *checkin_example_lines,
                "",
                "没完成也照实改，当天五项都有回答，才计为1个完整打卡日。产品和餐食建议随手拍照发群里，有疑问随时问我们。",
            ]
        )
    else:
        lines.extend(
            [
                "接下来请每天在群内完成五项打卡。当天五项都有回答，才计为1个完整打卡日。",
            ]
        )
    lines.append("")
    if state.get("high_commitment_candidate") is True and state.get(
        "high_commitment_target"
    ):
        lines.extend(
            [
                "结合您目前的情况，可以选择：",
                f"1. 高承诺档：{state['high_commitment_target']}，累计完整打卡率需达到85%；",
                f"2. 标准档：{standard_target}，累计完整打卡率需达到60%；",
                "3. 先按当前能做到的节奏执行，暂不启用减重目标。",
                "",
                "请直接回复“选择高承诺档”“选择标准档”或“先按当前节奏”。",
            ]
        )
    else:
        lines.extend(
            [
                f"当前可选择标准档：{standard_target}，累计完整打卡率需达到60%；也可以先按当前能做到的节奏执行，暂不启用减重目标。",
                "",
                "请直接回复“选择标准档”或“先按当前节奏”。",
            ]
        )
    return "\n".join(lines)


def apply_commitment_choice(
    state: dict[str, Any],
    *,
    choice: CommitmentChoice,
    source_message_id: int,
    source_text: str,
    occurred_at: datetime,
    sender_id: str | None = None,
    sender_name: str | None = None,
) -> dict[str, Any]:
    updated = deepcopy(state)
    if choice == "high_commitment":
        if updated.get("high_commitment_candidate") is not True:
            raise ValueError("当前不具备高承诺档资格")
        if not updated.get("high_commitment_target"):
            raise ValueError("当前没有可用的高承诺目标")
        if updated.get("high_commitment_downgraded_at"):
            raise ValueError("高承诺档已在本周期降级，不能自动重新进入")
        updated["execution_path"] = HIGH_COMMITMENT_PATH
        updated["status"] = STATUS_ACTIVE
    elif choice == "standard":
        updated["execution_path"] = STANDARD_PATH
        updated["status"] = STATUS_ACTIVE
    elif choice == "current_pace":
        updated["execution_path"] = STANDARD_PATH
        updated["status"] = STATUS_PAUSED
    else:  # pragma: no cover - Literal 已限制，保留运行时防御
        raise ValueError("无效的执行选择")

    updated["confirmation"] = {
        "choice": choice,
        "occurred_at": occurred_at.isoformat(),
        "source_message_id": source_message_id,
        "source_text": source_text,
        "sender_id": sender_id,
        "sender_name": sender_name,
    }
    return updated


def review_execution_commitment(
    state: dict[str, Any],
    *,
    checkpoint: int,
    complete_days: int,
    occurred_at: datetime,
) -> tuple[dict[str, Any], str | None]:
    """按累计完整打卡日复评，返回新状态和需要发送的群内文案。"""

    if checkpoint not in REVIEW_CHECKPOINTS:
        raise ValueError("unsupported execution checkpoint")
    updated = deepcopy(state)
    reviews = dict(updated.get("reviews") or {})
    key = f"day{checkpoint}"
    if key in reviews:
        return updated, None
    if updated.get("status") not in {STATUS_ACTIVE, STATUS_RECONFIRMING}:
        return updated, None

    complete_days = max(0, min(int(complete_days), checkpoint))
    rate = complete_days / checkpoint
    percent = round(rate * 100)
    path_before = str(updated.get("execution_path") or STANDARD_PATH)
    action = "none"
    message: str | None = None

    if path_before == HIGH_COMMITMENT_PATH:
        if rate >= HIGH_COMMITMENT_COMPLETE_RATE:
            action = "retain_high_commitment"
            message = (
                f"截至服务第{checkpoint}天，您累计完成{complete_days}天完整打卡，"
                f"完整打卡率{percent}%，达到高承诺档85%的执行约定。前面的连续记录很有价值，"
                "接下来继续按当前高承诺目标推进。"
            )
        else:
            updated["execution_path"] = STANDARD_PATH
            updated["high_commitment_downgraded_at"] = occurred_at.isoformat()
            updated["high_commitment_downgrade_reason"] = (
                f"D{checkpoint}累计完整打卡{complete_days}/{checkpoint}天（{percent}%），低于85%"
            )
            if rate >= STANDARD_COMPLETE_RATE:
                action = "downgrade_to_standard"
                updated["status"] = STATUS_ACTIVE
                message = (
                    f"截至服务第{checkpoint}天，您累计完成{complete_days}天完整打卡，完整打卡率{percent}%。"
                    "这段时间已经积累了有效记录，但还没有达到高承诺档85%的执行约定，"
                    f"接下来调整为标准档，继续按{updated.get('standard_target') or '标准目标'}稳步推进。"
                )

    if rate < STANDARD_COMPLETE_RATE:
        action = "reconfirm_standard"
        updated["execution_path"] = STANDARD_PATH
        updated["status"] = STATUS_RECONFIRMING
        message = _render_below_standard_message(
            checkpoint=checkpoint,
            complete_days=complete_days,
            percent=percent,
        )

    reviews[key] = {
        "checkpoint": checkpoint,
        "complete_days": complete_days,
        "required_standard_days": minimum_complete_days(
            checkpoint, STANDARD_COMPLETE_RATE
        ),
        "required_high_commitment_days": minimum_complete_days(
            checkpoint, HIGH_COMMITMENT_COMPLETE_RATE
        ),
        "complete_rate": round(rate, 4),
        "complete_rate_percent": percent,
        "path_before": path_before,
        "path_after": updated.get("execution_path"),
        "status_after": updated.get("status"),
        "action": action,
        "reviewed_at": occurred_at.isoformat(),
        "message_status": "pending" if message else "not_required",
    }
    updated["reviews"] = reviews
    return updated, message


def _render_below_standard_message(
    *, checkpoint: int, complete_days: int, percent: int
) -> str:
    return (
        f"截至服务第{checkpoint}天，您已经完成{complete_days}天完整打卡，完整打卡率{percent}%。"
        "这些记录很有价值，也让我们看到还有一些执行环节需要一起调顺。\n"
        "接下来您可以选择：\n"
        "1. 继续按标准目标推进，我们一起把累计完整打卡率提升到60%以上；\n"
        "2. 先按目前能做到的节奏稳住，原定减重目标暂时不作为当前阶段的复盘标准。\n"
        "您更倾向哪一种？也请告诉我们主要卡在哪里，我们看看怎么帮助您调整。"
    )


def agent_execution_context(state: dict[str, Any] | None) -> str:
    data = state if isinstance(state, dict) else {}
    if not data:
        return (
            "当前未生成执行约定，未处于邀请待确认状态；只解释通用规则，不询问用户选档。"
        )
    confirmation = (
        data.get("confirmation") if isinstance(data.get("confirmation"), dict) else {}
    )
    invitation = (
        data.get("invitation") if isinstance(data.get("invitation"), dict) else {}
    )
    status = str(data.get("status") or STATUS_PENDING)
    invitation_sent = str(invitation.get("status") or "") in {
        "sending",
        "sent",
        "unknown",
    }
    if status == STATUS_PENDING and invitation_sent and not confirmation:
        selection_state = "邀请已发出，等待用户明确选择"
    elif status == STATUS_RECONFIRMING:
        selection_state = "阶段复评后等待用户重新确认"
    elif confirmation:
        selection_state = "已确认，不主动重复追问选档"
    else:
        selection_state = "邀请未发出或未成功，不询问用户选档"
    if status == STATUS_ACTIVE:
        path_display = str(data.get("execution_path") or STANDARD_PATH)
    elif status == STATUS_PAUSED:
        path_display = "当前节奏（目标暂停）"
    elif status == STATUS_RECONFIRMING:
        path_display = "标准档待重新确认"
    else:
        path_display = "待确认，尚未生效"
    last_review = None
    reviews = data.get("reviews") if isinstance(data.get("reviews"), dict) else {}
    if reviews:
        last_key = max(
            reviews, key=lambda item: int(str(item).removeprefix("day") or 0)
        )
        last_review = reviews[last_key]
    lines = [
        f"当前执行档位：{path_display}",
        f"承诺状态：{status}",
        f"选档状态：{selection_state}",
        f"高承诺资格：{'通过' if data.get('high_commitment_candidate') is True else '未通过'}",
        f"标准目标：{data.get('standard_target') or '未提供'}",
        f"高承诺目标：{data.get('high_commitment_target') or '不适用'}",
        f"当前有效目标：{effective_target(data) or '暂未启用'}",
    ]
    if confirmation:
        lines.append(
            f"最近确认：{confirmation.get('choice')}，证据消息 {confirmation.get('source_message_id')}。"
        )
    if isinstance(last_review, dict):
        lines.append(
            f"最近复评：D{last_review.get('checkpoint')}，完整{last_review.get('complete_days')}天，"
            f"{last_review.get('complete_rate_percent')}%，处理={last_review.get('action')}。"
        )
    return "\n".join(lines)
