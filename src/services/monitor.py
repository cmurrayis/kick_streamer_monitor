"""
Status monitoring service coordinating WebSocket events and database updates.

Main service that orchestrates real-time monitoring by combining WebSocket
events from Kick.com with database updates and status management.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set, Callable
from enum import Enum

from ..models import (
    Streamer, StreamerStatus, StreamerStatusUpdate,
    StatusEvent, StatusEventCreate, EventType,
    Configuration
)
from .database import DatabaseService
from .auth import KickOAuthService  
from .websocket import KickWebSocketService, PusherEvent, PusherEventType, StreamerChannel

logger = logging.getLogger(__name__)


class MonitoringError(Exception):
    """Base exception for monitoring operations."""
    pass


class ServiceNotStartedError(MonitoringError):
    """Service has not been started."""
    pass


class MonitoringMode(str, Enum):
    """Monitoring service operation modes."""
    AUTOMATIC = "automatic"  # Fully automated monitoring
    MANUAL = "manual"       # Manual mode with UI display
    DRY_RUN = "dry_run"    # Testing mode without database updates


class StreamerMonitorState:
    """State tracking for individual streamer monitoring."""
    
    def __init__(self, streamer: Streamer):
        self.streamer = streamer
        self.last_event_timestamp: Optional[datetime] = None
        self.consecutive_failures = 0
        self.last_database_update: Optional[datetime] = None
        self.pending_events: List[StatusEventCreate] = []
        self.is_subscribed = False
        self.subscription_time: Optional[datetime] = None
    
    def update_from_event(self, event: PusherEvent) -> Optional[StatusEventCreate]:
        """
        Process WebSocket event and create status event if needed.
        
        Returns:
            StatusEventCreate if status change detected, None otherwise
        """
        event_timestamp = datetime.now(timezone.utc)
        
        # Determine status change
        if event.event == PusherEventType.STREAMER_IS_LIVE:
            if self.streamer.status != StreamerStatus.ONLINE:
                return StatusEventCreate(
                    streamer_id=self.streamer.id,
                    event_type=EventType.STREAM_START,
                    previous_status=self.streamer.status,
                    new_status=StreamerStatus.ONLINE,
                    event_timestamp=event_timestamp,
                    received_timestamp=event_timestamp,
                    event_data=event.data or {}
                )
        
        elif event.event == PusherEventType.STREAMER_OFFLINE:
            if self.streamer.status != StreamerStatus.OFFLINE:
                return StatusEventCreate(
                    streamer_id=self.streamer.id,
                    event_type=EventType.STREAM_END,
                    previous_status=self.streamer.status,
                    new_status=StreamerStatus.OFFLINE,
                    event_timestamp=event_timestamp,
                    received_timestamp=event_timestamp,
                    event_data=event.data or {}
                )
        
        return None
    
    def should_update_database(self) -> bool:
        """Check if database update is needed based on timing."""
        if not self.last_database_update:
            return True
        
        # Don't update too frequently (minimum 1 second between updates)
        time_since_update = datetime.now(timezone.utc) - self.last_database_update
        return time_since_update.total_seconds() >= 1.0
    
    def mark_database_updated(self) -> None:
        """Mark that database has been updated."""
        self.last_database_update = datetime.now(timezone.utc)
    
    def record_failure(self) -> None:
        """Record a monitoring failure."""
        self.consecutive_failures += 1
    
    def reset_failures(self) -> None:
        """Reset failure counter."""
        self.consecutive_failures = 0


class KickMonitorService:
    """
    Main monitoring service that coordinates all components.
    
    Orchestrates WebSocket connections, database updates, and status monitoring
    for multiple Kick.com streamers with proper error handling and recovery.
    """
    
    def __init__(
        self,
        database_service: DatabaseService,
        oauth_service: KickOAuthService,
        websocket_service: KickWebSocketService,
        mode: MonitoringMode = MonitoringMode.AUTOMATIC,
        update_interval: int = 5,
        max_batch_size: int = 50,
        event_buffer_size: int = 1000
    ):
        self.database_service = database_service
        self.oauth_service = oauth_service
        self.websocket_service = websocket_service
        self.mode = mode
        self.update_interval = update_interval
        self.max_batch_size = max_batch_size
        
        # Service state
        self._is_running = False
        self._start_time: Optional[datetime] = None
        self._stop_event = asyncio.Event()
        
        # Streamer tracking
        self._monitored_streamers: Dict[int, StreamerMonitorState] = {}
        self._username_to_id_map: Dict[str, int] = {}
        
        # Event processing
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=event_buffer_size)
        self._batch_events: List[StatusEventCreate] = []
        self._last_batch_process: Optional[datetime] = None
        
        # Tasks
        self._main_task: Optional[asyncio.Task] = None
        self._event_processor_task: Optional[asyncio.Task] = None
        self._periodic_update_task: Optional[asyncio.Task] = None
        
        # Statistics
        self._total_events_processed = 0
        self._total_status_updates = 0
        self._total_database_writes = 0
        self._last_activity_time: Optional[datetime] = None
        
        # Event handlers
        self._status_change_handlers: List[Callable] = []
    
    async def start(self) -> None:
        """Start the monitoring service."""
        if self._is_running:
            logger.warning("Monitoring service already running")
            return
        
        logger.info(f"Starting monitoring service in {self.mode.value} mode")
        
        try:
            # Ensure all services are connected
            if not self.database_service._is_connected:
                await self.database_service.connect()
            
            if not self.oauth_service._session:
                await self.oauth_service.start()
            
            # Load streamers from database
            await self._load_streamers()
            
            # Setup WebSocket event handlers
            self._setup_websocket_handlers()
            
            # Start WebSocket service
            await self.websocket_service.start()
            
            # Subscribe to streamer channels
            await self._subscribe_to_streamers()
            
            # Start processing tasks
            self._event_processor_task = asyncio.create_task(self._event_processor())
            self._periodic_update_task = asyncio.create_task(self._periodic_updater())
            self._main_task = asyncio.create_task(self._main_monitor_loop())
            
            self._is_running = True
            self._start_time = datetime.now(timezone.utc)
            
            logger.info(f"Monitoring service started with {len(self._monitored_streamers)} streamers")
            
        except Exception as e:
            logger.error(f"Failed to start monitoring service: {e}")
            await self.stop()
            raise MonitoringError(f"Service startup failed: {e}") from e
    
    async def stop(self) -> None:
        """Stop the monitoring service."""
        if not self._is_running:
            return
        
        logger.info("Stopping monitoring service")
        self._stop_event.set()
        
        # Cancel tasks
        for task in [self._main_task, self._event_processor_task, self._periodic_update_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Process remaining events
        await self._flush_event_queue()
        
        # Stop WebSocket service
        await self.websocket_service.stop()
        
        self._is_running = False
        
        runtime = datetime.now(timezone.utc) - self._start_time if self._start_time else timedelta(0)
        logger.info(f"Monitoring service stopped after {runtime.total_seconds():.1f} seconds")
    
    async def _load_streamers(self) -> None:
        """Load active streamers from database."""
        logger.info("Loading active streamers from database")
        
        try:
            streamers = await self.database_service.get_active_streamers()
            
            for streamer in streamers:
                state = StreamerMonitorState(streamer)
                self._monitored_streamers[streamer.id] = state
                self._username_to_id_map[streamer.username] = streamer.id
            
            logger.info(f"Loaded {len(streamers)} active streamers")
            
        except Exception as e:
            logger.error(f"Failed to load streamers: {e}")
            raise MonitoringError(f"Failed to load streamers: {e}") from e
    
    def _setup_websocket_handlers(self) -> None:
        """Setup WebSocket event handlers."""
        # Handle streamer status events
        self.websocket_service.add_event_handler(
            PusherEventType.STREAMER_IS_LIVE,
            self._handle_streamer_event
        )
        
        self.websocket_service.add_event_handler(
            PusherEventType.STREAMER_OFFLINE,
            self._handle_streamer_event
        )
        
        logger.debug("WebSocket event handlers configured")
    
    async def _subscribe_to_streamers(self) -> None:
        """Subscribe to WebSocket channels for all monitored streamers."""
        logger.info("Subscribing to streamer channels")
        
        subscription_count = 0
        for state in self._monitored_streamers.values():
            try:
                await self.websocket_service.subscribe_to_streamer(
                    state.streamer.username,
                    state.streamer.kick_user_id
                )
                state.is_subscribed = True
                state.subscription_time = datetime.now(timezone.utc)
                subscription_count += 1
                
            except Exception as e:
                logger.error(f"Failed to subscribe to {state.streamer.username}: {e}")
                state.record_failure()
        
        logger.info(f"Subscribed to {subscription_count} streamer channels")
    
    async def _handle_streamer_event(self, event: PusherEvent) -> None:
        """Handle streamer status change events."""
        try:
            # Extract streamer info from channel
            if not event.channel or not event.channel.startswith("channel."):
                return
            
            kick_user_id = event.channel.split(".", 1)[1]
            
            # Find streamer state
            streamer_state = None
            for state in self._monitored_streamers.values():
                if state.streamer.kick_user_id == kick_user_id:
                    streamer_state = state
                    break
            
            if not streamer_state:
                logger.warning(f"Received event for unknown streamer ID: {kick_user_id}")
                return
            
            # Process event
            status_event = streamer_state.update_from_event(event)
            
            if status_event:
                # Queue event for processing
                try:
                    await self._event_queue.put(status_event)
                    streamer_state.last_event_timestamp = datetime.now(timezone.utc)
                    streamer_state.reset_failures()
                    self._last_activity_time = datetime.now(timezone.utc)
                    
                    logger.info(
                        f"Status change: {streamer_state.streamer.username} "
                        f"{status_event.previous_status} -> {status_event.new_status}"
                    )
                    
                except asyncio.QueueFull:
                    logger.error("Event queue full, dropping event")
            
        except Exception as e:
            logger.error(f"Error handling streamer event: {e}")
    
    async def _event_processor(self) -> None:
        """Process status events from queue."""
        while not self._stop_event.is_set():
            try:
                # Get event from queue with timeout
                try:
                    event = await asyncio.wait_for(
                        self._event_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Add to batch
                self._batch_events.append(event)
                
                # Process batch if it's full or enough time has passed
                should_process = (
                    len(self._batch_events) >= self.max_batch_size or
                    (self._last_batch_process and 
                     datetime.now(timezone.utc) - self._last_batch_process > timedelta(seconds=1))
                )
                
                if should_process:
                    await self._process_event_batch()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Event processor error: {e}")
                await asyncio.sleep(1)
        
        # Process remaining events
        if self._batch_events:
            await self._process_event_batch()
    
    async def _process_event_batch(self) -> None:
        """Process batch of status events."""
        if not self._batch_events:
            return
        
        if self.mode == MonitoringMode.DRY_RUN:
            logger.info(f"DRY RUN: Would process {len(self._batch_events)} events")
            self._batch_events.clear()
            return
        
        try:
            # Group events by streamer
            streamer_updates: Dict[int, StatusEventCreate] = {}
            
            for event in self._batch_events:
                # Keep latest event per streamer
                streamer_updates[event.streamer_id] = event
            
            # Process each streamer update
            for streamer_id, event in streamer_updates.items():
                await self._process_single_event(streamer_id, event)
            
            self._total_events_processed += len(self._batch_events)
            self._batch_events.clear()
            self._last_batch_process = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Error processing event batch: {e}")
    
    async def _process_single_event(self, streamer_id: int, event: StatusEventCreate) -> None:
        """Process single status event with database update."""
        try:
            state = self._monitored_streamers.get(streamer_id)
            if not state:
                logger.warning(f"No state found for streamer ID: {streamer_id}")
                return
            
            # Check if database update is needed
            if not state.should_update_database():
                return
            
            # Update streamer status in database
            status_update = StreamerStatusUpdate(
                new_status=event.new_status,
                previous_status=event.previous_status,
                timestamp=event.event_timestamp
            )
            
            updated_streamer = await self.database_service.update_streamer_status(
                streamer_id,
                status_update
            )
            
            if updated_streamer:
                # Create status event record
                created_event = await self.database_service.create_status_event(event)
                
                # Mark event as processed
                await self.database_service.mark_event_processed(
                    created_event.id,
                    datetime.now(timezone.utc)
                )
                
                # Update local state
                state.streamer = updated_streamer
                state.mark_database_updated()
                
                self._total_status_updates += 1
                self._total_database_writes += 2  # Streamer update + event insert
                
                # Call status change handlers
                await self._call_status_change_handlers(state.streamer, event)
                
                logger.debug(f"Updated {state.streamer.username} status: {event.new_status.value}")
            
        except Exception as e:
            logger.error(f"Error processing event for streamer {streamer_id}: {e}")
            if state:
                state.record_failure()
    
    async def _periodic_updater(self) -> None:
        """Perform periodic maintenance tasks."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self.update_interval)
                
                if self._stop_event.is_set():
                    break
                
                # Check for stale connections
                await self._check_connection_health()
                
                # Process any remaining batched events
                if self._batch_events and (
                    not self._last_batch_process or
                    datetime.now(timezone.utc) - self._last_batch_process > timedelta(seconds=5)
                ):
                    await self._process_event_batch()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic updater error: {e}")
    
    async def _check_connection_health(self) -> None:
        """Check health of all service connections."""
        # Check WebSocket connection
        ws_status = self.websocket_service.get_connection_status()
        if not ws_status["is_connected"]:
            logger.warning("WebSocket connection lost")
        
        # Check database connection
        db_health = await self.database_service.health_check()
        if db_health["status"] != "healthy":
            logger.warning(f"Database unhealthy: {db_health.get('error', 'Unknown error')}")
        
        # Check OAuth token
        token_info = self.oauth_service.get_token_info()
        if token_info and token_info["is_expired"]:
            logger.info("OAuth token expired, will refresh on next request")
    
    async def _main_monitor_loop(self) -> None:
        """Main monitoring loop."""
        logger.info("Main monitoring loop started")
        
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(1)
                
                # Check if we should continue running
                if self._stop_event.is_set():
                    break
        
        except asyncio.CancelledError:
            pass
        
        logger.info("Main monitoring loop stopped")
    
    async def _flush_event_queue(self) -> None:
        """Process all remaining events in queue."""
        remaining_events = 0
        
        while not self._event_queue.empty():
            try:
                event = self._event_queue.get_nowait()
                self._batch_events.append(event)
                remaining_events += 1
            except asyncio.QueueEmpty:
                break
        
        if remaining_events > 0:
            logger.info(f"Processing {remaining_events} remaining events")
            await self._process_event_batch()
    
    async def add_streamer(self, username: str, kick_user_id: str) -> bool:
        """Add new streamer to monitoring."""
        try:
            # Check if already monitored
            if username in self._username_to_id_map:
                logger.info(f"Streamer {username} already being monitored")
                return True
            
            # Get or create streamer in database
            existing = await self.database_service.get_streamer_by_username(username)
            
            if existing:
                streamer = existing
            else:
                from ..models import StreamerCreate
                streamer_create = StreamerCreate(
                    kick_user_id=kick_user_id,
                    username=username,
                    status=StreamerStatus.UNKNOWN
                )
                streamer = await self.database_service.create_streamer(streamer_create)
            
            # Add to monitoring
            state = StreamerMonitorState(streamer)
            self._monitored_streamers[streamer.id] = state
            self._username_to_id_map[username] = streamer.id
            
            # Subscribe if service is running
            if self._is_running:
                await self.websocket_service.subscribe_to_streamer(username, kick_user_id)
                state.is_subscribed = True
                state.subscription_time = datetime.now(timezone.utc)
            
            logger.info(f"Added streamer to monitoring: {username}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add streamer {username}: {e}")
            return False
    
    async def remove_streamer(self, username: str) -> bool:
        """Remove streamer from monitoring."""
        try:
            streamer_id = self._username_to_id_map.get(username)
            if not streamer_id:
                logger.warning(f"Streamer {username} not being monitored")
                return False
            
            # Unsubscribe from WebSocket
            if self._is_running:
                await self.websocket_service.unsubscribe_from_streamer(username)
            
            # Remove from tracking
            del self._monitored_streamers[streamer_id]
            del self._username_to_id_map[username]
            
            logger.info(f"Removed streamer from monitoring: {username}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to remove streamer {username}: {e}")
            return False
    
    def add_status_change_handler(self, handler: Callable) -> None:
        """Add handler for status changes."""
        self._status_change_handlers.append(handler)
    
    def remove_status_change_handler(self, handler: Callable) -> None:
        """Remove status change handler."""
        try:
            self._status_change_handlers.remove(handler)
        except ValueError:
            pass
    
    async def _call_status_change_handlers(self, streamer: Streamer, event: StatusEventCreate) -> None:
        """Call all registered status change handlers."""
        for handler in self._status_change_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(streamer, event)
                else:
                    handler(streamer, event)
            except Exception as e:
                logger.error(f"Status change handler error: {e}")
    
    def get_monitoring_stats(self) -> Dict[str, Any]:
        """Get comprehensive monitoring statistics."""
        if not self._is_running:
            raise ServiceNotStartedError("Monitoring service not started")
        
        runtime = datetime.now(timezone.utc) - self._start_time if self._start_time else timedelta(0)
        
        # Streamer statistics
        online_count = sum(1 for s in self._monitored_streamers.values() if s.streamer.status == StreamerStatus.ONLINE)
        offline_count = sum(1 for s in self._monitored_streamers.values() if s.streamer.status == StreamerStatus.OFFLINE)
        unknown_count = sum(1 for s in self._monitored_streamers.values() if s.streamer.status == StreamerStatus.UNKNOWN)
        
        # Subscription statistics
        subscribed_count = sum(1 for s in self._monitored_streamers.values() if s.is_subscribed)
        failed_count = sum(1 for s in self._monitored_streamers.values() if s.consecutive_failures > 0)
        
        return {
            "service_status": {
                "is_running": self._is_running,
                "mode": self.mode.value,
                "uptime_seconds": runtime.total_seconds(),
                "last_activity": self._last_activity_time.isoformat() if self._last_activity_time else None
            },
            "streamers": {
                "total_monitored": len(self._monitored_streamers),
                "online": online_count,
                "offline": offline_count,
                "unknown": unknown_count,
                "subscribed": subscribed_count,
                "failed": failed_count
            },
            "processing": {
                "total_events_processed": self._total_events_processed,
                "total_status_updates": self._total_status_updates,
                "total_database_writes": self._total_database_writes,
                "queue_size": self._event_queue.qsize(),
                "batch_size": len(self._batch_events),
                "last_batch_process": self._last_batch_process.isoformat() if self._last_batch_process else None
            },
            "connections": {
                "websocket": self.websocket_service.get_connection_status(),
                "oauth": self.oauth_service.get_stats()
            }
        }
    
    def get_streamer_details(self) -> List[Dict[str, Any]]:
        """Get detailed information about all monitored streamers."""
        streamers = []
        
        for state in self._monitored_streamers.values():
            streamers.append({
                "id": state.streamer.id,
                "username": state.streamer.username,
                "kick_user_id": state.streamer.kick_user_id,
                "display_name": state.streamer.display_name,
                "status": state.streamer.status.value,
                "last_seen_online": state.streamer.last_seen_online.isoformat() if state.streamer.last_seen_online else None,
                "last_status_update": state.streamer.last_status_update.isoformat() if state.streamer.last_status_update else None,
                "is_subscribed": state.is_subscribed,
                "subscription_time": state.subscription_time.isoformat() if state.subscription_time else None,
                "consecutive_failures": state.consecutive_failures,
                "last_event_timestamp": state.last_event_timestamp.isoformat() if state.last_event_timestamp else None,
                "pending_events_count": len(state.pending_events)
            })
        
        return sorted(streamers, key=lambda x: x["username"])
    
    @property
    def is_running(self) -> bool:
        """Check if service is currently running."""
        return self._is_running