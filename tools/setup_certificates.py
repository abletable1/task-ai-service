"""Create a project-local CA bundle for requests without modifying system trust."""
import ssl
import time
from pathlib import Path

import certifi
import requests

from config.settings import ROOT

ROOT_CERT_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"


def main():
    for attempt in range(1, 4):
        try:
            with requests.get(ROOT_CERT_URL, timeout=15) as response:
                response.raise_for_status()
                certificate = response.content.decode("ascii")
            break
        except requests.exceptions.SSLError:
            raise
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 3:
                raise
        except requests.HTTPError as exc:
            if exc.response.status_code not in (408, 429) and exc.response.status_code < 500:
                raise
            if attempt == 3:
                raise
        time.sleep(2 ** (attempt - 1))
    if "-----BEGIN CERTIFICATE-----" not in certificate:
        raise ValueError("Источник не вернул PEM-сертификат")
    bundle = Path(certifi.where()).read_text(encoding="ascii") + "\n" + certificate + "\n"
    ssl.create_default_context(cadata=bundle)
    directory = ROOT / "certs"
    directory.mkdir(exist_ok=True)
    path = directory / "ca-bundle.pem"
    path.write_text(bundle, encoding="ascii")
    print("CA bundle created: certs/ca-bundle.pem")
    print("Set GIGACHAT_CA_BUNDLE_FILE=certs/ca-bundle.pem in .env and restart.")


if __name__ == "__main__":
    main()
