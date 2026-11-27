
import logging
import os
import sys
import time

from datetime import date, timedelta

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


# =====================================
# CONFIG
# =====================================

PROCESS_BUDGET_SECONDS = int(
    os.getenv("PROCESS_BUDGET_SECONDS", "240")
)

CHUNK_DAYS = int(
    os.getenv("INITIAL_SYNC_CHUNK_DAYS", "7")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


# =====================================
# DATE HELPERS
# =====================================

def build_date_chunks(since, until, chunk_days=7):
    """
    Chia khoảng thời gian thành các đoạn ngày liên tiếp.
    Ví dụ: 01-01 đến 14-01, chunk_days=7
    -> 01-01 đến 07-01
    -> 08-01 đến 14-01
    """

    start = date.fromisoformat(since)
    end = date.fromisoformat(until)

    chunks = []
    current = start

    while current <= end:

        chunk_end = min(
            current + timedelta(days=chunk_days - 1),
            end,
        )

        chunks.append((
            current.isoformat(),
            chunk_end.isoformat(),
        ))

        current = chunk_end + timedelta(days=1)

    return chunks


def get_account_map(accounts):
    return {
        str(account["account_id"]): account
        for account in accounts
    }


# =====================================
# PROCESS ONE TASK
# =====================================

def process_task(
    client,
    account,
    level,
    since,
    until,
    accounts,
):
    account_id = account["account_id"]

    logger.info(
        "Đang lấy dữ liệu: account=%s | level=%s | %s đến %s",
        account_id,
        level,
        since,
        until,
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

    # Lưu ngay sau khi tác vụ này thành công.
    existing_payload = load_ads()

    updated_payload = merge_ads(
        existing_payload=existing_payload,
        new_rows=normalized_rows,
        accounts=accounts,
        since=since,
        until=until,
        levels=(level,),
    )

    save_ads(updated_payload)

    logger.info(
        "Đã lưu: account=%s | level=%s | %s bản ghi",
        account_id,
        level,
        len(normalized_rows),
    )

    return len(normalized_rows), updated_payload


# =====================================
# INITIAL SYNC
# =====================================

def run_initial_sync(
    client,
    accounts,
    state,
    start_time,
):
    account_map = get_account_map(accounts)

    # Khởi tạo tiến độ lần đầu.
    if "initial_sync_account_ids" not in state:

        since, until = initial_date_range()

        account_ids = sorted(account_map.keys())

        state["initial_sync_since"] = since
        state["initial_sync_until"] = until
        state["initial_sync_account_ids"] = account_ids
        state["initial_sync_task_index"] = 0

        state["initial_sync_completed"] = False

        save_sync_state(state)

        logger.info(
            "Bắt đầu tạo tiến độ đồng bộ lịch sử."
        )

    since = state["initial_sync_since"]
    until = state["initial_sync_until"]

    account_ids = state["initial_sync_account_ids"]

    chunks = build_date_chunks(
        since,
        until,
        CHUNK_DAYS,
    )

    tasks = []

    # Thứ tự tác vụ được giữ cố định giữa các lần chạy.
    for account_id in account_ids:

        for level in AD_LEVELS:

            for chunk_since, chunk_until in chunks:

                tasks.append((
                    account_id,
                    level,
                    chunk_since,
                    chunk_until,
                ))

    total_tasks = len(tasks)

    task_index = int(
        state.get("initial_sync_task_index", 0)
    )

    logger.info(
        "Tiến độ lịch sử: %s/%s tác vụ.",
        task_index,
        total_tasks,
    )

    total_rows = 0

    while task_index < total_tasks:

        # Chừa thời gian để GitHub commit dữ liệu.
        elapsed = time.monotonic() - start_time

        if elapsed >= PROCESS_BUDGET_SECONDS:
            break

        account_id, level, task_since, task_until = (
            tasks[task_index]
        )

        account = account_map.get(account_id)

        if account is None:
            raise RuntimeError(
                f"Tài khoản {account_id} không còn "
                "trong danh sách tài khoản truy cập được. "
                "Không thể tiếp tục tiến độ lịch sử."
            )

        rows_count, updated_payload = process_task(
            client=client,
            account=account,
            level=level,
            since=task_since,
            until=task_until,
            accounts=accounts,
        )

        total_rows += rows_count

        # Chỉ tăng tiến độ sau khi dữ liệu đã lưu thành công.
        task_index += 1

        state["initial_sync_task_index"] = task_index
        state["initial_sync_tasks_total"] = total_tasks
        state["last_successful_sync"] = updated_payload["updated_at"]

        save_sync_state(state)

        logger.info(
            "Lịch sử: %s/%s tác vụ hoàn thành.",
            task_index,
            total_tasks,
        )

    if task_index >= total_tasks:

        state["initial_sync_completed"] = True
        state["last_successful_sync"] = (
            load_ads()["updated_at"]
        )

        state.pop("initial_sync_account_ids", None)
        state.pop("initial_sync_task_index", None)
        state.pop("initial_sync_tasks_total", None)
        state.pop("initial_sync_since", None)
        state.pop("initial_sync_until", None)

        save_sync_state(state)

        logger.info(
            "ĐÃ HOÀN THÀNH ĐỒNG BỘ LỊCH SỬ %s NGÀY.",
            INITIAL_SYNC_DAYS,
        )

    else:

        logger.info(
            "Tạm dừng an toàn. Đã hoàn thành %s/%s tác vụ. "
            "Lần chạy tiếp theo sẽ tiếp tục từ vị trí này.",
            task_index,
            total_tasks,
        )

    return total_rows


# =====================================
# INCREMENTAL SYNC
# =====================================

def run_incremental_sync(
    client,
    accounts,
    state,
    start_time,
):
    account_map = get_account_map(accounts)

    # Nếu lần trước chưa hoàn thành, tiếp tục đúng khoảng ngày đó.
    if "incremental_account_ids" not in state:

        since, until = incremental_date_range()

        state["incremental_since"] = since
        state["incremental_until"] = until
        state["incremental_account_ids"] = sorted(
            account_map.keys()
        )
        state["incremental_task_index"] = 0

        save_sync_state(state)

    since = state["incremental_since"]
    until = state["incremental_until"]

    account_ids = state["incremental_account_ids"]

    tasks = [
        (account_id, level)
        for account_id in account_ids
        for level in AD_LEVELS
    ]

    total_tasks = len(tasks)

    task_index = int(
        state.get("incremental_task_index", 0)
    )

    logger.info(
        "Đồng bộ định kỳ: %s đến %s",
        since,
        until,
    )

    total_rows = 0

    while task_index < total_tasks:

        elapsed = time.monotonic() - start_time

        if elapsed >= PROCESS_BUDGET_SECONDS:
            break

        account_id, level = tasks[task_index]

        account = account_map.get(account_id)

        if account is None:
            raise RuntimeError(
                f"Tài khoản {account_id} không còn "
                "trong danh sách truy cập được."
            )

        rows_count, updated_payload = process_task(
            client=client,
            account=account,
            level=level,
            since=since,
            until=until,
            accounts=accounts,
        )

        total_rows += rows_count

        task_index += 1

        state["incremental_task_index"] = task_index
        state["incremental_tasks_total"] = total_tasks
        state["last_successful_sync"] = updated_payload["updated_at"]

        save_sync_state(state)

    if task_index >= total_tasks:

        state["last_successful_sync"] = (
            load_ads()["updated_at"]
        )

        state["last_sync_since"] = since
        state["last_sync_until"] = until

        state.pop("incremental_account_ids", None)
        state.pop("incremental_task_index", None)
        state.pop("incremental_tasks_total", None)
        state.pop("incremental_since", None)
        state.pop("incremental_until", None)

        save_sync_state(state)

        logger.info(
            "ĐÃ HOÀN THÀNH ĐỒNG BỘ ĐỊNH KỲ."
        )

    else:

        logger.info(
            "Đồng bộ định kỳ tạm dừng: %s/%s tác vụ. "
            "Lần chạy sau sẽ tiếp tục.",
            task_index,
            total_tasks,
        )

    return total_rows


# =====================================
# MAIN
# =====================================

def run():

    start_time = time.monotonic()

    logger.info("====================================")
    logger.info("META ADS DASHBOARD - START SYNC")
    logger.info("====================================")

    validate_config()

    client = MetaAPIClient()

    profile = client.validate_token()

    logger.info(
        "Token hợp lệ. Người dùng/API identity: %s",
        profile.get("name", profile.get("id", "Unknown")),
    )

    accounts = client.get_ad_accounts()

    if not accounts:
        raise RuntimeError(
            "Token không tìm thấy tài khoản quảng cáo nào. "
            "Kiểm tra quyền ads_read và quyền truy cập."
        )

    logger.info(
        "Tìm thấy %s tài khoản quảng cáo.",
        len(accounts),
    )

    state = load_sync_state()

    initial_completed = state.get(
        "initial_sync_completed",
        False,
    )

    if not initial_completed:

        total_rows = run_initial_sync(
            client=client,
            accounts=accounts,
            state=state,
            start_time=start_time,
        )

    else:

        total_rows = run_incremental_sync(
            client=client,
            accounts=accounts,
            state=state,
            start_time=start_time,
        )

    final_payload = load_ads()

    logger.info("====================================")
    logger.info("SYNC RUN FINISHED")
    logger.info("Accounts available: %s", len(accounts))
    logger.info("Rows processed this run: %s", total_rows)
    logger.info(
        "Total stored rows: %s",
        len(final_payload.get("data", [])),
    )
    logger.info(
        "Initial sync completed: %s",
        state.get("initial_sync_completed", False),
    )
    logger.info("====================================")


if __name__ == "__main__":

    try:
        run()

    except Exception:
        logger.exception("META ADS SYNC FAILED")
        sys.exit(1)
