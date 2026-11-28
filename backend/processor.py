
from datetime import date
from typing import Any, List, Optional


MESSAGE_ACTION_TYPES = (
    "onsite_conversion.messaging_conversation_started_7d",
    "messaging_conversation_started_7d",
    "onsite_conversion.messaging_conversation_started",
    "messaging_conversation_started",
)


VALID_LEVELS = ("campaign", "adset", "ad")


def to_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_integer(value: Any) -> int:
    number = to_number(value)
    return int(number) if number is not None else 0


def get_message_results(actions: Any):
    if not isinstance(actions, list):
        return None, "messaging_conversations_started"

    action_map = {}

    for action in actions:
        if not isinstance(action, dict):
            continue

        action_type = action.get("action_type")
        value = to_number(action.get("value"))

        if action_type and value is not None:
            action_map[action_type] = value

    for action_type in MESSAGE_ACTION_TYPES:
        if action_type in action_map:
            return (
                action_map[action_type],
                "messaging_conversations_started",
            )

    return None, "messaging_conversations_started"


def normalize_insight(
    row: dict,
    account: dict,
    level: str,
) -> dict:

    if level not in VALID_LEVELS:
        raise ValueError(f"Level không hợp lệ: {level}")

    account_id = str(
        row.get("account_id")
        or account.get("account_id")
        or ""
    )

    if not account_id.isdigit():
        raise ValueError(
            f"Account ID không hợp lệ: {account_id}"
        )

    date_start = str(row.get("date_start") or "")

    try:
        date.fromisoformat(date_start)
    except ValueError as exc:
        raise ValueError(
            f"Ngày date_start không hợp lệ: {date_start}"
        ) from exc

    results, result_type = get_message_results(
        row.get("actions")
    )

    normalized = {
        "level": level,

        "account_id": account_id,
        "account_name": (
            row.get("account_name")
            or account.get("name")
            or ""
        ),
        "account_currency": account.get("currency", ""),

        "campaign_id": str(row.get("campaign_id") or ""),
        "campaign_name": row.get("campaign_name") or "",

        "adset_id": str(row.get("adset_id") or ""),
        "adset_name": row.get("adset_name") or "",

        "ad_id": str(row.get("ad_id") or ""),
        "ad_name": row.get("ad_name") or "",

        "date_start": date_start,
        "date_stop": row.get("date_stop", ""),

        "spend": to_number(row.get("spend")) or 0,
        "impressions": to_integer(row.get("impressions")),
        "reach": to_integer(row.get("reach")),
        "clicks": to_integer(row.get("clicks")),

        "ctr": to_number(row.get("ctr")),
        "cpc": to_number(row.get("cpc")),

        "results": results,
        "result_type": result_type,
    }

    return normalized


def normalize_insights(
    rows: List[dict],
    account: dict,
    level: str,
) -> List[dict]:

    if level not in VALID_LEVELS:
        raise ValueError(f"Level không hợp lệ: {level}")

    output = []

    for row in rows:
        item = normalize_insight(
            row=row,
            account=account,
            level=level,
        )

        if level == "campaign" and not item["campaign_id"]:
            continue

        if level == "adset" and not item["adset_id"]:
            continue

        if level == "ad" and not item["ad_id"]:
            continue

        output.append(item)

    return output


def make_record_key(row: dict) -> str:

    level = row.get("level", "")
    account_id = str(row.get("account_id", ""))
    date_start = str(row.get("date_start", ""))

    if level not in VALID_LEVELS:
        return ""

    if not account_id or not date_start:
        return ""

    object_id = {
        "campaign": row.get("campaign_id", ""),
        "adset": row.get("adset_id", ""),
        "ad": row.get("ad_id", ""),
    }.get(level, "")

    if not object_id:
        return ""

    return "|".join([
        account_id,
        level,
        str(object_id),
        date_start,
    ])
