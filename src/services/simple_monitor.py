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
        self._start_time: Optional[datetime] = None
        
        # Statistics
        self._total_checks = 0
        self._successful_checks = 0
        self._failed_checks = 0
        
        # Cached streamer data for manual mode
        self._cached_streamers: List[Streamer] = []
    
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
            
            self._start_time = datetime.now(timezone.utc)
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
            
            # Update cached streamers for manual mode
            self._cached_streamers = streamers
            
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
            livestream = data.get('livestream')
            is_live = livestream and livestream.get('is_live', False)
            new_status = 'online' if is_live else 'offline'

            # Extract viewer count and livestream ID for analytics
            viewer_count = None
            livestream_id = None
            if livestream and is_live:
                viewer_count = livestream.get('viewer_count') or livestream.get('viewers')
                livestream_id = livestream.get('id')

                # Log viewer count for monitoring
                if viewer_count is not None:
                    logger.debug(f'{streamer.username} has {viewer_count} viewers')

            # Extract additional streamer metadata from channel data
            profile_updates = {}
            if data:
                # Extract profile information that might be available
                if 'bio' in data:
                    profile_updates['bio'] = data['bio']
                if 'profile_picture' in data:
                    profile_updates['profile_picture_url'] = data['profile_picture']
                elif 'avatar' in data:
                    profile_updates['profile_picture_url'] = data['avatar']
                if 'followers_count' in data:
                    profile_updates['follower_count'] = data['followers_count']
                elif 'followersCount' in data:
                    profile_updates['follower_count'] = data['followersCount']

                # Always update is_live status
                profile_updates['is_live'] = is_live

            # Handle status changes and viewer data updates separately
            status_changed = new_status != streamer.status.value

            if status_changed:
                # Status actually changed - use full status update
                await self._update_streamer_status(streamer, new_status, viewer_count, livestream_id)
            elif viewer_count is not None:
                # Status same but we have viewer data to update
                await self._update_viewer_data_only(streamer, viewer_count, livestream_id)

            # Update profile information if we have any changes
            if profile_updates:
                await self._update_profile_data(streamer, profile_updates)

            # Update playback URL if live (like your JS)
            if is_live and livestream.get('playback_url'):
                await self._update_playback_url(streamer, livestream['playback_url'])
            
            return new_status
            
        except Exception as e:
            logger.error(f'Failed to process {streamer.username}: {e}')
            return 'error'
    
    async def _update_streamer_status(self, streamer: Streamer, new_status: str,
                                     viewer_count: Optional[int] = None,
                                     livestream_id: Optional[int] = None):
        """Update streamer status in database with viewer count."""
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
                # Update viewer statistics if we have viewer data
                if viewer_count is not None and new_status == 'online':
                    await self.database_service.update_streamer_viewer_stats(
                        streamer.id, viewer_count, livestream_id
                    )

                # Create status event with viewer count
                event_create = StatusEventCreate(
                    streamer_id=streamer.id,
                    event_type=event_type,
                    previous_status=streamer.status,
                    new_status=status_enum,
                    event_timestamp=datetime.now(timezone.utc),
                    received_timestamp=datetime.now(timezone.utc),
                    event_data={
                        'livestream_id': livestream_id if livestream_id else None
                    },
                    viewer_count=viewer_count
                )

                await self.database_service.create_status_event(event_create)
                
                logger.info(f"Updated {streamer.username}: {streamer.status.value} -> {new_status}")
        
        except Exception as e:
            logger.error(f"Error updating {streamer.username} status: {e}")
    
    async def _update_viewer_data_only(self, streamer: Streamer, viewer_count: int, livestream_id: Optional[int] = None):
        """Update only viewer data without changing status."""
        try:
            # Update viewer statistics in database
            if viewer_count is not None:
                await self.database_service.update_streamer_viewer_stats(
                    streamer.id, viewer_count, livestream_id
                )
                logger.debug(f"Updated viewer count for {streamer.username}: {viewer_count}")

        except Exception as e:
            logger.error(f"Error updating viewer data for {streamer.username}: {e}")

    async def _update_profile_data(self, streamer: Streamer, profile_updates: dict):
        """Update streamer profile information."""
        try:
            # Build update query dynamically based on available fields
            update_fields = []
            values = []
            param_count = 1

            for field, value in profile_updates.items():
                if value is not None:  # Only update non-null values
                    update_fields.append(f"{field} = ${param_count}")
                    values.append(value)
                    param_count += 1

            if update_fields:
                query = f"""
                UPDATE streamer
                SET {', '.join(update_fields)}, updated_at = NOW()
                WHERE id = ${param_count}
                """
                values.append(streamer.id)

                async with self.database_service.get_connection() as conn:
                    await conn.execute(query, *values)

                logger.debug(f"Updated profile for {streamer.username}: {list(profile_updates.keys())}")

        except Exception as e:
            logger.error(f"Error updating profile data for {streamer.username}: {e}")

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
    
    def get_monitoring_stats(self) -> Dict[str, Any]:
        """Get comprehensive monitoring statistics compatible with manual mode."""
        from datetime import datetime, timezone, timedelta
        
        runtime = timedelta(seconds=0)
        if self._start_time:
            runtime = datetime.now(timezone.utc) - self._start_time
        
        # Count streamers by status from cached data
        total_monitored = len(self._cached_streamers)
        online_count = sum(1 for s in self._cached_streamers if s.status.value == 'online')
        offline_count = sum(1 for s in self._cached_streamers if s.status.value == 'offline')
        unknown_count = sum(1 for s in self._cached_streamers if s.status.value == 'unknown')
        
        return {
            "service_status": {
                "is_running": self._is_running,
                "mode": "simple",
                "uptime_seconds": runtime.total_seconds(),
                "start_time": self._start_time.isoformat() if hasattr(self, '_start_time') and self._start_time else None
            },
            "streamers": {
                "total_monitored": total_monitored,
                "online": online_count,
                "offline": offline_count,
                "unknown": unknown_count,
                "subscribed": 0,  # N/A for simple mode
                "failed": 0
            },
            "connections": {
                "websocket_connected": False,  # Simple mode doesn't use WebSocket
                "oauth_authenticated": bool(self.oauth_service._session)
            },
            "processing": {
                "total_checks": self._total_checks,
                "successful_checks": self._successful_checks,
                "failed_checks": self._failed_checks,
                "success_rate": (self._successful_checks / max(1, self._total_checks)) * 100,
                "events_processed": 0,  # Simple mode doesn't track events separately
                "events_pending": 0
            }
        }
    
    def get_streamer_details(self) -> List[Dict[str, Any]]:
        """Get detailed information about all monitored streamers."""
        try:
            result = []
            
            for streamer in self._cached_streamers:
                result.append({
                    "id": streamer.id,
                    "username": streamer.username,
                    "kick_user_id": streamer.kick_user_id,
                    "display_name": streamer.display_name,
                    "status": streamer.status.value,
                    "last_seen_online": streamer.last_seen_online.isoformat() if streamer.last_seen_online else None,
                    "last_status_update": streamer.last_status_update.isoformat() if streamer.last_status_update else None,
                    "current_viewers": getattr(streamer, 'current_viewers', None),
                    "peak_viewers": getattr(streamer, 'peak_viewers', None),
                    "avg_viewers": getattr(streamer, 'avg_viewers', None),
                    "is_subscribed": False,  # Simple mode doesn't use subscriptions
                    "subscription_time": None,
                    "consecutive_failures": 0,  # Simple mode doesn't track this
                    "last_event_timestamp": streamer.last_status_update.isoformat() if streamer.last_status_update else None,
                    "pending_events_count": 0
                })
            
            return sorted(result, key=lambda x: x["username"])
        except Exception as e:
            logger.error(f"Error getting streamer details: {e}")
            return []
    
    @property
    def is_running(self) -> bool:
        """Check if service is currently running."""
        return self._is_running