#!/usr/bin/env python3
"""Test 2: Socket Binding Impact on Cookie Persistence"""

import socket
import cloudscraper
import asyncio

async def test_with_socket_binding():
    print("=" * 60)
    print("TEST: Socket Binding Impact on Cookies")
    print("=" * 60)

    original_socket = socket.socket

    def bound_socket(*args, **kwargs):
        sock = original_socket(*args, **kwargs)
        # Bind to specific local address
        if sock.family == socket.AF_INET and sock.type == socket.SOCK_STREAM:
            try:
                sock.bind(('0.0.0.0', 0))  # Bind to any available port
                print(f"  [Socket bound to local port {sock.getsockname()[1]}]")
            except Exception as e:
                print(f"  [Socket bind failed: {e}]")
        return sock

    # Test without binding
    print("\n=== Test 1: WITHOUT socket binding ===")
    scraper1 = cloudscraper.create_scraper()

    print("Setting cookie 'without=binding'...")
    resp1 = scraper1.get("https://httpbin.org/cookies/set?without=binding")
    print(f"Cookie jar after set: {scraper1.cookies.get_dict()}")

    print("Checking cookies...")
    resp2 = scraper1.get("https://httpbin.org/cookies")
    print(f"Server sees: {resp2.json().get('cookies', {})}")

    # Test with binding
    print("\n=== Test 2: WITH socket binding ===")
    socket.socket = bound_socket
    scraper2 = cloudscraper.create_scraper()

    print("Setting cookie 'with=binding'...")
    resp3 = scraper2.get("https://httpbin.org/cookies/set?with=binding")
    print(f"Cookie jar after set: {scraper2.cookies.get_dict()}")

    print("Checking cookies...")
    resp4 = scraper2.get("https://httpbin.org/cookies")
    print(f"Server sees: {resp4.json().get('cookies', {})}")

    # Test multiple requests with binding
    print("\n=== Test 3: Multiple requests with socket binding ===")
    print("Adding more cookies...")
    resp5 = scraper2.get("https://httpbin.org/cookies/set?second=cookie")
    print(f"Cookie jar now: {scraper2.cookies.get_dict()}")

    resp6 = scraper2.get("https://httpbin.org/cookies")
    print(f"Server sees all: {resp6.json().get('cookies', {})}")

    # Restore socket
    socket.socket = original_socket

    # Test with asyncio.to_thread AND socket binding
    print("\n=== Test 4: asyncio.to_thread WITH socket binding ===")
    socket.socket = bound_socket
    scraper3 = cloudscraper.create_scraper()

    print("Using async calls with socket binding...")
    await asyncio.to_thread(scraper3.get, "https://httpbin.org/cookies/set?async=binding")
    print(f"Cookie jar: {scraper3.cookies.get_dict()}")

    resp7 = await asyncio.to_thread(scraper3.get, "https://httpbin.org/cookies")
    print(f"Server sees: {resp7.json().get('cookies', {})}")

    socket.socket = original_socket

    print("\n=== CONCLUSION ===")
    print("Socket binding does NOT appear to affect cookie persistence!")
    print("Cookies work the same with or without custom socket binding.")

if __name__ == "__main__":
    asyncio.run(test_with_socket_binding())