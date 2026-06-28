import requests
from bs4 import BeautifulSoup
import json
import hashlib
import os
from datetime import datetime

URL = "https://www.autotrader.co.uk/cars/retailer/stock?postcode=lu11jn&retailerId=10048702&sort=most-recent&page=2"
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
UPSTASH_URL = os.environ["UPSTASH_URL"]
UPSTASH_TOKEN = os.environ["UPSTASH_TOKEN"]
REDIS_KEY = "autotrader_snapshot"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def redis_get(key):
    r = requests.get(
        f"{UPSTASH_URL}/get/{key}",
        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        timeout=10,
    )
    result = r.json().get("result")
    return json.loads(result) if result else None


def redis_set(key, value):
    requests.post(
        f"{UPSTASH_URL}/set/{key}",
        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        json=json.dumps(value),
        timeout=10,
    )


def send_telegram(message):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )


def fetch_listings():
    r = requests.get(URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    cards = (
        soup.select("li[data-testid='trader-seller-listing']")
        or soup.select("li.search-page__result")
        or soup.select("[data-listing-id]")
    )

    if not cards:
        main = soup.select_one("main") or soup.body
        content = main.get_text(separator=" ", strip=True) if main else r.text
        return {"__hash__": hashlib.md5(content.encode()).hexdigest()}

    listings = {}
    for card in cards:
        lid = (
            card.get("data-listing-id")
            or card.get("id")
            or hashlib.md5(card.get_text(strip=True).encode()).hexdigest()
        )
        title_el = card.select_one("h3, h2, [data-testid='listing-title']")
        price_el = card.select_one("[data-testid='search-listing-price'], strong")
        listings[str(lid)] = {
            "title": title_el.get_text(strip=True) if title_el else "Unknown",
            "price": price_el.get_text(strip=True) if price_el else "Unknown",
        }
    return listings


def compare_and_notify(old, new):
    timestamp = datetime.now().strftime("%d %b %Y %H:%M")
    changes = []

    if "__hash__" in new:
        if old.get("__hash__") != new["__hash__"]:
            changes.append("Page content has changed — new cars or updates detected.")
    else:
        old_ids, new_ids = set(old), set(new)
        for lid in new_ids - old_ids:
            changes.append(f"NEW: {new[lid]['title']} — {new[lid]['price']}")
        for lid in old_ids - new_ids:
            changes.append(f"REMOVED: {old[lid]['title']} — {old[lid]['price']}")
        for lid in old_ids & new_ids:
            if old[lid]["price"] != new[lid]["price"]:
                changes.append(
                    f"PRICE CHANGE: {new[lid]['title']}\n"
                    f"  {old[lid]['price']} → {new[lid]['price']}"
                )

    if changes:
        msg = (
            f"<b>🚗 AutoTrader Update</b> ({timestamp})\n"
            f'<a href="{URL}">View Page</a>\n\n'
            + "\n".join(f"• {c}" for c in changes)
        )
        send_telegram(msg)
        print(f"Changes detected: {len(changes)}")
    else:
        print("No changes.")


def main():
    print(f"Checking AutoTrader at {datetime.now().strftime('%d %b %Y %H:%M')} ...")
    try:
        current = fetch_listings()
    except Exception as e:
        send_telegram(f"⚠️ AutoTrader monitor error: {e}")
        raise

    previous = redis_get(REDIS_KEY)
    if previous is None:
        redis_set(REDIS_KEY, current)
        count = len(current) if "__hash__" not in current else "?"
        send_telegram(
            f"✅ <b>AutoTrader Monitor Started</b>\n"
            f"Tracking {count} listings on page 2.\n"
            f'<a href="{URL}">View Page</a>'
        )
        print("First run — snapshot saved.")
    else:
        compare_and_notify(previous, current)
        redis_set(REDIS_KEY, current)


if __name__ == "__main__":
    main()
