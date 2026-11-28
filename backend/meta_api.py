
import json
import logging
import random
import time
from typing import Iterator, List, Optional

import requests

from config import (
    API_PAGE_LIMIT,
    GRAPH_API_BASE,
    MAX_RETRIES,
    META_ACCESS_TOKEN,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)


class MetaAPIError(Exception):
    """Lỗi trả về từ Meta Graph API."""


class MetaAPIClient:
    def __init__(self):
        if not META_ACCESS_TOKEN:
            raise MetaAPIError("META_ACCESS_TOKEN chưa được cấu hình.")

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Meta-Ads-Dashboard/1.0",
        })

    
def _request(
    self,
    url: str,
    params: Optional[dict] = None,
) -> dict:

    request_params = dict(params or {})

    if "access_token" not in request_params:
        request_params["access_token"] = META_ACCESS_TOKEN

    permanent_codes = {
        10, 100, 190, 200
    }

    retryable_codes = {
        1, 2, 4, 17, 32, 613
    }

    # Meta có thể trả HTTP 400 nhưng nội dung thực tế
    # lại là lỗi dịch vụ tạm thời.
    retryable_subcodes = {
        1504044,
    }

    # MAX_RETRIES là tổng số lần thử, bao gồm lần đầu tiên.
    total_attempts = max(1, MAX_RETRIES)

    for attempt in range(total_attempts):

        try:
            response = self.session.get(
                url,
                params=request_params,
                timeout=REQUEST_TIMEOUT,
            )

            try:
                payload = response.json()
            except ValueError:
                payload = {}

            if not isinstance(payload, dict):
                payload = {}

            if response.ok and "error" not in payload:
                return payload

            error = payload.get("error") or {}

            if not isinstance(error, dict):
                error = {}

            code = error.get("code")
            subcode = error.get("error_subcode")

            # Chuẩn hóa mã lỗi về integer.
            try:
                code = int(code) if code is not None else None
            except (TypeError, ValueError):
                code = None

            try:
                subcode = int(subcode) if subcode is not None else None
            except (TypeError, ValueError):
                subcode = None

            message = error.get(
                "message",
                response.text[:500],
            )

            status = response.status_code

            # Các lỗi HTTP có khả năng tạm thời.
            retryable_http = (
                status >= 500
                or status in (408, 425, 429)
            )

            # Các lỗi Graph API có khả năng tạm thời.
            retryable_graph = (
                code in retryable_codes
                or subcode in retryable_subcodes
            )

            # Lỗi token, quyền truy cập hoặc tham số.
            permanent_graph = (
                code in permanent_codes
            )

            should_retry = (
                retryable_http or retryable_graph
            ) and not permanent_graph

            if not should_retry:
                raise MetaAPIError(
                    f"Meta API lỗi HTTP {status}, "
                    f"code={code}, subcode={subcode}: {message}"
                )

            if attempt == total_attempts - 1:
                raise MetaAPIError(
                    f"Meta API vẫn lỗi sau {total_attempts} lần thử. "
                    f"HTTP {status}, code={code}, "
                    f"subcode={subcode}: {message}"
                )

            # Ưu tiên Retry-After do máy chủ trả về.
            retry_after = response.headers.get("Retry-After")

            try:
                if retry_after:
                    wait_seconds = float(retry_after)
                else:
                    wait_seconds = min(5 * (2 ** attempt), 60)
            except (TypeError, ValueError):
                wait_seconds = min(5 * (2 ** attempt), 60)

            # Giới hạn thời gian chờ và thêm jitter.
            wait_seconds = min(max(wait_seconds, 1), 120)
            wait_seconds += random.uniform(0, 3)

            logger.warning(
                "Meta API tạm lỗi: HTTP %s, code=%s, "
                "subcode=%s. Lần thử %s/%s. "
                "Thử lại sau %.1f giây.",
                status,
                code,
                subcode,
                attempt + 1,
                total_attempts,
                wait_seconds,
            )

            time.sleep(wait_seconds)

        except requests.RequestException as exc:

            if attempt == total_attempts - 1:
                raise MetaAPIError(
                    f"Không thể kết nối Meta API sau "
                    f"{total_attempts} lần thử: {exc}"
                ) from exc

            wait_seconds = (
                min(5 * (2 ** attempt), 60)
                + random.uniform(0, 3)
            )

            logger.warning(
                "Lỗi kết nối Meta API: %s. "
                "Lần thử %s/%s. Thử lại sau %.1f giây.",
                exc,
                attempt + 1,
                total_attempts,
                wait_seconds,
            )

            time.sleep(wait_seconds)

    raise MetaAPIError(
        "Meta API thất bại sau nhiều lần thử."
    )


    def _paginate(
        self,
        url: str,
        params: Optional[dict] = None,
    ) -> Iterator[dict]:

        next_url = url
        next_params = params

        visited_urls = set()

        while next_url:

            if next_url in visited_urls:
                raise MetaAPIError(
                    "Meta API trả về URL phân trang bị lặp."
                )

            visited_urls.add(next_url)

            payload = self._request(
                next_url,
                next_params,
            )

            for item in payload.get("data", []):
                yield item

            paging = payload.get("paging") or {}

            next_url = paging.get("next")
            next_params = None

    def validate_token(self) -> dict:
        return self._request(
            f"{GRAPH_API_BASE}/me",
            {"fields": "id,name"},
        )

    def get_ad_accounts(self) -> List[dict]:

        fields = (
            "id,account_id,name,currency,"
            "account_status,timezone_name"
        )

        url = f"{GRAPH_API_BASE}/me/adaccounts"

        accounts = list(
            self._paginate(
                url,
                {
                    "fields": fields,
                    "limit": API_PAGE_LIMIT,
                },
            )
        )

        unique = {}

        for account in accounts:

            account_id = account.get("account_id")

            if not account_id:
                raw_id = account.get("id", "")
                account_id = raw_id.replace("act_", "")

            if account_id:
                account_id = str(account_id)

                account["account_id"] = account_id
                account["id"] = f"act_{account_id}"

                unique[account_id] = account

        result = list(unique.values())

        logger.info(
            "Tìm thấy %s tài khoản quảng cáo.",
            len(result),
        )

        return result

    @staticmethod
    def _insight_fields(level: str) -> str:

        common = [
            "account_id",
            "account_name",
            "date_start",
            "date_stop",
            "spend",
            "impressions",
            "reach",
            "clicks",
            "ctr",
            "cpc",
            "actions",
        ]

        if level == "campaign":

            common += [
                "campaign_id",
                "campaign_name",
            ]

        elif level == "adset":

            common += [
                "campaign_id",
                "campaign_name",
                "adset_id",
                "adset_name",
            ]

        elif level == "ad":

            common += [
                "campaign_id",
                "campaign_name",
                "adset_id",
                "adset_name",
                "ad_id",
                "ad_name",
            ]

        else:
            raise ValueError(
                f"Level không hợp lệ: {level}"
            )

        return ",".join(common)

    def get_insights(
        self,
        account_id: str,
        level: str,
        since: str,
        until: str,
    ) -> List[dict]:

        normalized_id = str(account_id).replace(
            "act_", ""
        )

        url = (
            f"{GRAPH_API_BASE}/act_"
            f"{normalized_id}/insights"
        )

        params = {
            "level": level,
            "time_increment": 1,
            "time_range": json.dumps({
                "since": since,
                "until": until,
            }),
            "fields": self._insight_fields(level),
            "limit": API_PAGE_LIMIT,
        }

        rows = list(
            self._paginate(url, params)
        )

        logger.info(
            "Account %s | level=%s | %s đến %s | %s dòng",
            normalized_id,
            level,
            since,
            until,
            len(rows),
        )

        return rows
