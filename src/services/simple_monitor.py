"""
Simple polling-based monitor service that works like the JavaScript version.
Uses browser fallback for Cloudflare bypass, no complex WebSocket discovery.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from models import Streamer, StreamerStatus, StreamerStatusUpdate, StatusEventCreate, EventType
from .database import DatabaseService
from .auth import KickOAuthService

logger = logging.getLogger(__name__)


class SimpleMonitorService:
    """
    Simple polling-based monitor service.
    Polls API endpoints directly like the working JavaScript version.
    """
    
    def __init__(
        self,
        database_service: DatabaseService,
        oauth_service: KickOAuthService,
        check_interval: int = 30
    ):
        self.database_service = database_service
        self.oauth_service = oauth_service  # This has browser fallback built-in
        self.check_interval = check_interval
        
        # Service state
        self._is_running = False
        self._stop_event = asyncio.Event()
        self._main_task: Optional[asyncio.Task] = None
        
        # Statistics
        self._total_checks = 0
        self._successful_checks = 0
        self._failed_checks = 0
    
    async def start(self):
        """Start the simple monitoring service."""
        if self._is_running:
            logger.warning("Simple monitor already running")
            return
            
        logger.info("Starting simple polling monitor")
        
        try:
            # Ensure services are connected
            if not self.database_service._is_connected:
                await self.database_service.connect()
            
            if not self.oauth_service._session:
                await self.oauth_service.start()
            
            self._is_running = True
            self._main_task = asyncio.create_task(self._monitoring_loop())
            
            logger.info("Simple monitor started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start simple monitor: {e}")
            await self.stop()
            raise
    
    async def stop(self):
        """Stop the monitoring service."""
        if not self._is_running:
            return
            
        logger.info("Stopping simple monitor")
        self._stop_event.set()
        
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        
        self._is_running = False
        logger.info("Simple monitor stopped")
    
    async def _monitoring_loop(self):
        """Main monitoring loop - like your JavaScript version."""
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
        """Check status of all streamers - exactly like your JS checkAllStreamers()."""
        logger.info('--- Starting streamer check cycle ---')
        self._total_checks += 1
        
        try:
            # Get streamers from database
            streamers = await self.database_service.get_active_streamers()
            
            if not streamers:
                logger.info('No active streamers found. Cycle finished.')
                return
            
            logger.info(f'Found {len(streamers)} streamers to check.')
            streamer_statuses = {}
            cycle_success = True
            
            # Process each streamer (like your JS version)
            for streamer in streamers:
                try:
                    status = await self._process_streamer(streamer)
                    streamer_statuses[streamer.username] = status
                except Exception as e:
                    logger.error(f"Error checking {streamer.username}: {e}")
                    streamer_statuses[streamer.username] = 'error'
                    cycle_success = False
            
            # Log results (same as your JS)
            for username, status in streamer_statuses.items():
                logger.info(f'Streamer {username}: {status}')
            
            if cycle_success:
                self._successful_checks += 1
            else:
                self._failed_checks += 1
                
        except Exception as e:
            logger.error(f'Error in check cycle: {e}')
            self._failed_checks += 1
        finally:
            logger.info('--- Streamer check cycle finished ---')
    
    async def _process_streamer(self, streamer: Streamer) -> str:
        """Process single streamer - like your JS processStreamer()."""
        logger.debug(f'Checking status for streamer: {streamer.username}')
        
        try:
            # Fetch data using OAuth with browser fallback (like your JS fetchApiDataWithBrowser)
            data = await self.oauth_service.get_channel_info(streamer.username)
            
            if not data:
                logger.debug(f'No data returned for {streamer.username}. Setting offline.')
                await self._update_streamer_status(streamer, 'offline')
                return 'offline'
            
            # Parse livestream status (same logic as your JS)
            is_live = data.get('livestream') and data['livestream'].get('is_live', False)
            new_status = 'online' if is_live else 'offline'
            
            # Update database if status changed
            if new_status != streamer.status.value:
                await self._update_streamer_status(streamer, new_status)
                
                # Update playback URL if live (like your JS)
                if is_live and data['livestream'].get('playback_url'):
                    await self._update_playback_url(streamer, data['livestream']['playback_url'])
            
            return new_status
            
        except Exception as e:
            logger.error(f'Failed to process {streamer.username}: {e}')
            return 'error'
    
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
                return  # Don't update for error status
            
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
    
    async def _update_playback_url(self, streamer: Streamer, playback_url: str):
        """Update playback URL for live streamer."""
        try:
            # This would need a database method - for now just log
            logger.debug(f"Would update playback URL for {streamer.username}: {playback_url}")
        except Exception as e:
            logger.error(f"Error updating playback URL for {streamer.username}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        return {
            "is_running": self._is_running,
            "check_interval": self.check_interval,
            "total_checks": self._total_checks,
            "successful_checks": self._successful_checks,
            "failed_checks": self._failed_checks,
            "success_rate": (
                self._successful_checks / max(1, self._total_checks)
            ) * 100,
            "oauth_stats": self.oauth_service.get_stats()
        }
    
    @property
    def is_running(self) -> bool:
        """Check if service is currently running."""
        return self._is_running