"""
CLI command to test OAuth vs Browser methods for debugging Cloudflare issues.
"""

import asyncio
import sys
import os
from pathlib import Path

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.auth import KickOAuthService, OAuthConfig
from services.browser_client import BrowserAPIClient
from services.hybrid_monitor import HybridMonitorService
from services.database import DatabaseService, DatabaseConfig
from lib.config import load_config

async def test_oauth_method(username: str):
    """Test OAuth method specifically."""
    print(f"\n=== Testing OAuth Method for {username} ===")
    
    config = load_config()
    
    oauth_config = OAuthConfig(
        client_id=config.kick_client_id,
        client_secret=config.kick_client_secret
    )
    
    async with KickOAuthService(oauth_config) as oauth_service:
        try:
            print("1. Getting OAuth token...")
            token = await oauth_service.get_access_token()
            print(f"   ✓ Token obtained (expires in {oauth_service._current_token.expires_in}s)")
            
            print("2. Making API request...")
            data = await oauth_service.get_channel_info(username)
            
            if data:
                livestream = data.get('livestream')
                if livestream and livestream.get('is_live'):
                    status = "ONLINE"
                else:
                    status = "OFFLINE"
                print(f"   ✓ Success: {username} is {status}")
                return True
            else:
                print("   ✗ No data returned")
                return False
                
        except Exception as e:
            print(f"   ✗ OAuth failed: {e}")
            return False

async def test_browser_method(username: str):
    """Test browser method specifically."""
    print(f"\n=== Testing Browser Method for {username} ===")
    
    async with BrowserAPIClient(headless=True) as browser:
        try:
            print("1. Starting browser...")
            print("2. Fetching channel data...")
            
            data = await browser.fetch_channel_data(username)
            
            if data:
                livestream = data.get('livestream')
                if livestream and livestream.get('is_live'):
                    status = "ONLINE"
                else:
                    status = "OFFLINE"
                print(f"   ✓ Success: {username} is {status}")
                return True
            else:
                print("   ✗ No data returned")
                return False
                
        except Exception as e:
            print(f"   ✗ Browser failed: {e}")
            return False

async def main():
    """Main test function."""
    if len(sys.argv) != 2:
        print("Usage: python test_methods.py <username>")
        print("Example: python test_methods.py xqc")
        sys.exit(1)
    
    username = sys.argv[1]
    
    print(f"Testing API access methods for streamer: {username}")
    print("=" * 50)
    
    # Test OAuth method
    oauth_success = await test_oauth_method(username)
    
    # Test Browser method
    browser_success = await test_browser_method(username)
    
    # Summary
    print(f"\n=== Summary ===")
    print(f"OAuth Method:   {'✓ SUCCESS' if oauth_success else '✗ FAILED'}")
    print(f"Browser Method: {'✓ SUCCESS' if browser_success else '✗ FAILED'}")
    
    if oauth_success:
        print("\n✓ OAuth is working - your credentials are fine!")
        print("  Cloudflare may be intermittent or based on request patterns.")
    elif browser_success:
        print("\n⚠ OAuth blocked by Cloudflare, but browser method works!")
        print("  Recommendation: Use hybrid mode for production.")
    else:
        print("\n✗ Both methods failed - check network connectivity and credentials.")
    
    # Recommendation
    if oauth_success and browser_success:
        print("\nRecommendation: Use OAUTH mode for better performance")
    elif browser_success:
        print("\nRecommendation: Use BROWSER or HYBRID mode")
    else:
        print("\nRecommendation: Check configuration and network settings")

if __name__ == "__main__":
    asyncio.run(main())