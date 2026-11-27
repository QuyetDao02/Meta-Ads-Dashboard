
import logging
import sys

from config import (
    AD_LEVELS,
    INITIAL_SYNC_DAYS,
    incremental_date_range,
    initial_date_range,
    validate_config,
)

from meta_api import MetaAPIClient
from processor import normalize_insights
from storage import (
    load_ads,
    load_sync_state,
    merge_ads,
    save_ads,
    save_sync_state,
)


# ==============================
# LOGGING
# ==============================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def run():
    logger.info("====================================")
    logger.info("META ADS DASHBOARD - START SYNC")
    logger.info("====================================")

    # 1. Kiểm tra cấu hình và Access Token.
    validate_config()

    # 2. Khởi tạo Meta API.
    client = MetaAPIClient()

    profile = client.validate_token()

    logger.info(
        "Token hợp lệ. Người dùng/API identity: %s",
        profile.get("name", profile.get("id", "Unknown")),
    )

    # 3. Tìm tất cả tài khoản quảng cáo.
    accounts = client.get_ad_accounts()

    if not accounts:
        raise RuntimeError(
            "Token không tìm thấy tài khoản quảng cáo nào. "
            "Kiểm tra quyền ads_read và quyền truy cập tài khoản."
        )

    logger.info("Tìm thấy %s tài khoản quảng cáo.", len(accounts))

    # 4. Xác định đồng bộ lần đầu hay đồng bộ định kỳ.
    state = load_sync_state()

    initial_completed = state.get(
        "initial_sync_completed", False
    )

    if initial_completed:
        since, until = incremental_date_range()
        logger.info(
            "Đồng bộ định kỳ: %s đến %s",
            since,
            until,
        )
    else:
        since, until = initial_date_range()
        logger.info(
            "Đồng bộ lịch sử lần đầu: %s đến %s",
            since,
            until,
        )

    # 5. Tải dữ liệu từ cả ba cấp.
    all_rows = []

    for account in accounts:
        account_id = account["account_id"]
        account_name = account.get("name", "")
        currency = account.get("currency", "")

        logger.info(
            "Đang xử lý tài khoản %s | %s | %s",
            account_id,
            account_name,
            currency,
        )

        for level in AD_LEVELS:
            logger.info(
                "Đang lấy Insights: account=%s, level=%s",
                account_id,
                level,
            )

            raw_rows = client.get_insights(
                account_id=account_id,
                level=level,
                since=since,
                until=until,
            )

            normalized_rows = normalize_insights(
                rows=raw_rows,
                account=account,
                level=level,
            )

            all_rows.extend(normalized_rows)

            logger.info(
                "Hoàn thành %s: %s bản ghi",
                level,
                len(normalized_rows),
            )

    # 6. Chỉ ghi dữ liệu sau khi toàn bộ API thành công.
    # Nếu một tài khoản hoặc cấp bị lỗi, không cập nhật kho JSON.
    existing_payload = load_ads()

    updated_payload = merge_ads(
        existing_payload=existing_payload,
        new_rows=all_rows,
        accounts=accounts,
        since=since,
        until=until,
        levels=AD_LEVELS,
    )

    save_ads(updated_payload)

    # 7. Đánh dấu hoàn thành đồng bộ.
    # Chỉ cập nhật trạng thái sau khi ads.json đã lưu thành công.
    save_sync_state({
        "initial_sync_completed": True,
        "last_successful_sync": updated_payload["updated_at"],
        "last_sync_since": since,
        "last_sync_until": until,
        "accounts_count": len(accounts),
        "records_count": len(updated_payload["data"]),
        "initial_sync_days": INITIAL_SYNC_DAYS,
    })

    logger.info("====================================")
    logger.info("SYNC COMPLETED SUCCESSFULLY")
    logger.info("Accounts: %s", len(accounts))
    logger.info("New/updated rows: %s", len(all_rows))
    logger.info("Total stored rows: %s", len(updated_payload["data"]))
    logger.info("====================================")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        logger.exception("META ADS SYNC FAILED")
        sys.exit(1)
