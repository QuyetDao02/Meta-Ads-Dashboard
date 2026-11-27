
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ==============================
# META API
# ==============================

API_VERSION = os.getenv("META_API_VERSION", "v27.0")
GRAPH_API_BASE = f"https://graph.facebook.com/{API_VERSION}"

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "").strip()

# ==============================
# SYNC CONFIGURATION
# ==============================

TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

# Lịch sử cần lưu
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "365"))

# Lần đồng bộ định kỳ lấy lại 7 ngày gần nhất
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))

# Lần chạy đầu lấy tối đa 365 ngày
INITIAL_SYNC_DAYS = int(
    os.getenv("INITIAL_SYNC_DAYS", "365")
)

# Kích thước mỗi trang API
API_PAGE_LIMIT = int(os.getenv("API_PAGE_LIMIT", "500"))

# Giới hạn retry khi Meta API lỗi tạm thời
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))

# Thời gian chờ request
REQUEST_TIMEOUT = int(
    os.getenv("REQUEST_TIMEOUT", "90")
)

# ==============================
# STORAGE
# ==============================

ROOT_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

DATA_DIR = os.path.join(
    ROOT_DIR, "docs", "data"
)

ADS_DATA_FILE = os.path.join(
    DATA_DIR, "ads.json"
)

SYNC_STATE_FILE = os.path.join(
    DATA_DIR, "sync_state.json"
)

# ==============================
# DATA LEVELS
# ==============================

AD_LEVELS = ("campaign", "adset", "ad")

# ==============================
# DATE HELPERS
# ==============================

def today_local():
    return datetime.now(TIMEZONE).date()


def date_string(value):
    return value.strftime("%Y-%m-%d")


def initial_date_range():
    end = today_local()
    start = end - timedelta(days=INITIAL_SYNC_DAYS - 1)

    return date_string(start), date_string(end)


def incremental_date_range():
    end = today_local()
    start = end - timedelta(days=LOOKBACK_DAYS - 1)

    return date_string(start), date_string(end)


def retention_cutoff():
    return date_string(
        today_local() - timedelta(days=RETENTION_DAYS - 1)
    )


def validate_config():
    if not META_ACCESS_TOKEN:
        raise RuntimeError(
            "Missing META_ACCESS_TOKEN. "
            "Please configure it in GitHub Actions Secrets."
        )

    if RETENTION_DAYS < 1:
        raise ValueError("RETENTION_DAYS must be >= 1")

    if LOOKBACK_DAYS < 1:
        raise ValueError("LOOKBACK_DAYS must be >= 1")

    if INITIAL_SYNC_DAYS < 1:
        raise ValueError("INITIAL_SYNC_DAYS must be >= 1")

    if API_PAGE_LIMIT < 1:
        raise ValueError("API_PAGE_LIMIT must be >= 1")
