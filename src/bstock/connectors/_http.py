"""HTTP access shared by the connectors: session, timeout, retries and pagination (SPEC §8.2).

``requests`` (extra ``[connectors]``) is imported when the first client is created.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from urllib.parse import urljoin

from bstock._deps import require
from bstock.exceptions import ConnectorError

__all__ = ["HttpClient"]

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
ADEME_BUDGET_MARKER = "le budget allou"
"""Part of the message of the ADEME data-fair API when the processing-time budget is exceeded
("… dépasse le budget alloué …"); ASCII only, so that it also matches JSON-escaped accents."""


def _user_agent() -> str:
    from bstock import __version__  # circular import

    return f"bstock/{__version__} (Python building stock library)"


class HttpClient:
    """Minimal JSON client over a ``requests`` session.

    Parameters
    ----------
    base_url
        URL that relative paths are joined to; keep its trailing ``/``.
    session
        A ``requests.Session`` (or compatible object, e.g. from ``requests-cache``). A new session
        is created when omitted.
    headers
        Headers added to every request (e.g. an API key).
    timeout
        Timeout of each request, in s.
    max_retries
        Number of new attempts after a retryable failure (429, 5xx, ADEME budget message, network
        error).
    backoff
        Wait before the first new attempt, in s; doubled at each attempt. A ``Retry-After`` header
        in seconds takes precedence.
    sleep
        Function used to wait, replaceable in tests.

    Raises
    ------
    MissingDependencyError
        If ``requests`` is not installed.
    """

    def __init__(
        self,
        base_url: str,
        *,
        session: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        requests = require("requests", "connectors")
        self.base_url = base_url
        self.session = session if session is not None else requests.Session()
        self.headers = {"User-Agent": _user_agent(), **(headers or {})}
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self._sleep = sleep
        self._network_errors: tuple[type[BaseException], ...] = (
            requests.ConnectionError,
            requests.Timeout,
        )

    def url(self, path: str) -> str:
        """Return the absolute URL of ``path`` (an absolute URL is returned unchanged)."""
        return urljoin(self.base_url, path)

    def get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        not_found_ok: bool = False,
    ) -> Any | None:
        """Send a GET request and return the response, retrying on transient failures.

        Parameters
        ----------
        path
            Path relative to ``base_url``, or absolute URL.
        params
            Query parameters.
        headers
            Headers added to the client ones for this request.
        not_found_ok
            Return ``None`` on a 404 instead of raising.

        Returns
        -------
        requests.Response or None
            The successful response, or ``None`` on a 404 with ``not_found_ok``.

        Raises
        ------
        ConnectorError
            On an HTTP error other than a retryable one, or when the retries are exhausted.
        """
        url = self.url(path)
        all_headers = {**self.headers, **(headers or {})}
        attempt = 0
        while True:
            try:
                response = self.session.get(
                    url, params=params, headers=all_headers, timeout=self.timeout
                )
            except self._network_errors as error:
                reason = f"network error: {error}"
                response = None
            else:
                if response.status_code == 404 and not_found_ok:
                    return None
                if response.status_code < 400:
                    return response
                reason = f"HTTP {response.status_code}: {_body_excerpt(response)}"
                if not _is_retryable(response):
                    raise ConnectorError(f"GET {url} failed with {reason}")
            if attempt >= self.max_retries:
                raise ConnectorError(f"GET {url} failed after {attempt + 1} attempts ({reason})")
            delay = _retry_after(response) or self.backoff * 2**attempt
            logger.info("GET %s: %s; new attempt in %.1f s", url, reason, delay)
            self._sleep(delay)
            attempt += 1

    def get_json(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        not_found_ok: bool = False,
    ) -> Any | None:
        """Send a GET request and return the decoded JSON body (``None`` on an accepted 404).

        Raises
        ------
        ConnectorError
            As :meth:`get`, or if the body is not JSON.
        """
        response = self.get(path, params, headers=headers, not_found_ok=not_found_ok)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise ConnectorError(f"GET {response.url} did not return JSON") from error

    def paginate(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        results_key: str = "results",
        next_key: str = "next",
        max_pages: int | None = None,
    ) -> Iterator[Any]:
        """Iterate over the items of a paginated listing, following the ``next`` URL.

        Parameters
        ----------
        path, params
            First page request; the following pages use the ``next`` URL as is.
        results_key, next_key
            Keys of the items and of the next page URL in each page.
        max_pages
            Stop after this number of pages (no limit by default).

        Raises
        ------
        ConnectorError
            As :meth:`get_json`, or if a page lacks ``results_key``.
        """
        url: str | None = path
        page_params = params
        pages = 0
        while url and (max_pages is None or pages < max_pages):
            page = self.get_json(url, page_params)
            if not isinstance(page, dict) or not isinstance(page.get(results_key), list):
                raise ConnectorError(f"GET {self.url(url)}: no {results_key!r} list in the page")
            yield from page[results_key]
            url, page_params = page.get(next_key), None
            pages += 1


def _is_retryable(response: Any) -> bool:
    return response.status_code in RETRY_STATUSES or ADEME_BUDGET_MARKER in _body_excerpt(
        response, 2000
    )


def _retry_after(response: Any | None) -> float | None:
    """Return the ``Retry-After`` delay in seconds, if the response gives one as a number."""
    value = None if response is None else response.headers.get("Retry-After")
    try:
        return max(float(value), 0.0) if value is not None else None
    except ValueError:  # an HTTP date: fall back on the backoff
        return None


def _body_excerpt(response: Any, size: int = 300) -> str:
    return str(response.text)[:size]
