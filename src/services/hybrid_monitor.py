"""
Hybrid monitoring service using browser-based API calls when OAuth fails.
Combines OAuth authentication with browser automation fallback.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from enum import Enum

from models import Streamer, StreamerStatus, StreamerStatusUpdate, StatusEvent, StatusEventCreate, EventType
from .database import DatabaseService
from .auth import KickOAuthService, AuthenticationError
from .browser_client import BrowserAPIClient

logger = logging.getLogger(__name__)


class FetchMethod(str, Enum):
    """Available methods for fetching data."""
    OAUTH = "oauth"
    BROWSER = "browser"
    HYBRID = "hybrid"


class HybridMonitorService:
    """
    Hybrid monitoring service that tries OAuth first, falls back to browser automation.
    Inspired by the working JS Puppeteer solution.
    """
    
    def __init__(
        self,
        database_service: DatabaseService,
        oauth_service: KickOAuthService,
        method: FetchMethod = FetchMethod.HYBRID,
        browser_headless: bool = True,
        proxy_url: Optional[str] = None,
        check_interval: int = 30
    ):
        self.database_service = database_service
        self.oauth_service = oauth_service
        self.method = method
        self.check_interval = check_interval
        
        # Browser client for fallback
        self.browser_client = BrowserAPIClient(
            headless=browser_headless,
            proxy_url=proxy_url
        )
        
        # Service state
        self._is_running = False
        self._stop_event = asyncio.Event()
        self._main_task: Optional[asyncio.Task] = None
        
        # Statistics
        self._oauth_success_count = 0
        self._oauth_failure_count = 0
        self._browser_success_count = 0
        self._browser_failure_count = 0
        self._total_checks = 0
    
    async def start(self):
        """Start the hybrid monitoring service."""
        if self._is_running:
            logger.warning("Hybrid monitor already running")
            return
            
        logger.info(f"Starting hybrid monitor in {self.method.value} mode")
        
        try:
            # Start database connection
            if not self.database_service._is_connected:
                await self.database_service.connect()
            
            # Start OAuth service if using it
            if self.method in [FetchMethod.OAUTH, FetchMethod.HYBRID]:
                if not self.oauth_service._session:
                    await self.oauth_service.start()
            
            # Start browser client if using it
            if self.method in [FetchMethod.BROWSER, FetchMethod.HYBRID]:
                await self.browser_client.start()
            
            self._is_running = True
            self._main_task = asyncio.create_task(self._monitoring_loop())
            
            logger.info("Hybrid monitor started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start hybrid monitor: {e}")
            await self.stop()
            raise
    
    async def stop(self):
        """Stop the hybrid monitoring service."""
        if not self._is_running:
            return
            
        logger.info("Stopping hybrid monitor")
        self._stop_event.set()
        
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        
        # Close services
        await self.browser_client.close()
        
        self._is_running = False
        logger.info("Hybrid monitor stopped")
    
    async def _monitoring_loop(self):
        """Main monitoring loop."""
        logger.info("Starting monitoring cycle")
        
        while not self._stop_event.is_set():
            try:
                await self._check_all_streamers()
                
                # Wait for next cycle
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.check_interval
                    )
                except asyncio.TimeoutError:
                    pass
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5)  # Wait before retrying
    
    async def _check_all_streamers(self):
        """Check status of all streamers."""
        try:
            # Get list of streamers from database
            streamers = await self.database_service.get_active_streamers()
            
            if not streamers:
                logger.info("No active streamers found")
                return
            
            logger.info(f"Checking {len(streamers)} streamers")
            streamer_statuses = {}
            
            # Process each streamer
            for streamer in streamers:
                status = await self._check_streamer_status(streamer.username)
                streamer_statuses[streamer.username] = status
                
                # Update database if status changed
                if status != 'unknown' and status != streamer.status.value:
                    await self._update_streamer_status(streamer, status)
            
            # Log results (similar to JS version)
            for username, status in streamer_statuses.items():
                logger.info(f"Streamer {username}: {status}")
                
            self._total_checks += 1
            
        except Exception as e:
            logger.error(f"Error checking streamers: {e}")
    
    async def _check_streamer_status(self, username: str) -> str:
        """
        Check single streamer status using hybrid approach.
        
        Returns:
            'online', 'offline', or 'unknown'
        """
        if self.method == FetchMethod.OAUTH:
            return await self._check_with_oauth(username)
        elif self.method == FetchMethod.BROWSER:
            return await self._check_with_browser(username)
        else:  # HYBRID
            return await self._check_hybrid(username)
    
    async def _check_with_oauth(self, username: str) -> str:
        """Check streamer status using OAuth API."""
        try:
            data = await self.oauth_service.get_channel_info(username)
            
            if not data:
                logger.warning(f"No data returned for {username}")
                self._oauth_failure_count += 1
                return 'unknown'
            
            # Parse livestream status
            if 'livestream' in data and data['livestream']:
                livestream = data['livestream']
                if isinstance(livestream, dict) and livestream.get('is_live', False):
                    self._oauth_success_count += 1
                    return 'online'
            
            self._oauth_success_count += 1
            return 'offline'
            
        except AuthenticationError as e:
            if "403" in str(e) or "Cloudflare" in str(e):
                logger.error(f"OAuth blocked by Cloudflare for {username}")
            else:
                logger.error(f"OAuth auth error for {username}: {e}")
            self._oauth_failure_count += 1
            return 'unknown'
        except Exception as e:
            logger.error(f"OAuth error for {username}: {e}")
            self._oauth_failure_count += 1
            return 'unknown'
    
    async def _check_with_browser(self, username: str) -> str:
        """Check streamer status using browser automation."""
        try:
            status = await self.browser_client.check_streamer_status(username)
            
            if status != 'unknown':
                self._browser_success_count += 1
            else:
                self._browser_failure_count += 1
            
            return status
            
        except Exception as e:
            logger.error(f"Browser error for {username}: {e}")
            self._browser_failure_count += 1
            return 'unknown'
    
    async def _check_hybrid(self, username: str) -> str:
        """Check streamer status using hybrid approach (OAuth first, browser fallback)."""
        # Try OAuth first
        oauth_status = await self._check_with_oauth(username)
        
        # If OAuth failed due to blocking, try browser
        if oauth_status == 'unknown' and self._oauth_failure_count > self._oauth_success_count:
            logger.info(f"OAuth failing, using browser for {username}")
            return await self._check_with_browser(username)
        
        return oauth_status
    
    async def _update_streamer_status(self, streamer: Streamer, new_status: str):
        """Update streamer status in database."""
        try:
            # Convert string to enum
            if new_status == 'online':
                status_enum = StreamerStatus.ONLINE
                event_type = EventType.STREAM_START
            elif new_status == 'offline':
                status_enum = StreamerStatus.OFFLINE
                event_type = EventType.STREAM_END
            else:
                return  # Don't update for unknown status
            
            # Update streamer status
            status_update = StreamerStatusUpdate(
                new_status=status_enum,
                previous_status=streamer.status,
                timestamp=datetime.now(timezone.utc)
            )
            
            updated_streamer = await self.database_service.update_streamer_status(
                streamer.id,
                status_update
            )
            
            if updated_streamer:
                # Create status event
                event_create = StatusEventCreate(
                    streamer_id=streamer.id,
                    event_type=event_type,
                    previous_status=streamer.status,
                    new_status=status_enum,
                    event_timestamp=datetime.now(timezone.utc),
                    received_timestamp=datetime.now(timezone.utc),
                    event_data={}
                )
                
                await self.database_service.create_status_event(event_create)
                
                logger.info(f"Updated {streamer.username}: {streamer.status.value} -> {new_status}")
        
        except Exception as e:
            logger.error(f"Error updating {streamer.username} status: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        return {
            "method": self.method.value,
            "is_running": self._is_running,
            "total_checks": self._total_checks,
            "oauth": {
                "success_count": self._oauth_success_count,
                "failure_count": self._oauth_failure_count,
                "success_rate": (
                    self._oauth_success_count / max(1, self._oauth_success_count + self._oauth_failure_count)
                ) * 100
            },
            "browser": {
                "success_count": self._browser_success_count,
                "failure_count": self._browser_failure_count,
                "success_rate": (
                    self._browser_success_count / max(1, self._browser_success_count + self._browser_failure_count)
                ) * 100
            }
        }
    
    async def test_methods(self, username: str) -> Dict[str, Any]:
        """Test both OAuth and browser methods for a specific streamer."""
        results = {}
        
        # Test OAuth
        logger.info(f"Testing OAuth for {username}")
        oauth_start = datetime.now()
        oauth_status = await self._check_with_oauth(username)
        oauth_time = (datetime.now() - oauth_start).total_seconds()
        
        results['oauth'] = {
            'status': oauth_status,
            'response_time': oauth_time,
            'success': oauth_status != 'unknown'
        }
        
        # Test Browser
        logger.info(f"Testing browser for {username}")
        browser_start = datetime.now()
        browser_status = await self._check_with_browser(username)
        browser_time = (datetime.now() - browser_start).total_seconds()
        
        results['browser'] = {
            'status': browser_status,
            'response_time': browser_time,
            'success': browser_status != 'unknown'
        }
        
        return results