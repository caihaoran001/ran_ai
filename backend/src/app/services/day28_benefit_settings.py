from __future__ import annotations

"""第28天权益（券/赠品）的运营配置：存 `app_settings`，后台可改。

设计上的三条硬规则，都是为了不让助教对客户编造钱的事：

1. **没有领取链接就等于这张券不可用。** `enabled=True` 但 `claim_url` 为空时一律
   不可发放——发一条"你有 200 元券"却没有入口，客户只能来问人，比不发更糟。
2. **面额、有效期、适用范围只从这里取，一个字都不许模型自己想。** 配置缺哪项，
   对外话术里就不出现哪项，绝不用"大概""左右"补。
3. **系统只承认"领取入口已发出"。** 商城未打通，看不到客户领没领、用没用，
   所以这里没有"已领取/已使用"的概念（见 `Day28BenefitGrant` 的注释）。

三类券的骨架预置好，但默认全是关闭状态：Lucy 把面额、适用商品、有效期和领取链接
填进后台并打开开关，助教才会提它。
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

SETTING_KEY = "day28_benefits"

KIND_COUPON = "coupon"
KIND_GIFT = "gift"
BENEFIT_KINDS: tuple[str, ...] = (KIND_COUPON, KIND_GIFT)

# 什么时候给这张券。资格口径由用户拍板：走完 28 天且客户还在群里就有，
# 不看打卡率、不看效果、不看有没有答问题（Lucy 明确要求不以填问卷/好评/承诺复购为条件）。
GRANT_ALWAYS = "always"
GRANT_CROSS_PRODUCT_INTENT = "cross_product_intent"  # 客户表示想看其他产品才给
GRANT_CONDITIONS: tuple[str, ...] = (GRANT_ALWAYS, GRANT_CROSS_PRODUCT_INTENT)

MAX_TEXT_CHARS = 200
MAX_URL_CHARS = 1000


@dataclass(frozen=True)
class Day28Benefit:
    code: str
    name: str
    kind: str = KIND_COUPON
    # 自由文本而不是数字：赠品券的"价值"是"咖啡1管"，不是金额。
    face_value: str = ""
    scope: str = ""  # 适用商品/范围
    threshold: str = ""  # 使用门槛
    validity_days: int | None = None
    claim_url: str = ""
    enabled: bool = False
    grant_when: str = GRANT_ALWAYS

    @property
    def grantable(self) -> bool:
        """能不能真发出去。开着但没链接不算可用——见模块开头第 1 条。"""
        return bool(self.enabled and self.claim_url.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "face_value": self.face_value,
            "scope": self.scope,
            "threshold": self.threshold,
            "validity_days": self.validity_days,
            "claim_url": self.claim_url,
            "enabled": self.enabled,
            "grant_when": self.grant_when,
        }


@dataclass(frozen=True)
class Day28BenefitConfig:
    benefits: tuple[Day28Benefit, ...] = field(default_factory=tuple)

    def grantable_for(self, *, cross_product_intent: bool) -> list[Day28Benefit]:
        """本次该发哪些券。跨品券只在客户确实表达了想看其他产品时才进列表。"""
        selected: list[Day28Benefit] = []
        for benefit in self.benefits:
            if not benefit.grantable:
                continue
            if benefit.grant_when == GRANT_CROSS_PRODUCT_INTENT and not cross_product_intent:
                continue
            selected.append(benefit)
        return selected

    def by_code(self, code: str) -> Day28Benefit | None:
        for benefit in self.benefits:
            if benefit.code == code:
                return benefit
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"benefits": [benefit.to_dict() for benefit in self.benefits]}


# 三类券的骨架。字段留空、开关关闭——等运营把真实规则填进后台。
DEFAULT_BENEFITS: tuple[Day28Benefit, ...] = (
    Day28Benefit(
        code="product_200",
        name="200元产品券",
        kind=KIND_COUPON,
        grant_when=GRANT_ALWAYS,
    ),
    Day28Benefit(
        code="repurchase_gift",
        name="复购赠品券",
        kind=KIND_GIFT,
        grant_when=GRANT_ALWAYS,
    ),
    Day28Benefit(
        code="member_fixed",
        name="定额会员券",
        kind=KIND_COUPON,
        grant_when=GRANT_CROSS_PRODUCT_INTENT,
    ),
)


def _clean_text(value: object, *, label: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 个字符。")
    return text


def _clean_url(value: object) -> str:
    """领取入口：可以是网址，也可以是微信小店那种口令。

    2026-09-05 上线当天被真实配置打脸：优惠券的领取入口是
    `#微信小店://店铺/优惠券/xxxx` 这样的口令，运营复制进来直接被"必须 http 开头"
    挡住。口令是要原样发到群里让客户点的，所以只要求"看得出是个入口"
    （带 :// 的协议头），不再限定协议是什么。
    """
    url = str(value or "").strip()
    if not url:
        return ""
    if len(url) > MAX_URL_CHARS:
        raise ValueError(f"领取入口不能超过 {MAX_URL_CHARS} 个字符。")
    if "://" not in url:
        raise ValueError(
            "领取入口要么是 http(s):// 开头的网址，"
            "要么是小店口令（形如 #微信小店://店铺/优惠券/编号）。"
        )
    return url


def _clean_validity_days(value: object) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("有效期天数必须是整数。") from exc
    if days <= 0 or days > 3650:
        raise ValueError("有效期天数必须在 1 到 3650 之间。")
    return days


def parse_benefit_config(payload: object) -> Day28BenefitConfig:
    """校验并规范化整份券配置；任何一项不合规就整份拒收。

    整份拒收是刻意的：券配置直接对应钱。半份生效会让运营以为改动已保存，
    而客户那边收到的是新旧混合的规则。
    """
    if payload in (None, ""):
        return Day28BenefitConfig(benefits=DEFAULT_BENEFITS)
    if not isinstance(payload, dict):
        raise ValueError("券配置必须是对象。")
    raw_list = payload.get("benefits")
    if raw_list in (None, ""):
        return Day28BenefitConfig(benefits=DEFAULT_BENEFITS)
    if not isinstance(raw_list, list):
        raise ValueError("benefits 必须是列表。")
    benefits: list[Day28Benefit] = []
    seen: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            raise ValueError("每张券必须是对象。")
        code = _clean_text(raw.get("code"), label="券编码", limit=40)
        if not code:
            raise ValueError("券编码不能为空。")
        if code in seen:
            raise ValueError(f"券编码重复：{code}")
        seen.add(code)
        name = _clean_text(raw.get("name"), label="券名称", limit=100)
        if not name:
            raise ValueError(f"券「{code}」缺少名称。")
        kind = str(raw.get("kind") or KIND_COUPON).strip()
        if kind not in BENEFIT_KINDS:
            raise ValueError(f"券「{code}」的类型不支持：{kind}")
        grant_when = str(raw.get("grant_when") or GRANT_ALWAYS).strip()
        if grant_when not in GRANT_CONDITIONS:
            raise ValueError(f"券「{code}」的发放条件不支持：{grant_when}")
        benefits.append(
            Day28Benefit(
                code=code,
                name=name,
                kind=kind,
                face_value=_clean_text(raw.get("face_value"), label="面额"),
                scope=_clean_text(raw.get("scope"), label="适用范围"),
                threshold=_clean_text(raw.get("threshold"), label="使用门槛"),
                validity_days=_clean_validity_days(raw.get("validity_days")),
                claim_url=_clean_url(raw.get("claim_url")),
                enabled=bool(raw.get("enabled")),
                grant_when=grant_when,
            )
        )
    return Day28BenefitConfig(benefits=tuple(benefits))


async def get_benefit_config(session: AsyncSession) -> Day28BenefitConfig:
    """取当前券配置；没配过就返回三张关闭状态的骨架。

    库里存着一份非法配置时**不静默回落到默认**（那会让"我明明配了券"变成
    "助教一个字不提"的哑谜），直接抛出让调用方看见。
    """
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        return Day28BenefitConfig(benefits=DEFAULT_BENEFITS)
    return parse_benefit_config(setting.value_json)


async def set_benefit_config(
    session: AsyncSession, payload: object
) -> Day28BenefitConfig:
    config = parse_benefit_config(payload)
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        session.add(AppSetting(key=SETTING_KEY, value_json=config.to_dict()))
    else:
        setting.value_json = config.to_dict()
    await session.flush()
    return config


def describe_benefit(benefit: Day28Benefit, *, language: str = "zh") -> str:
    """把一张券写成给客户看的一行。只拼配置里真有的字段，缺的直接不提。"""
    if language == "en":
        details = [
            item
            for item in (
                benefit.face_value,
                f"valid for {benefit.validity_days} days" if benefit.validity_days else "",
                f"for {benefit.scope}" if benefit.scope else "",
                benefit.threshold,
            )
            if item
        ]
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{benefit.name}{suffix}: {benefit.claim_url}"
    details = [
        item
        for item in (
            benefit.face_value,
            f"{benefit.validity_days}天内有效" if benefit.validity_days else "",
            f"适用于{benefit.scope}" if benefit.scope else "",
            benefit.threshold,
        )
        if item
    ]
    suffix = f"（{'，'.join(details)}）" if details else ""
    return f"{benefit.name}{suffix}：{benefit.claim_url}"


def describe_benefits(
    benefits: list[Day28Benefit], *, language: str = "zh"
) -> str:
    """多张券拼成 `{权益清单}` 占位符的填充值；没有可发的券返回空串。

    返回空串的含义是"这条权益消息不该发"，由调用方判断，不在这里替它决定。
    """
    lines = [describe_benefit(benefit, language=language) for benefit in benefits]
    return "\n".join(lines)
