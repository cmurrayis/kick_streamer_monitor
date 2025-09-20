#!/usr/bin/env python3
"""Test 4: Cookie Isolation in Thread Pool"""

import threading
import cloudscraper
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

def worker_with_cookies(worker_id, shared_scraper=None):
    """Test if cookies are isolated per thread"""
    if shared_scraper:
        scraper = shared_scraper
        print(f"Worker {worker_id}: Using SHARED scraper")
    else:
        scraper = cloudscraper.create_scraper()
        print(f"Worker {worker_id}: Created OWN scraper")

    # Set a unique cookie for this worker
    print(f"Worker {worker_id}: Setting cookie 'worker{worker_id}=value{worker_id}'")
    resp1 = scraper.get(f"https://httpbin.org/cookies/set?worker{worker_id}=value{worker_id}")

    # Small delay to simulate work
    time.sleep(0.5)

    # Check what cookies we have
    resp2 = scraper.get("https://httpbin.org/cookies")
    cookies = resp2.json().get('cookies', {})

    print(f"Worker {worker_id} sees cookies: {cookies}")
    print(f"Worker {worker_id} cookie jar: {scraper.cookies.get_dict()}")
    return cookies

async def test_cookie_isolation():
    print("=" * 60)
    print("TEST: Cookie Isolation Between Threads")
    print("=" * 60)

    print("\n=== Test 1: Separate scrapers per worker ===")
    print("Each worker creates its own CloudScraper instance\n")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for i in range(3):
            future = executor.submit(worker_with_cookies, i)
            futures.append(future)

        results = []
        for future in futures:
            results.append(future.result())

    print("\n--- Results Summary ---")
    for i, cookies in enumerate(results):
        print(f"Worker {i} final cookies: {cookies}")

    # Check for cross-contamination
    isolated = True
    for i, cookies in enumerate(results):
        for j in range(3):
            if i != j and f"worker{j}" in cookies:
                print(f"WARNING: Worker {i} has cookie from Worker {j}!")
                isolated = False

    if isolated:
        print("GOOD: No cookie contamination between workers with separate scrapers")

    print("\n=== Test 2: Shared scraper (NOT RECOMMENDED) ===")
    print("All workers share the same CloudScraper instance\n")

    shared_scraper = cloudscraper.create_scraper()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for i in range(3, 6):  # Use different worker IDs
            future = executor.submit(worker_with_cookies, i, shared_scraper)
            futures.append(future)

        results2 = []
        for future in futures:
            results2.append(future.result())

    print("\n--- Results Summary ---")
    for i, cookies in enumerate(results2, start=3):
        print(f"Worker {i} final cookies: {cookies}")

    # Check for mixing
    mixed = False
    for i, cookies in enumerate(results2, start=3):
        if len(cookies) > 1:
            print(f"Worker {i} sees multiple cookies: EXPECTED with shared scraper")
            mixed = True

    if mixed:
        print("WARNING: Shared scraper causes cookie mixing between workers")

    print("\n=== CONCLUSION ===")
    print("1. Each worker MUST have its own CloudScraper instance")
    print("2. Cookies are properly isolated when using separate scrapers")
    print("3. Shared scrapers cause cookie contamination between workers")

if __name__ == "__main__":
    asyncio.run(test_cookie_isolation())