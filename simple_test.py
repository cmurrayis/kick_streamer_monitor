#!/usr/bin/env python3
"""
Simple test script to diagnose Cloudflare vs OAuth issues.
Run from project root: python simple_test.py xqc
"""

import asyncio
import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_oauth_simple(username: str):
    """Test OAuth method with minimal dependencies."""
    print(f"\n=== Testing OAuth Method for {username} ===")
    
    # Get credentials
    client_id = os.getenv('KICK_CLIENT_ID')
    client_secret = os.getenv('KICK_CLIENT_SECRET')
    
    if not client_id or not client_secret:
        print("   ✗ Missing KICK_CLIENT_ID or KICK_CLIENT_SECRET")
        print("   Set these in your .env file or environment")
        return False
    
    print(f"1. Found credentials (ID: {client_id[:10]}...)")
    
    try:
        import aiohttp
        
        # OAuth token request
        token_url = "https://id.kick.com/oauth/token"
        api_url = f"https://kick.com/api/v1/channels/{username}"
        
        async with aiohttp.ClientSession() as session:
            print("2. Requesting OAuth token...")
            
            # Get access token
            token_data = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret
            }
            
            async with session.post(token_url, data=token_data) as response:
                if response.status == 200:
                    token_info = await response.json()
                    access_token = token_info.get('access_token')
                    print(f"   ✓ Token obtained")
                else:
                    error_text = await response.text()
                    print(f"   ✗ Token failed: HTTP {response.status}")
                    print(f"   Error: {error_text[:100]}")
                    return False
            
            print("3. Making API request...")
            
            # Use token to call API
            headers = {
                'Authorization': f'Bearer {access_token}',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Referer': 'https://kick.com/',
                'Origin': 'https://kick.com'
            }
            
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    livestream = data.get('livestream')
                    if livestream and livestream.get('is_live'):
                        status = "ONLINE"
                    else:
                        status = "OFFLINE"
                    print(f"   ✓ Success: {username} is {status}")
                    return True
                elif response.status == 403:
                    error_text = await response.text()
                    print(f"   ✗ 403 BLOCKED by Cloudflare")
                    print(f"   Response: {error_text[:200]}")
                    return False
                else:
                    error_text = await response.text()
                    print(f"   ✗ API failed: HTTP {response.status}")
                    print(f"   Error: {error_text[:100]}")
                    return False
                    
    except Exception as e:
        print(f"   ✗ OAuth failed: {e}")
        return False

async def test_browser_simple(username: str):
    """Test browser method with playwright."""
    print(f"\n=== Testing Browser Method for {username} ===")
    
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("   ✗ Playwright not installed")
        print("   Run: pip install playwright && playwright install chromium")
        return False
    
    api_url = f"https://kick.com/api/v1/channels/{username}"
    
    try:
        async with async_playwright() as playwright:
            print("1. Starting browser...")
            
            browser = await playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )
            
            page = await browser.new_page()
            
            # Set realistic user agent
            await page.set_extra_http_headers({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            print("2. Fetching channel data...")
            response = await page.goto(api_url, wait_until='networkidle')
            
            if response.status == 200:
                # Extract JSON from page
                data = await page.evaluate("""() => {
                    const text = document.body.textContent;
                    try { return JSON.parse(text); } 
                    catch { return null; }
                }""")
                
                await browser.close()
                
                if data:
                    livestream = data.get('livestream')
                    if livestream and livestream.get('is_live'):
                        status = "ONLINE"
                    else:
                        status = "OFFLINE"
                    print(f"   ✓ Success: {username} is {status}")
                    return True
                else:
                    print("   ✗ Failed to parse JSON")
                    return False
            else:
                await browser.close()
                print(f"   ✗ Browser failed: HTTP {response.status}")
                return False
                
    except Exception as e:
        print(f"   ✗ Browser failed: {e}")
        return False

async def main():
    """Main test function."""
    if len(sys.argv) != 2:
        print("Usage: python simple_test.py <username>")
        print("Example: python simple_test.py xqc")
        sys.exit(1)
    
    username = sys.argv[1]
    
    print(f"Testing API access methods for: {username}")
    print("=" * 50)
    
    # Test both methods
    oauth_success = await test_oauth_simple(username)
    browser_success = await test_browser_simple(username)
    
    # Summary
    print(f"\n=== Results ===")
    print(f"OAuth Method:   {'✓ SUCCESS' if oauth_success else '✗ FAILED'}")
    print(f"Browser Method: {'✓ SUCCESS' if browser_success else '✗ FAILED'}")
    
    # Analysis
    if oauth_success and browser_success:
        print("\n✅ Both methods work - OAuth credentials are valid!")
        print("   Recommendation: Use OAuth mode for better performance")
    elif oauth_success and not browser_success:
        print("\n✅ OAuth works, browser setup issue")
        print("   Recommendation: Use OAuth mode")
    elif not oauth_success and browser_success:
        print("\n⚠️  OAuth blocked by Cloudflare, browser works")
        print("   This confirms the Cloudflare blocking issue")
        print("   Recommendation: Use browser or hybrid mode")
    else:
        print("\n❌ Both methods failed")
        print("   Check network connection and credentials")

if __name__ == "__main__":
    asyncio.run(main())