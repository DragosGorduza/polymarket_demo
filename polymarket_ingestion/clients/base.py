from __future__ import annotations

from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class ApiUnauthorizedError(RuntimeError):
    """Raised when an API endpoint returns HTTP 401."""


class BaseApiClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 20,
        user_agent: str = "polymarket-ingestion-mvp/0.1",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        if extra_headers:
            self.session.headers.update(extra_headers)

        retry = Retry(
            total=4,
            backoff_factor=0.4,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, params=params, timeout=self.timeout_seconds)
        if resp.status_code == 401:
            raise ApiUnauthorizedError(f"401 Unauthorized for {url}")
        resp.raise_for_status()
        return resp.json()
