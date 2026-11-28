
import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone

from config import (
    ADS_DATA_DIR,
    ACCOUNTS_DATA_FILE,
    RETENTION_DAYS,
    SYNC_STATE_FILE,
    retention_cutoff,
)
from processor import make_record_key

logger = logging.getLogger(__name__)


# =====================================
# JSON HELPERS
# =====================================

def _read_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"Không đọc được JSON {path}: {exc}"
        ) from exc


def _atomic_write_json(path, payload):
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
                separators=(",", ":"),
            )
            file.write("\n")

        os.replace(temp_path, path)

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


# =====================================
# FILE PATH
# =====================================

def _account_month_file(account_id, month):
    account_id = str(account_id)

    if not re.fullmatch(r"[0-9]+", account_id):
        raise ValueError(
            f"Account ID không hợp lệ: {account_id}"
        )

    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError(
            f"Tháng không hợp lệ: {month}"
        )

    account_dir = os.path.join(
        ADS_DATA_DIR,
        account_id,
    )

    return os.path.join(
        account_dir,
        f"{month}.json",
    )


def _month_range(since, until):
    start = date.fromisoformat(since)
    end = date.fromisoformat(until)

    if start > end:
        raise ValueError("since không được lớn hơn until.")

    current = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)

    while current <= last:
        yield current.strftime("%Y-%m")

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(
                current.year,
                current.month + 1,
                1,
            )


# =====================================
# ACCOUNT STORAGE
# =====================================

def save_accounts(accounts):
    account_map = {}

    for account in accounts:
        account_id = str(
            account.get("account_id", "")
        )

        if account_id:
            account_map[account_id] = account

    payload = {
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "total_accounts": len(account_map),
        "accounts": sorted(
            account_map.values(),
            key=lambda item: str(
                item.get("name", "")
            ).lower(),
        ),
    }

    _atomic_write_json(
        ACCOUNTS_DATA_FILE,
        payload,
    )

    logger.info(
        "Đã lưu danh sách %s tài khoản.",
        len(account_map),
    )


def load_accounts():
    payload = _read_json(
        ACCOUNTS_DATA_FILE,
        {
            "updated_at": None,
            "total_accounts": 0,
            "accounts": [],
        },
    )

    if not isinstance(payload, dict):
        raise RuntimeError(
            "accounts.json không hợp lệ."
        )

    accounts = payload.get("accounts", [])

    if not isinstance(accounts, list):
        raise RuntimeError(
            "accounts.json: accounts phải là danh sách."
        )

    return payload


# =====================================
# SYNC STATE
# =====================================

def load_sync_state():
    state = _read_json(
        SYNC_STATE_FILE,
        {
            "initial_sync_completed": False,
            "last_successful_sync": None,
        },
    )

    if not isinstance(state, dict):
        raise RuntimeError(
            "Cấu trúc sync_state.json không hợp lệ."
        )

    return state


def save_sync_state(state):
    _atomic_write_json(
        SYNC_STATE_FILE,
        state,
    )


# =====================================
# READ ONE ACCOUNT / MONTH
# =====================================

def load_month_data(account_id, month):
    path = _account_month_file(
        account_id,
        month,
    )

    payload = _read_json(
        path,
        {
            "account_id": str(account_id),
            "month": month,
            "updated_at": None,
            "data": [],
        },
    )

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"File dữ liệu không hợp lệ: {path}"
        )

    rows = payload.get("data", [])

    if not isinstance(rows, list):
        raise RuntimeError(
            f"Trường data không hợp lệ: {path}"
        )

    return payload


# =====================================
# UPDATE ONE ACCOUNT / DATE RANGE
# =====================================

