import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

from curl_cffi import requests

ENDPOINTS = []

for index, line in enumerate(os.environ["PING_URLS"].splitlines(), 1):
    line = line.strip()
    if not line:
        continue

    if "|" in line:
        label, url = line.split("|", 1)
        label, url = label.strip(), url.strip()
    else:
        label, url = f"#{index}", line

    if not url.startswith("https://"):
        raise ValueError(f"{label}: HTTPS required")

    ENDPOINTS.append((label, url))

if not ENDPOINTS:
    raise RuntimeError("No endpoints configured")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path(os.getenv("STATE_FILE", "state/state.json"))

TIMEOUT = 20
WORKERS = 10


def endpoint_id(label):
    return hashlib.sha256(label.encode()).hexdigest()


def check_endpoint(label, url):
    start = perf_counter()

    try:
        response = requests.get(
            url,
            impersonate="chrome",
            timeout=TIMEOUT,
            verify=True,
            allow_redirects=True,
        )
        if response.status_code >= 500:
            response.raise_for_status()

        return {
            "id": endpoint_id(label),
            "label": label,
            "success": True,
            "status": response.status_code,
            "latency": round((perf_counter() - start) * 1000),
        }
    except Exception as error:
        return {
            "id": endpoint_id(label),
            "label": label,
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
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    results = []

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [
            executor.submit(check_endpoint, label, url)
            for label, url in ENDPOINTS
        ]

        for future in as_completed(futures):
            results.append(future.result())

    order = {endpoint_id(label): i for i, (label, _) in enumerate(ENDPOINTS)}
    results.sort(key=lambda result: order[result["id"]])

    changes = [
        result for result in results
        if previous.get(result["id"]) != result["success"]
    ]

    if changes:
        lines = ["isitlive | State changes", ""]

        for result in changes:
            if result["success"]:
                lines.append(
                    f"🟢 {result['label']} is UP"
                    f" · HTTP {result['status']}"
                    f" · {result['latency']}ms"
                )
            else:
                lines.append(
                    f"🔴 {result['label']} is DOWN"
                    f" · {result['error']}"
                    f" · {result['latency']}ms"
                )

        lines.extend(["", f"Changed: {len(changes)}/{len(results)}"])
        message = "\n".join(lines)

        notify(message)
        print(message)
    else:
        print("No state changes. Notification skipped.")

    current = {
        result["id"]: result["success"]
        for result in results
    }
    STATE_FILE.write_text(json.dumps(current, indent=2) + "\n")


if __name__ == "__main__":
    main()
