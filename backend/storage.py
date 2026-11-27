
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List

from config import (
    ADS_DATA_FILE,
    RETENTION_DAYS,
    SYNC_STATE_FILE,
    retention_cutoff,
)
from processor import make_record_key

logger = logging.getLogger(__name__)


def _read_json(path: str, default):
    """Đọc JSON. Nếu file không tồn tại thì dùng giá trị mặc định."""
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        # Không tự tạo file rỗng khi JSON lỗi vì có thể làm mất dữ liệu.
        raise RuntimeError(
            f"Không đọc được JSON {path}: {exc}"
        ) from exc


def load_ads():
    """Đọc kho dữ liệu quảng cáo hiện tại."""
    payload = _read_json(
        ADS_DATA_FILE,
        {
            "updated_at": None,
            "retention_days": RETENTION_DAYS,
            "accounts": [],
            "data": [],
        },
    )

    if not isinstance(payload, dict):
        raise RuntimeError("Cấu trúc ads.json không hợp lệ.")

    if not isinstance(payload.get("data", []), list):
        raise RuntimeError("ads.json: trường data phải là danh sách.")

    if not isinstance(payload.get("accounts", []), list):
        raise RuntimeError("ads.json: trường accounts phải là danh sách.")

    return payload


def load_sync_state():
    """Đọc trạng thái đồng bộ."""
    state = _read_json(
        SYNC_STATE_FILE,
        {
            "initial_sync_completed": False,
            "last_successful_sync": None,
        },
    )

    if not isinstance(state, dict):
        raise RuntimeError("Cấu trúc sync_state.json không hợp lệ.")

    return state


def _atomic_write_json(path: str, payload: dict):
    """
    Ghi JSON an toàn:
    ghi file tạm trước, sau đó thay thế file chính.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        prefix=".tmp_",
        suffix=".json",
        dir=directory,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        os.replace(temp_path, path)

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def save_ads(payload: dict):
    _atomic_write_json(ADS_DATA_FILE, payload)


def save_sync_state(state: dict):
    _atomic_write_json(SYNC_STATE_FILE, state)


def merge_ads(
    existing_payload: dict,
    new_rows: List[dict],
    accounts: List[dict],
    since: str,
    until: str,
    levels: tuple,
):
    """
    Cập nhật kho JSON.

    - Xóa bản ghi thuộc tài khoản/cấp/ngày đang được đồng bộ.
    - Chèn dữ liệu mới từ Meta.
    - Giữ nguyên dữ liệu ngoài khoảng đồng bộ.
    - Chống trùng bằng record key.
    - Loại dữ liệu quá thời hạn lưu trữ.
    """

    existing_rows = existing_payload.get("data", [])

    refreshed_account_ids = {
        str(account.get("account_id", ""))
        for account in accounts
    }

    refreshed_levels = set(levels)

    merged = {}

    # Giữ các dòng không thuộc vùng đang được đồng bộ.
    for row in existing_rows:
        account_id = str(row.get("account_id", ""))
        level = row.get("level", "")
        date = row.get("date_start", "")

        is_refreshed = (
            account_id in refreshed_account_ids
            and level in refreshed_levels
            and since <= date <= until
        )

        if not is_refreshed:
            key = make_record_key(row)
            merged[key] = row

    # Chèn dữ liệu vừa tải từ Meta.
    for row in new_rows:
        key = make_record_key(row)
        merged[key] = row

    # Giữ lại dữ liệu trong thời hạn lưu trữ.
    cutoff = retention_cutoff()

    retained_rows = [
        row
        for row in merged.values()
        if row.get("date_start", "") >= cutoff
    ]

    # Sắp xếp để JSON ổn định, dễ kiểm tra thay đổi.
    retained_rows.sort(
        key=lambda row: (
            row.get("date_start", ""),
            row.get("account_id", ""),
            row.get("level", ""),
            row.get("campaign_id", ""),
            row.get("adset_id", ""),
            row.get("ad_id", ""),
        )
    )

    # Cập nhật thông tin tài khoản nhưng vẫn giữ tài khoản
    # cũ nếu lần đồng bộ hiện tại không trả về tài khoản đó.
    account_map = {}

    for account in existing_payload.get("accounts", []):
        account_id = str(account.get("account_id", ""))
        if account_id:
            account_map[account_id] = account

    for account in accounts:
        account_id = str(account.get("account_id", ""))
        if account_id:
            account_map[account_id] = account

    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "retention_days": RETENTION_DAYS,
        "accounts": sorted(
            account_map.values(),
            key=lambda item: str(item.get("name", "")).lower(),
        ),
        "data": retained_rows,
    }

    logger.info(
        "Kho dữ liệu: %s bản ghi trước, %s bản ghi mới, "
        "%s bản ghi sau cập nhật.",
        len(existing_rows),
        len(new_rows),
        len(retained_rows),
    )

    return result
