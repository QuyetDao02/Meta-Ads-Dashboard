import logging
import sys
from collections import defaultdict
from datetime import date

from config import AD_LEVELS
from meta_api import MetaAPIClient
from processor import normalize_insights
from storage import (
    load_accounts,
    upsert_audit_ads,
)


# =====================================
# AUDIT CONFIG
# =====================================

AUDIT_SINCE = "2026-11-24"
AUDIT_UNTIL = "2026-11-28"

LEVELS = ("campaign", "adset", "ad")


# =====================================
# LOGGING
# =====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


# =====================================
# VALIDATION
# =====================================

def validate_audit_config():

    since = date.fromisoformat(AUDIT_SINCE)
    until = date.fromisoformat(AUDIT_UNTIL)

    if since > until:
        raise ValueError(
            "AUDIT_SINCE không được lớn hơn AUDIT_UNTIL."
        )

    if tuple(LEVELS) != tuple(AD_LEVELS):
        raise RuntimeError(
            "Thứ tự hoặc cấu hình AD_LEVELS không khớp. "
            "Dừng để tránh rà soát sai cấp dữ liệu."
        )


# =====================================
# LOAD SAVED ACCOUNTS
# =====================================

def get_audit_accounts(client):

    saved_payload = load_accounts()

    saved_accounts = saved_payload.get(
        "accounts",
        [],
    )

    if not saved_accounts:
        raise RuntimeError(
            "accounts.json không có danh sách tài khoản. "
            "Không thể bắt đầu rà soát."
        )

    saved_ids = []

    for account in saved_accounts:

        account_id = str(
            account.get("account_id", "")
        )

        if not account_id.isdigit():
            raise RuntimeError(
                f"Account ID không hợp lệ trong accounts.json: "
                f"{account_id}"
            )

        if account_id in saved_ids:
            raise RuntimeError(
                f"Account ID bị trùng trong accounts.json: "
                f"{account_id}"
            )

        saved_ids.append(account_id)

    stored_total = saved_payload.get("total_accounts")

    if (
        stored_total is not None
        and int(stored_total) != len(saved_accounts)
    ):
        raise RuntimeError(
            "Số tài khoản trong accounts.json không khớp "
            "với total_accounts."
        )

    logger.info(
        "Đã đọc %s tài khoản đã lưu.",
        len(saved_accounts),
    )

    # Kiểm tra quyền truy cập hiện tại.
    available_accounts = client.get_ad_accounts()

    available_map = {
        str(account["account_id"]): account
        for account in available_accounts
    }

    missing_ids = [
        account_id
        for account_id in saved_ids
        if account_id not in available_map
    ]

    if missing_ids:
        raise RuntimeError(
            f"Có {len(missing_ids)} tài khoản đã lưu "
            "không còn truy cập được. "
            "Dừng trước khi ghi dữ liệu. "
            f"Ví dụ ID: {', '.join(missing_ids[:10])}"
        )

    # Giữ đúng danh sách và thứ tự tài khoản đã lưu.
    # Dùng metadata mới từ API cho quá trình chuẩn hóa.
    accounts = [
        available_map[account_id]
        for account_id in saved_ids
    ]

    logger.info(
        "Kiểm tra quyền truy cập thành công: %s/%s tài khoản.",
        len(accounts),
        len(saved_ids),
    )

    return accounts


# =====================================
# RUN AUDIT
# =====================================

def run_audit():

    validate_audit_config()

    logger.info("====================================")
    logger.info("META ADS - RECENT DATA AUDIT")
    logger.info("====================================")

    logger.info(
        "Khoảng rà soát: %s đến %s",
        AUDIT_SINCE,
        AUDIT_UNTIL,
    )

    logger.info(
        "Các cấp dữ liệu: %s",
        ", ".join(LEVELS),
    )

    client = MetaAPIClient()

    profile = client.validate_token()

    logger.info(
        "Token hợp lệ: %s",
        profile.get("name", profile.get("id", "Unknown")),
    )

    accounts = get_audit_accounts(client)

    totals = defaultdict(int)

    total_tasks = len(accounts) * len(LEVELS)
    completed_tasks = 0

    logger.info(
        "Tổng số tác vụ rà soát: %s",
        total_tasks,
    )

    for account in accounts:

        account_id = str(account["account_id"])

        for level in LEVELS:

            logger.info(
                "------------------------------------"
            )

            logger.info(
                "Bắt đầu rà soát: account=%s | level=%s",
                account_id,
                level,
            )

            # Lấy toàn bộ các trang API.
            raw_rows = client.get_insights(
                account_id=account_id,
                level=level,
                since=AUDIT_SINCE,
                until=AUDIT_UNTIL,
            )

            # Chuẩn hóa trước khi ghi.
            normalized_rows = normalize_insights(
                rows=raw_rows,
                account=account,
                level=level,
            )

            # Chỉ cập nhật hoặc bổ sung.
            # Không xóa dữ liệu cũ không xuất hiện trong API.
            result = upsert_audit_ads(
                account=account,
                new_rows=normalized_rows,
                since=AUDIT_SINCE,
                until=AUDIT_UNTIL,
                level=level,
            )

            for key, value in result.items():
                totals[key] += value

            completed_tasks += 1

            logger.info(
                "Hoàn thành %s/%s | account=%s | level=%s | "
                "API=%s | giữ nguyên=%s | cập nhật=%s | "
                "thêm mới=%s",
                completed_tasks,
                total_tasks,
                account_id,
                level,
                result["checked"],
                result["unchanged"],
                result["updated"],
                result["added"],
            )

    # Chỉ đến đây khi tất cả tác vụ đã hoàn thành.
    logger.info("====================================")
    logger.info("RECENT DATA AUDIT COMPLETED")
    logger.info("====================================")

    logger.info(
        "Khoảng ngày: %s đến %s",
        AUDIT_SINCE,
        AUDIT_UNTIL,
    )

    logger.info(
        "Tài khoản: %s",
        len(accounts),
    )

    logger.info(
        "Tác vụ hoàn thành: %s/%s",
        completed_tasks,
        total_tasks,
    )

    logger.info(
        "Bản ghi được đối chiếu: %s",
        totals["checked"],
    )

    logger.info(
        "Bản ghi giữ nguyên: %s",
        totals["unchanged"],
    )

    logger.info(
        "Bản ghi được cập nhật: %s",
        totals["updated"],
    )

    logger.info(
        "Bản ghi mới được bổ sung: %s",
        totals["added"],
    )

    logger.info(
        "File JSON thực sự thay đổi: %s",
        totals["files_changed"],
    )

    logger.info(
        "Không thay đổi sync_state.json."
    )

    logger.info(
        "Không xóa dữ liệu cũ không xuất hiện trong API."
    )

    logger.info("====================================")


if __name__ == "__main__":

    try:
        run_audit()

    except Exception:
        logger.exception(
            "RECENT DATA AUDIT FAILED. "
            "Không được xem lần rà soát này là hoàn tất."
        )
        sys.exit(1)
