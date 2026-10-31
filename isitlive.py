
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

from curl_cffi import requests

URLS = [
    url.strip()
    for url in os.environ["PING_URLS"].splitlines()
    if url.strip()
]

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path(os.getenv("STATE_FILE", "state/state.json"))

TIMEOUT = 20
WORKERS = 10


def endpoint_id(url):
    return hashlib.sha256(url.encode()).hexdigest()


def check_endpoint(index, url):
    start = perf_counter()

    try:
        if not url.startswith("https://"):
            raise ValueError("HTTPS required")

        response = requests.get(
            url,
            impersonate="chrome",
            timeout=TIMEOUT,
            verify=True,
            allow_redirects=True,
        )

        response.raise_for_status()

        return {
            "id": endpoint_id(url),
            "index": index,
            "success": True,
            "status": response.status_code,
            "latency": round((perf_counter() - start) * 1000),
        }

    except Exception as error:
        return {
            "id": endpoint_id(url),
            "index": index,
            "success": False,
            "error": type(error).__name__,
            "latency": round((perf_counter() - start) * 1000),
        }


def notify(message):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        impersonate="chrome",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=TIMEOUT,
        verify=True,
    )

    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram notification failed")


def main():
    if not URLS:
        raise RuntimeError("No endpoints configured")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    previous = (
        json.loads(STATE_FILE.read_text())
        if STATE_FILE.exists()
        else {}
    )

    results = []

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [
            executor.submit(check_endpoint, index, url)
            for index, url in enumerate(URLS, 1)
        ]

        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: item["index"])

    changes = [
        result for result in results
        if previous.get(result["id"]) != result["success"]
    ]

    if changes:
        lines = ["isitlive | State changes", ""]

        for result in changes:
            if result["success"]:
                lines.append(
                    f"🟢 #{result['index']} RECOVERED "
                    f"· HTTP {result['status']} "
                    f"· {result['latency']}ms"
                )
            else:
                lines.append(
                    f"🔴 #{result['index']} DOWN "
                    f"· {result['error']} "
                    f"· {result['latency']}ms"
                )

        lines.extend([
            "",
            f"Changed: {len(changes)}/{len(results)}",
        ])

        notify("\n".join(lines))
        print("\n".join(lines))
    else:
        print("No state changes. Notification skipped.")

    current = {
        result["id"]: result["success"]
        for result in results
    }

    STATE_FILE.write_text(
        json.dumps(current, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
