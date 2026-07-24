"""档案官失败分类。

自动隔离是有损操作，只允许显式标记为“可归因到这条消息”的确定性异常触发。
网络、限流、模型服务、数据库、配置和未知代码错误都只重试，不自动跳消息。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError


class ArchivistMessageProcessingError(Exception):
    """调用方确认由当前单条消息稳定触发、适合隔离的确定性错误。"""


@dataclass(frozen=True)
class ArchivistFailureClassification:
    kind: str
    fingerprint: str


def classify_archivist_failure(exc: Exception) -> ArchivistFailureClassification:
    reason = f"{type(exc).__name__}: {exc}"
    fingerprint = hashlib.sha256(reason.encode("utf-8", errors="replace")).hexdigest()[:32]

    if isinstance(exc, ArchivistMessageProcessingError):
        return ArchivistFailureClassification("message", fingerprint)

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return ArchivistFailureClassification("transient", fingerprint)

    if isinstance(exc, SQLAlchemyError):
        return ArchivistFailureClassification("system", fingerprint)

    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 425, 429} or (
        isinstance(status_code, int) and status_code >= 500
    ):
        return ArchivistFailureClassification("transient", fingerprint)

    error_name = type(exc).__name__.lower()
    if any(
        marker in error_name
        for marker in ("timeout", "connection", "ratelimit", "internalserver")
    ):
        return ArchivistFailureClassification("transient", fingerprint)

    # 401/403、缺 key、模型名错误、代码 bug 等都可能在所有服务单上复现；
    # 即使它们不是“瞬时”错误，也绝不能靠跳过用户消息解决。
    return ArchivistFailureClassification("system", fingerprint)
