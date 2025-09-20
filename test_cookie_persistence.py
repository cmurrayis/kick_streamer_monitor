#!/usr/bin/env python3
"""Test 1: CloudScraper Cookie Persistence with asyncio.to_thread"""

import asyncio
import cloudscraper
import json

async def test_cookie_persistence():
    print("=" * 60)
    print("TEST: Cookie Persistence with CloudScraper")
    print("=" * 60)

    # Test 1: Direct synchronous calls
    print("\n=== Test 1: Synchronous calls ===")
    scraper = cloudscraper.create_scraper()

    print("Making request to set cookie 'test1=value1'...")
    resp1 = scraper.get("https://httpbin.org/cookies/set?test1=value1")
    print(f"Response status: {resp1.status_code}")
    print(f"Cookies in jar after request 1: {scraper.cookies.get_dict()}")

    print("\nMaking request to check cookies...")
    resp2 = scraper.get("https://httpbin.org/cookies")
    print(f"Response status: {resp2.status_code}")
    print(f"Server sees cookies: {resp2.json().get('cookies', {})}")
    print(f"Cookie jar state: {scraper.cookies.get_dict()}")

    # Test 2: Using asyncio.to_thread
    print("\n=== Test 2: asyncio.to_thread calls ===")
    scraper2 = cloudscraper.create_scraper()

    print("Making async request to set cookie 'test2=value2'...")
    resp3 = await asyncio.to_thread(scraper2.get, "https://httpbin.org/cookies/set?test2=value2")
    print(f"Response status: {resp3.status_code}")
    print(f"Cookies in jar after async request 1: {scraper2.cookies.get_dict()}")

    print("\nMaking async request to check cookies...")
    resp4 = await asyncio.to_thread(scraper2.get, "https://httpbin.org/cookies")
    print(f"Response status: {resp4.status_code}")
    print(f"Server sees cookies: {resp4.json().get('cookies', {})}")
    print(f"Cookie jar state: {scraper2.cookies.get_dict()}")

    # Test 3: Multiple cookies with asyncio.to_thread
    print("\n=== Test 3: Multiple cookies with asyncio.to_thread ===")
    scraper3 = cloudscraper.create_scraper()

    print("Setting multiple cookies via async calls...")
    await asyncio.to_thread(scraper3.get, "https://httpbin.org/cookies/set?cookie1=value1")
    print(f"After cookie1: {scraper3.cookies.get_dict()}")

    await asyncio.to_thread(scraper3.get, "https://httpbin.org/cookies/set?cookie2=value2")
    print(f"After cookie2: {scraper3.cookies.get_dict()}")

    resp5 = await asyncio.to_thread(scraper3.get, "https://httpbin.org/cookies")
    print(f"Server sees all cookies: {resp5.json().get('cookies', {})}")

    print("\n=== CONCLUSION ===")
    if scraper3.cookies.get_dict() == resp5.json().get('cookies', {}):
        print("✓ Cookies persist correctly with asyncio.to_thread!")
    else:
        print("✗ Cookie persistence issue detected!")
        print(f"  Jar has: {scraper3.cookies.get_dict()}")
        print(f"  Server sees: {resp5.json().get('cookies', {})}")

if __name__ == "__main__":
    asyncio.run(test_cookie_persistence())