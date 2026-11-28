
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

from config import (
    ADS_DATA_FILE,
    ADS_DATA_DIR,
    ACCOUNTS_DATA_FILE,
)
from storage import _atomic_write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def migrate():
    if not os.path.isfile(ADS_DATA_FILE):
        raise FileNotFoundError(
            f"Không tìm thấy dữ liệu cũ: {ADS_DATA_FILE}"
        )

    logger.info("Đang đọc ads.json cũ...")

    with open(
        ADS_DATA_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        old_payload = json.load(file)

    if not isinstance(old_payload, dict):
        raise RuntimeError("ads.json cũ không hợp lệ.")

    old_rows = old_payload.get("data", [])
    old_accounts = old_payload.get("accounts", [])

    if not isinstance(old_rows, list):
        raise RuntimeError("ads.json: data không phải danh sách.")

    if not isinstance(old_accounts, list):
        raise RuntimeError("ads.json: accounts không phải danh sách.")

    logger.info(
        "Dữ liệu cũ: %s bản ghi, %s tài khoản.",
        len(old_rows),
        len(old_accounts),
    )

    # Không ghi đè dữ liệu phân mảnh đã tồn tại.
    if os.path.isdir(ADS_DATA_DIR):
        existing = [
            name
            for name in os.listdir(ADS_DATA_DIR)
            if os.path.isdir(
                os.path.join(ADS_DATA_DIR, name)
            )
        ]

        if existing:
            raise RuntimeError(
                "Thư mục ads/ đã có dữ liệu. "
                "Dừng để tránh ghi đè."
            )

    grouped = defaultdict(list)

    for index, row in enumerate(old_rows, start=1):

        if not isinstance(row, dict):
            raise RuntimeError(
                f"Bản ghi số {index} không phải object."
            )

        account_id = str(
            row.get("account_id", "")
        )

        row_date = str(
            row.get("date_start", "")
        )

        if not re.fullmatch(r"[0-9]+", account_id):
            raise RuntimeError(
                f"Bản ghi {index} thiếu account_id hợp lệ."
            )

        try:
            datetime.strptime(
                row_date,
                "%Y-%m-%d",
            )
        except ValueError as exc:
            raise RuntimeError(
                f"Bản ghi {index} có date_start không hợp lệ: "
                f"{row_date}"
            ) from exc

        month = row_date[:7]

        grouped[(account_id, month)].append(row)

    logger.info(
        "Đã phân nhóm thành %s file JSON.",
        len(grouped),
    )

    # Ghi từng file phân mảnh.
    total_written = 0

    for (account_id, month), rows in grouped.items():

        account_dir = os.path.join(
            ADS_DATA_DIR,
            account_id,
        )

        path = os.path.join(
            account_dir,
            f"{month}.json",
        )

        payload = {
            "account_id": account_id,
            "month": month,
            "updated_at": old_payload.get(
                "updated_at"
            ),
            "data": rows,
        }

        _atomic_write_json(
            path,
            payload,
        )

        total_written += len(rows)

        logger.info(
            "Đã chuyển: account=%s | month=%s | %s dòng",
            account_id,
            month,
            len(rows),
        )

    # Lưu danh sách tài khoản.
    account_map = {}

    for account in old_accounts:
        account_id = str(
            account.get("account_id", "")
        )

        if account_id:
            account_map[account_id] = account

    accounts_payload = {
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
        accounts_payload,
    )

    # Đọc lại toàn bộ phân mảnh để xác minh.
    logger.info("Đang kiểm tra dữ liệu sau chuyển đổi...")

    total_readback = 0

    for (account_id, month) in grouped:

        path = os.path.join(
            ADS_DATA_DIR,
            account_id,
            f"{month}.json",
        )

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)

        total_readback += len(
            payload.get("data", [])
        )

    if total_written != len(old_rows):
        raise RuntimeError(
            "Số bản ghi ghi ra không khớp dữ liệu gốc."
        )

    if total_readback != len(old_rows):
        raise RuntimeError(
            "Số bản ghi đọc lại không khớp dữ liệu gốc."
        )

    logger.info("====================================")
    logger.info("CHUYỂN ĐỔI THÀNH CÔNG")
    logger.info("Bản ghi gốc: %s", len(old_rows))
    logger.info("Bản ghi đã ghi: %s", total_written)
    logger.info("Bản ghi đọc lại: %s", total_readback)
    logger.info("Số file phân mảnh: %s", len(grouped))
    logger.info("Số tài khoản: %s", len(account_map))
    logger.info("====================================")

    # Chỉ xóa file cũ sau khi xác minh đủ số lượng.
    # Git history vẫn giữ bản ads.json ở commit trước.
    os.remove(ADS_DATA_FILE)

    logger.info(
        "Đã xóa ads.json cũ sau khi xác minh thành công."
    )


if __name__ == "__main__":
    migrate()