def update_ads(
    account,
    new_rows,
    since,
    until,
    level,
):
    """
    Chỉ cập nhật những file tháng thuộc:
    - Tài khoản đang đồng bộ.
    - Cấp dữ liệu đang đồng bộ.
    - Khoảng ngày đang đồng bộ.
    """

    account_id = str(
        account.get("account_id", "")
    )

    if not account_id:
        raise ValueError("Thiếu account_id.")

    if level not in ("campaign", "adset", "ad"):
        raise ValueError(f"Level không hợp lệ: {level}")

    if date.fromisoformat(since) > date.fromisoformat(until):
        raise ValueError("Khoảng ngày không hợp lệ.")

    new_rows_by_month = {}

    for row in new_rows:

        row_account_id = str(row.get("account_id", ""))
        row_level = row.get("level")
        row_date = str(row.get("date_start", ""))
    
        if row_account_id != account_id:
            raise ValueError(
                f"Sai account_id: mong đợi {account_id}, "
                f"nhận được {row_account_id}"
            )
    
        if row_level != level:
            raise ValueError(
                f"Sai level: mong đợi {level}, "
                f"nhận được {row_level}"
            )
    
        try:
            date.fromisoformat(row_date)
        except ValueError as exc:
            raise ValueError(
                f"date_start không hợp lệ: {row_date}"
            ) from exc
    
        if not since <= row_date <= until:
            raise ValueError(
                f"Bản ghi nằm ngoài khoảng đồng bộ: {row_date}"
            )
    
        month = row_date[:7]
    
        new_rows_by_month.setdefault(
            month,
            [],
        ).append(row)

    total_before = 0
    total_new = 0
    total_after = 0

    for month in _month_range(since, until):

        path = _account_month_file(
            account_id,
            month,
        )

        payload = load_month_data(
            account_id,
            month,
        )

        existing_rows = payload.get("data", [])

        total_before += len(existing_rows)

        merged = {}

        # Giữ dữ liệu ngoài phạm vi làm mới.
        for row in existing_rows:

            row_date = str(
                row.get("date_start", "")
            )

            is_refreshed = (
                str(row.get("account_id", "")) == account_id
                and row.get("level") == level
                and since <= row_date <= until
            )

            if is_refreshed:
                continue

            key = make_record_key(row)

            if key:
                merged[key] = row

        # Thêm dữ liệu mới.
        month_new_rows = new_rows_by_month.get(
            month,
            [],
        )

        for row in month_new_rows:

            key = make_record_key(row)

            if key:
                merged[key] = row

        # Loại dữ liệu quá hạn.
        cutoff = retention_cutoff()

        retained_rows = [
            row
            for row in merged.values()
            if str(row.get("date_start", "")) >= cutoff
        ]

        retained_rows.sort(
            key=lambda row: (
                row.get("date_start", ""),
                row.get("level", ""),
                row.get("campaign_id", ""),
                row.get("adset_id", ""),
                row.get("ad_id", ""),
            )
        )

        updated_payload = {
            "account_id": account_id,
            "month": month,
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "data": retained_rows,
        }

        _atomic_write_json(
            path,
            updated_payload,
        )

        total_new += len(month_new_rows)
        total_after += len(retained_rows)

        logger.info(
            "Đã lưu phân mảnh: account=%s | month=%s | "
            "level=%s | %s bản ghi",
            account_id,
            month,
            level,
            len(retained_rows),
        )

    updated_at = datetime.now(
        timezone.utc
    ).isoformat()

    logger.info(
        "Cập nhật phân mảnh: account=%s | level=%s | "
        "dữ liệu mới=%s",
        account_id,
        level,
        total_new,
    )

    return updated_at

# =====================================
# SAFE AUDIT UPSERT
# =====================================

def upsert_audit_ads(
    account,
    new_rows,
    since,
    until,
    level,
):
    """
    Đối chiếu dữ liệu Meta với dữ liệu đang lưu.

    - Bản ghi giống nhau: giữ nguyên.
    - Bản ghi khác nhau: cập nhật.
    - Bản ghi mới: bổ sung.
    - Không xóa bản ghi cũ nếu API không trả về.
    - Không chạy retention cleanup.
    - Chỉ ghi file khi thực sự có thay đổi.
    """

    account_id = str(
        account.get("account_id", "")
    )

    if not re.fullmatch(r"[0-9]+", account_id):
        raise ValueError(
            f"Account ID không hợp lệ: {account_id}"
        )

    if level not in ("campaign", "adset", "ad"):
        raise ValueError(
            f"Level không hợp lệ: {level}"
        )

    start = date.fromisoformat(since)
    end = date.fromisoformat(until)

    if start > end:
        raise ValueError("Khoảng ngày không hợp lệ.")

    rows_by_month = {}

    # Kiểm tra toàn bộ dữ liệu mới trước khi ghi.
    for row in new_rows:

        if not isinstance(row, dict):
            raise ValueError(
                "Bản ghi mới không phải JSON object."
            )

        if str(row.get("account_id", "")) != account_id:
            raise ValueError(
                f"Sai account_id trong dữ liệu mới: {row}"
            )

        if row.get("level") != level:
            raise ValueError(
                f"Sai level trong dữ liệu mới: {row}"
            )

        row_date = str(row.get("date_start", ""))

        try:
            parsed_date = date.fromisoformat(row_date)
        except ValueError as exc:
            raise ValueError(
                f"date_start không hợp lệ: {row_date}"
            ) from exc

        if not start <= parsed_date <= end:
            raise ValueError(
                f"Bản ghi nằm ngoài khoảng rà soát: {row_date}"
            )

        key = make_record_key(row)

        if not key:
            raise ValueError(
                "Không tạo được khóa bản ghi mới."
            )

        month = row_date[:7]

        month_rows = rows_by_month.setdefault(
            month,
            {},
        )

        if key in month_rows and month_rows[key] != row:
            raise RuntimeError(
                f"API trả về hai bản ghi khác nhau cùng khóa: {key}"
            )

        month_rows[key] = row

    stats = {
        "checked": 0,
        "unchanged": 0,
        "updated": 0,
        "added": 0,
        "files_changed": 0,
    }

    for month in _month_range(since, until):

        path = _account_month_file(
            account_id,
            month,
        )

        payload = load_month_data(
            account_id,
            month,
        )

        existing_rows = payload.get("data", [])

        if not isinstance(existing_rows, list):
            raise RuntimeError(
                f"Dữ liệu tháng không hợp lệ: {path}"
            )

        # Lập chỉ mục dữ liệu cũ.
        existing_index = {}

        for index, row in enumerate(existing_rows):

            if not isinstance(row, dict):
                raise RuntimeError(
                    f"Bản ghi cũ không hợp lệ: {path}"
                )

            key = make_record_key(row)

            if not key:
                raise RuntimeError(
                    f"Bản ghi cũ không có khóa hợp lệ: {path}"
                )

            if key in existing_index:
                raise RuntimeError(
                    f"Phát hiện khóa trùng trong dữ liệu cũ: {key}. "
                    "Dừng để tránh ghi đè sai."
                )

            existing_index[key] = index

        merged_rows = list(existing_rows)

        month_new_rows = rows_by_month.get(
            month,
            {},
        )

        changed = False

        for key, new_row in month_new_rows.items():

            stats["checked"] += 1

            if key in existing_index:

                index = existing_index[key]
                old_row = merged_rows[index]

                if old_row == new_row:
                    stats["unchanged"] += 1
                    continue

                merged_rows[index] = new_row
                stats["updated"] += 1
                changed = True

            else:

                existing_index[key] = len(merged_rows)
                merged_rows.append(new_row)

                stats["added"] += 1
                changed = True

        # Chỉ ghi file khi có dữ liệu mới hoặc dữ liệu thay đổi.
        if changed:

            merged_rows.sort(
                key=lambda row: (
                    row.get("date_start", ""),
                    row.get("level", ""),
                    row.get("campaign_id", ""),
                    row.get("adset_id", ""),
                    row.get("ad_id", ""),
                )
            )

            updated_payload = {
                "account_id": account_id,
                "month": month,
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "data": merged_rows,
            }

            _atomic_write_json(
                path,
                updated_payload,
            )

            stats["files_changed"] += 1

            logger.info(
                "Audit cập nhật: account=%s | month=%s | "
                "level=%s | số bản ghi=%s",
                account_id,
                month,
                level,
                len(merged_rows),
            )

    return stats

