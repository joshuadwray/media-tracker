"""Polite HTTP helper shared by all sources."""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 25
_last_request_at = 0.0
MIN_INTERVAL = 1.0  # seconds between outbound requests, be a good citizen


def _throttle() -> None:
    global _last_request_at
    wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def get(sess: requests.Session, url: str, *, params: Optional[dict] = None,
        headers: Optional[dict] = None, retries: int = 2) -> requests.Response:
    return _request(sess, "GET", url, params=params, headers=headers, retries=retries)


def post_json(sess: requests.Session, url: str, payload: Any, *,
              headers: Optional[dict] = None, retries: int = 2) -> requests.Response:
    return _request(sess, "POST", url, json=payload, headers=headers, retries=retries)


def _request(sess: requests.Session, method: str, url: str, *, retries: int = 2,
             **kwargs: Any) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            resp = sess.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
            if resp.status_code >= 500 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]
