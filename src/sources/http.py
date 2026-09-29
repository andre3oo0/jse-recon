"""HTTP session for vendor adapters, verified against the operating system's certificate store."""

import requests
import truststore

truststore.inject_into_ssl()  # corporate networks re-sign TLS with a CA that only the OS store trusts

USER_AGENT = "jse-recon/0.1 (personal research project; daily end-of-day prices)"


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s