# =====================================
# RETENTION CLEANUP
# =====================================

def prune_expired_data():
    """
    Xóa file tháng đã cũ hơn thời hạn lưu trữ.
    Với tháng chứa ngày cutoff, lọc từng bản ghi.
    """

    if not os.path.isdir(ADS_DATA_DIR):
        return

    cutoff = retention_cutoff()
    cutoff_month = cutoff[:7]

    deleted_files = 0
    deleted_rows = 0

    for account_id in os.listdir(ADS_DATA_DIR):

        account_dir = os.path.join(
            ADS_DATA_DIR,
            account_id,
        )

        if not os.path.isdir(account_dir):
            continue

        for filename in os.listdir(account_dir):

            if not filename.endswith(".json"):
                continue

            month = filename[:-5]

            if not re.fullmatch(r"\d{4}-\d{2}", month):
                continue

            path = os.path.join(
                account_dir,
                filename,
            )

            if month < cutoff_month:
                os.remove(path)
                deleted_files += 1
                continue

            if month == cutoff_month:

                payload = _read_json(
                    path,
                    {"data": []},
                )

                old_rows = payload.get("data", [])

                retained = [
                    row
                    for row in old_rows
                    if str(row.get("date_start", "")) >= cutoff
                ]

                deleted_rows += (
                    len(old_rows) - len(retained)
                )

                if retained:
                    payload["data"] = retained
                    payload["updated_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()

                    _atomic_write_json(
                        path,
                        payload,
                    )
                else:
                    os.remove(path)
                    deleted_files += 1

    logger.info(
        "Dọn dữ liệu quá hạn: %s file, %s bản ghi.",
        deleted_files,
        deleted_rows,
    )


# =====================================
# STORAGE STATISTICS
# =====================================

def get_storage_stats():
    total_rows = 0
    total_files = 0
    total_bytes = 0

    if not os.path.isdir(ADS_DATA_DIR):
        return {
            "rows": 0,
            "files": 0,
            "bytes": 0,
        }

    for account_id in os.listdir(ADS_DATA_DIR):

        account_dir = os.path.join(
            ADS_DATA_DIR,
            account_id,
        )

        if not os.path.isdir(account_dir):
            continue

        for filename in os.listdir(account_dir):

            if not filename.endswith(".json"):
                continue

            path = os.path.join(
                account_dir,
                filename,
            )

            payload = _read_json(
                path,
                {"data": []},
            )

            rows = payload.get("data", [])

            if not isinstance(rows, list):
                raise RuntimeError(
                    f"Dữ liệu không hợp lệ: {path}"
                )

            total_rows += len(rows)
            total_files += 1
            total_bytes += os.path.getsize(path)

    return {
        "rows": total_rows,
        "files": total_files,
        "bytes": total_bytes,
    }
