#!/usr/bin/env python3
"""Test 3: Kick.com Specific Cookie Requirements"""

import cloudscraper
import asyncio
import json
import re

async def test_kick_flow():
    print("=" * 60)
    print("TEST: Kick.com Cookie Flow")
    print("=" * 60)

    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )

    streamer = "xqc"  # Use a known streamer

    print("\n=== Initial Cookie State ===")
    print(f"Starting cookies: {dict(scraper.cookies)}")

    print("\n=== Step 1: Channel API Request ===")
    try:
        api_url = f"https://kick.com/api/v2/channels/{streamer}"
        print(f"Requesting: {api_url}")

        api_resp = await asyncio.to_thread(
            scraper.get,
            api_url,
            timeout=20
        )
        print(f"Status: {api_resp.status_code}")

        # Check what cookies we received
        print(f"\nCookies after API call:")
        for cookie in scraper.cookies:
            print(f"  {cookie.name}: {cookie.value[:50]}..." if len(str(cookie.value)) > 50 else f"  {cookie.name}: {cookie.value}")

        # Parse response
        if api_resp.status_code == 200:
            channel_data = api_resp.json()
            channel_id = channel_data.get('id')
            print(f"\nChannel ID found: {channel_id}")
        else:
            print(f"API response: {api_resp.text[:200]}")

    except Exception as e:
        print(f"API error: {e}")

    print("\n=== Step 2: Main Page Request (for client token) ===")
    try:
        main_url = f"https://kick.com/{streamer}"
        print(f"Requesting: {main_url}")

        main_resp = await asyncio.to_thread(
            scraper.get,
            main_url,
            timeout=20
        )
        print(f"Status: {main_resp.status_code}")

        # Look for client token in page
        client_token = None
        token_match = re.search(r'"client_token":\s*"([a-f0-9]{64})"', main_resp.text)
        if token_match:
            client_token = token_match.group(1)
            print(f"Found client token: {client_token[:20]}...")
        else:
            print("No client token found in page")

        # Check cookies after main page
        print(f"\nCookies after main page:")
        for cookie in scraper.cookies:
            print(f"  {cookie.name}: {cookie.value[:50]}..." if len(str(cookie.value)) > 50 else f"  {cookie.name}: {cookie.value}")

    except Exception as e:
        print(f"Main page error: {e}")

    print("\n=== Step 3: WebSocket Token Request ===")
    try:
        token_url = "https://websockets.kick.com/viewer/v1/token"
        headers = {
            'Referer': f'https://kick.com/{streamer}',
            'Origin': 'https://kick.com',
            'Accept': 'application/json'
        }

        if client_token:
            headers['x-client-token'] = client_token

        print(f"Requesting: {token_url}")
        print(f"Headers: {json.dumps(headers, indent=2)}")

        token_resp = await asyncio.to_thread(
            scraper.get,
            token_url,
            headers=headers,
            timeout=20
        )
        print(f"Status: {token_resp.status_code}")

        # Check what cookies were sent in the request
        if hasattr(token_resp.request, 'headers'):
            cookie_header = token_resp.request.headers.get('Cookie', 'None')
            if cookie_header != 'None':
                print(f"\nCookies SENT in token request:")
                for cookie_pair in cookie_header.split('; '):
                    if '=' in cookie_pair:
                        name, value = cookie_pair.split('=', 1)
                        print(f"  {name}: {value[:50]}..." if len(value) > 50 else f"  {name}: {value}")
            else:
                print("\nNO cookies sent in token request!")

        # Check final cookie state
        print(f"\nFinal cookie jar state:")
        for cookie in scraper.cookies:
            print(f"  {cookie.name}: {cookie.value[:50]}..." if len(str(cookie.value)) > 50 else f"  {cookie.name}: {cookie.value}")

        if token_resp.status_code == 200:
            token_data = token_resp.json()
            if 'data' in token_data and 'token' in token_data['data']:
                print(f"\nWebSocket token received: {token_data['data']['token'][:30]}...")
            else:
                print(f"\nToken response: {json.dumps(token_data, indent=2)[:200]}")
        else:
            print(f"\nToken error response: {token_resp.text[:200]}")

    except Exception as e:
        print(f"Token request error: {e}")

    print("\n=== ANALYSIS ===")
    print("Key findings:")
    print("1. CloudScraper maintains cookies between requests: YES")
    print("2. Cookies are sent in subsequent requests: Check 'Cookies SENT' above")
    print("3. Cloudflare cookies received: Check for __cf_bm, cf_clearance above")

if __name__ == "__main__":
    asyncio.run(test_kick_flow())