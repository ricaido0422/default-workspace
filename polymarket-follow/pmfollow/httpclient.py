"""HTTP 客户端模块。

基于 urllib 标准库实现，支持代理、限流、重试。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class HttpClientError(Exception):
    """HTTP 客户端通用错误。"""

    pass


class NotFoundError(Exception):
    """404 资源不存在错误。"""

    pass


class HttpClient:
    """带代理、限流、重试的 HTTP 客户端。

    使用 urllib 标准库，不依赖第三方包。
    """

    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: int = 30,
        min_interval: float = 0.1,
        max_retries: int = 3,
        user_agent: str = "pmfollow/0.1",
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.user_agent = user_agent
        self._last_request_ts: float = 0.0

    def _throttle(self) -> None:
        """限流：确保两次请求间隔不小于 min_interval。"""
        now = time.monotonic()
        elapsed = now - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        """构建 urllib opener，配置代理和 header。"""
        handlers: list = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()

    def _request(self, url: str, data: Optional[bytes] = None) -> Any:
        """执行单次 HTTP 请求并返回解析后的 JSON。

        内部方法，不处理重试。
        """
        opener = self._build_opener()
        req = urllib.request.Request(url, data=data)
        req.add_header("User-Agent", self.user_agent)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        resp = opener.open(req, timeout=self.timeout)
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def _request_with_retry(self, url: str, data: Optional[bytes] = None) -> Any:
        """带退避重试的请求。

        429/5xx 指数退避重试，404 抛 NotFoundError，其它 HTTPError 重试后抛 HttpClientError。
        """
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                return self._request(url, data)
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 404:
                    raise NotFoundError(f"404 资源不存在: {url}") from e
                if e.code == 429 or e.code >= 500:
                    if attempt < self.max_retries:
                        backoff = 0.5 * (2 ** attempt)
                        time.sleep(backoff)
                        continue
                    raise HttpClientError(f"HTTP {e.code} 重试 {self.max_retries} 次仍失败: {url}") from e
                # 其它 HTTP 错误码
                if attempt < self.max_retries:
                    backoff = 0.5 * (2 ** attempt)
                    time.sleep(backoff)
                    continue
                raise HttpClientError(f"HTTP {e.code}: {url}") from e
            except urllib.error.URLError as e:
                last_error = e
                if attempt < self.max_retries:
                    backoff = 0.5 * (2 ** attempt)
                    time.sleep(backoff)
                    continue
                raise HttpClientError(f"URL 错误: {url} - {e}") from e
            except json.JSONDecodeError as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise HttpClientError(f"JSON 解析失败: {url} - {e}") from e

        raise HttpClientError(f"请求失败，已耗尽重试: {url}") from last_error

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET 请求，返回 JSON。

        params 拼成 querystring，值为 None/空 的参数跳过。
        """
        if params:
            filtered = {k: v for k, v in params.items() if v is not None and v != ""}
            if filtered:
                qs = urllib.parse.urlencode(filtered, doseq=True)
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}{qs}"
        return self._request_with_retry(url, data=None)

    def post_json(self, url: str, body: Any) -> Any:
        """POST 请求，body 序列化为 JSON，返回 JSON 响应。"""
        data = json.dumps(body).encode("utf-8")
        return self._request_with_retry(url, data=data)
