
from typing import Any, Dict, List, Optional


# Chỉ chọn một nhóm kết quả thống nhất:
# số cuộc trò chuyện bắt đầu qua quảng cáo.
MESSAGE_ACTION_TYPES = (
    "onsite_conversion.messaging_conversation_started_7d",
    "messaging_conversation_started_7d",
    "onsite_conversion.messaging_conversation_started",
    "messaging_conversation_started",
)


def to_number(value: Any) -> Optional[float]:
    """Chuyển dữ liệu Meta dạng chuỗi thành số."""
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
    """
    Lấy số cuộc trò chuyện bắt đầu.
    Không cộng các loại hành động khác nhau vào cùng một kết quả.
    """
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
    """Chuẩn hóa một dòng Insights thành cấu trúc Dashboard."""

    account_id = str(
        row.get("account_id")
        or account.get("account_id")
        or ""
    )

    account_name = (
        row.get("account_name")
        or account.get("name")
        or ""
    )

    currency = account.get("currency", "")

    results, result_type = get_message_results(
        row.get("actions")
    )

    normalized = {
        "level": level,

        "account_id": account_id,
        "account_name": account_name,
        "account_currency": currency,

        "campaign_id": str(row.get("campaign_id") or ""),
        "campaign_name": row.get("campaign_name") or "",

        "adset_id": str(row.get("adset_id") or ""),
        "adset_name": row.get("adset_name") or "",

        "ad_id": str(row.get("ad_id") or ""),
        "ad_name": row.get("ad_name") or "",

        "date_start": row.get("date_start", ""),
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
    """Chuẩn hóa danh sách Insights."""

    output = []

    for row in rows:
        item = normalize_insight(
            row=row,
            account=account,
            level=level,
        )

        # Bỏ dòng không có ngày hoặc ID cấp quảng cáo.
        if not item["date_start"]:
            continue

        if level == "campaign" and not item["campaign_id"]:
            continue

        if level == "adset" and not item["adset_id"]:
            continue

        if level == "ad" and not item["ad_id"]:
            continue

        output.append(item)

    return output


def make_record_key(row: dict) -> str:
    """
    Khóa duy nhất của một bản ghi:
    tài khoản + cấp + ID đối tượng + ngày.
    """

    level = row.get("level", "")
    account_id = row.get("account_id", "")
    date = row.get("date_start", "")

    object_id = {
        "campaign": row.get("campaign_id", ""),
        "adset": row.get("adset_id", ""),
        "ad": row.get("ad_id", ""),
    }.get(level, "")

    return "|".join([
        str(account_id),
        str(level),
        str(object_id),
        str(date),
    ])
