"""
Browser-based API client using playwright to bypass Cloudflare protection.
Inspired by the working Puppeteer solution.
"""

import asyncio
import logging
import json
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


class BrowserAPIClient:
    """Browser-based client to fetch Kick.com API data, bypassing Cloudflare."""
    
    def __init__(self, headless: bool = True, proxy_url: Optional[str] = None):
        self.headless = headless
        self.proxy_url = proxy_url
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        
    async def __aenter__(self):
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def start(self):
        """Start the browser instance."""
        self._playwright = await async_playwright().start()
        
        # Launch options similar to the working JS code
        launch_options = {
            'headless': self.headless,
            'args': [
                '--no-sandbox',
                '--disable-setuid-sandbox', 
                '--disable-dev-shm-usage',
                '--disable-web-security'
            ]
        }
        
        if self.proxy_url:
            launch_options['proxy'] = {'server': self.proxy_url}
            
        self._browser = await self._playwright.chromium.launch(**launch_options)
        
        # Create context with realistic browser settings
        self._context = await self._browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        
        logger.info("Browser client started")
    
    async def close(self):
        """Close the browser instance."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser client closed")
    
    async def fetch_channel_data(self, username: str) -> Optional[Dict[str, Any]]:
        """
        Fetch channel data for a streamer using browser automation.
        
        Args:
            username: Streamer username
            
        Returns:
            Channel data dict or None if failed
        """
        if not self._context:
            raise RuntimeError("Browser client not started")
            
        api_url = f"https://kick.com/api/v1/channels/{username}"
        page = None
        
        try:
            page = await self._context.new_page()
            
            logger.debug(f"Fetching: {api_url}")
            
            # Navigate to API endpoint and wait for network idle
            response = await page.goto(api_url, wait_until='networkidle')
            
            if not response or not response.ok:
                logger.warning(f"Non-OK response {response.status if response else 'None'} for {username}")
                return None
                
            # Extract JSON from page content
            data = await page.evaluate("""() => {
                const responseText = document.body.textContent;
                try {
                    return JSON.parse(responseText);
                } catch (e) {
                    console.error('JSON parse error:', e.message);
                    console.error('Response text:', responseText);
                    return null;
                }
            }""")
            
            if data:
                logger.debug(f"Successfully fetched data for {username}")
                return data
            else:
                logger.warning(f"Failed to parse JSON response for {username}")
                return None
                
        except Exception as e:
            logger.error(f"Browser fetch failed for {username}: {e}")
            return None
            
        finally:
            if page:
                await page.close()
    
    async def check_streamer_status(self, username: str) -> str:
        """
        Check if a streamer is online or offline.
        
        Args:
            username: Streamer username
            
        Returns:
            'online', 'offline', or 'unknown'
        """
        data = await self.fetch_channel_data(username)
        
        if not data:
            return 'unknown'
            
        # Check livestream status
        if 'livestream' in data and data['livestream']:
            livestream = data['livestream']
            if isinstance(livestream, dict) and livestream.get('is_live', False):
                return 'online'
                
        return 'offline'
    
    async def get_playback_url(self, username: str) -> Optional[str]:
        """
        Get playback URL for a live stream.
        
        Args:
            username: Streamer username
            
        Returns:
            Playback URL if live, None otherwise
        """
        data = await self.fetch_channel_data(username)
        
        if not data:
            return None
            
        livestream = data.get('livestream')
        if isinstance(livestream, dict) and livestream.get('is_live', False):
            return livestream.get('playback_url')
            
        return None