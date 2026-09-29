"""HTTP helpers for vendor adapters: OS trust store, a polite user agent, and Retry-After aware backoff."""

import requests

USER_AGENT = "jse-recon/0.1 (personal research project; daily end-of-day prices)"
BACKOFF_BASE_SECONDS = 2.0


def use_os_trust_store() -> None:
    # Behind an SSL-inspecting proxy the OS store holds the corporate root certifi lacks; verification stays on
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - best effort; plain public-CA verification still applies without it
        pass


def session() -> requests.Session:
    use_os_trust_store()
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def retryable(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def backoff_seconds(response, attempt: int) -> float:
    # Honour the server's Retry-After when it sends one, with a second's margin
    header = getattr(response, "headers", {}).get("Retry-After")
    if header:
        try:
            return float(header) + 1.0
        except ValueError:
            pass
    return BACKOFF_BASE_SECONDS * (2 ** attempt)
