"""
Analytics data collection service for 1-minute interval metrics and session tracking.

Collects and stores:
- Viewer counts, running status, assigned counts every minute
- Stream session start/end events with analytics
- Performance metrics for monitoring health
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple
import json

from models import Streamer, StreamerStatus
from .database import DatabaseService
from .auth import KickOAuthService

logger = logging.getLogger(__name__)


class StreamerAnalyticsData:
    """Data structure for streamer analytics snapshot."""

    def __init__(
        self,
        streamer_id: int,
        username: str,
        viewers: int,
        running: bool,
        assigned: int,
        status: str,
        recorded_at: datetime
    ):
        self.streamer_id = streamer_id
        self.username = username
        self.viewers = viewers
        self.running = running
        self.assigned = assigned
        self.status = status
        self.recorded_at = recorded_at


class StreamSession:
    """Active stream session tracking."""

    def __init__(self, streamer_id: int, session_start: datetime, kick_livestream_id: Optional[int] = None):
        self.streamer_id = streamer_id
        self.session_start = session_start
        self.kick_livestream_id = kick_livestream_id

        # Analytics tracking
        self.viewer_readings: List[int] = []
        self.peak_viewers = 0
        self.total_minutes = 0

    @property
    def avg_viewers(self) -> int:
        """Calculate average viewers for this session."""
        if not self.viewer_readings:
            return 0
        return int(sum(self.viewer_readings) / len(self.viewer_readings))

    def add_viewer_reading(self, viewers: int):
        """Add a viewer count reading to this session."""
        self.viewer_readings.append(viewers)
        self.peak_viewers = max(self.peak_viewers, viewers)
        self.total_minutes = len(self.viewer_readings)


class AnalyticsService:
    """
    Service for collecting analytics data at 1-minute intervals.

    Responsibilities:
    - Collect viewer counts, running status every minute
    - Track stream sessions (start/end)
    - Calculate peak/average viewers per session
    - Store analytics data in database
    """

    def __init__(
        self,
        database_service: DatabaseService,
        oauth_service: KickOAuthService,
        collection_interval: int = 60  # 1 minute default
    ):
        self.database_service = database_service
        self.oauth_service = oauth_service
        self.collection_interval = collection_interval

        # Service state
        self._is_running = False
        self._stop_event = asyncio.Event()
        self._main_task: Optional[asyncio.Task] = None
        self._start_time: Optional[datetime] = None

        # Session tracking
        self._active_sessions: Dict[int, StreamSession] = {}

        # Statistics
        self._total_collections = 0
        self._successful_collections = 0
        self._failed_collections = 0
        self._database_errors = 0

    async def start(self):
        """Start the analytics collection service."""
        if self._is_running:
            logger.warning("Analytics service is already running")
            return

        logger.info(f"Starting analytics collection service (interval: {self.collection_interval}s)")
        self._is_running = True
        self._start_time = datetime.now(timezone.utc)
        self._stop_event.clear()

        # Start the main collection loop
        self._main_task = asyncio.create_task(self._collection_loop())

    async def stop(self):
        """Stop the analytics collection service."""
        if not self._is_running:
            return

        logger.info("Stopping analytics collection service")
        self._stop_event.set()

        if self._main_task:
            try:
                await asyncio.wait_for(self._main_task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Analytics service shutdown timed out, canceling task")
                self._main_task.cancel()

        self._is_running = False
        self._main_task = None
        logger.info("Analytics collection service stopped")

    async def _collection_loop(self):
        """Main collection loop - runs every minute."""
        logger.info("Analytics collection loop started")

        while not self._stop_event.is_set():
            collection_start = datetime.now(timezone.utc)

            try:
                # Perform analytics collection
                await self._collect_analytics_data()

                self._successful_collections += 1
                collection_time = (datetime.now(timezone.utc) - collection_start).total_seconds()
                logger.debug(f"Analytics collection completed in {collection_time:.2f}s")

            except Exception as e:
                self._failed_collections += 1
                logger.error(f"Analytics collection failed: {e}", exc_info=True)
            finally:
                self._total_collections += 1

            # Wait for next collection interval
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.collection_interval
                )
                break  # Stop event was set
            except asyncio.TimeoutError:
                continue  # Continue with next collection

        logger.info("Analytics collection loop ended")

    async def _collect_analytics_data(self):
        """Collect analytics data for all active streamers."""
        # Get all active streamers
        streamers = await self.database_service.get_active_streamers()

        if not streamers:
            logger.debug("No active streamers found for analytics collection")
            return

        # Collect data for each streamer - ALWAYS record something for each streamer
        analytics_data = []
        recorded_at = datetime.now(timezone.utc).replace(second=0, microsecond=0)  # Round to minute

        logger.info(f"Collecting analytics for {len(streamers)} streamers at {recorded_at}")

        for streamer in streamers:
            try:
                # This method now always returns data (never None)
                data = await self._collect_streamer_data(streamer, recorded_at)
                analytics_data.append(data)

                # Update session tracking
                await self._update_session_tracking(streamer, data)

            except Exception as e:
                logger.error(f"Failed to collect data for streamer {streamer.username}: {e}")
                # Even if session tracking fails, still record offline data point for continuity
                fallback_data = StreamerAnalyticsData(
                    streamer_id=streamer.id,
                    username=streamer.username,
                    viewers=0,
                    running=False,
                    assigned=0,  # Default to 0 if we can't get the count
                    status='offline',
                    recorded_at=recorded_at
                )
                analytics_data.append(fallback_data)

        # Batch insert analytics data
        if analytics_data:
            await self._store_analytics_data(analytics_data)
            logger.info(f"Stored analytics data for {len(analytics_data)} streamers")
        else:
            logger.warning("No analytics data to store - this shouldn't happen!")

    async def _collect_streamer_data(self, streamer: Streamer, recorded_at: datetime) -> StreamerAnalyticsData:
        """Collect analytics data for a single streamer."""
        # Always get assigned user count regardless of API success/failure
        assigned_count = await self._get_assigned_user_count(streamer.id)

        try:
            # Get current stream info from API
            stream_info = await self.oauth_service.get_channel_info(streamer.username)

            if not stream_info:
                # Streamer is offline or API failed - still record the data point
                logger.debug(f"No stream info for {streamer.username} - recording as offline")
                return StreamerAnalyticsData(
                    streamer_id=streamer.id,
                    username=streamer.username,
                    viewers=0,
                    running=False,
                    assigned=assigned_count,
                    status='offline',
                    recorded_at=recorded_at
                )

            # Extract analytics from stream info
            viewers = stream_info.get('viewers', 0)
            is_live = stream_info.get('is_live', False)

            logger.debug(f"Collected data for {streamer.username}: viewers={viewers}, live={is_live}")
            return StreamerAnalyticsData(
                streamer_id=streamer.id,
                username=streamer.username,
                viewers=viewers,
                running=is_live,
                assigned=assigned_count,
                status='online' if is_live else 'offline',
                recorded_at=recorded_at
            )

        except Exception as e:
            # Even if API fails, we still want to record a data point for continuity
            logger.warning(f"API failed for {streamer.username}, recording as offline: {e}")
            return StreamerAnalyticsData(
                streamer_id=streamer.id,
                username=streamer.username,
                viewers=0,
                running=False,
                assigned=assigned_count,
                status='offline',
                recorded_at=recorded_at
            )

    async def _get_assigned_user_count(self, streamer_id: int) -> int:
        """Get number of users assigned to this streamer."""
        try:
            async with self.database_service.transaction() as conn:
                result = await conn.fetch(
                    "SELECT COUNT(*) as count FROM user_streamer_assignments WHERE streamer_id = $1",
                    streamer_id
                )
                return result[0]['count'] if result else 0
        except Exception as e:
            logger.error(f"Failed to get assigned user count for streamer {streamer_id}: {e}")
            return 0

    async def _update_session_tracking(self, streamer: Streamer, data: StreamerAnalyticsData):
        """Update stream session tracking based on current data."""
        streamer_id = data.streamer_id

        if data.running and data.status == 'online':
            # Stream is live
            if streamer_id not in self._active_sessions:
                # Start new session
                logger.info(f"Starting new stream session for {data.username}")
                session = StreamSession(
                    streamer_id=streamer_id,
                    session_start=data.recorded_at
                )
                self._active_sessions[streamer_id] = session

                # Create session record in database
                await self._create_stream_session(session)

            # Add viewer reading to active session
            session = self._active_sessions[streamer_id]
            session.add_viewer_reading(data.viewers)

        else:
            # Stream is offline
            if streamer_id in self._active_sessions:
                # End active session
                session = self._active_sessions[streamer_id]
                logger.info(f"Ending stream session for {data.username} (duration: {session.total_minutes}min, peak: {session.peak_viewers})")

                # Update session in database
                await self._end_stream_session(session, data.recorded_at)

                # Remove from active sessions
                del self._active_sessions[streamer_id]

    async def _create_stream_session(self, session: StreamSession):
        """Create a new stream session record in database."""
        try:
            async with self.database_service.transaction() as conn:
                await conn.execute(
                    """
                    INSERT INTO stream_sessions (streamer_id, session_start, kick_livestream_id)
                    VALUES ($1, $2, $3)
                    """,
                    session.streamer_id,
                    session.session_start,
                    session.kick_livestream_id
                )
        except Exception as e:
            logger.error(f"Failed to create stream session: {e}")

    async def _end_stream_session(self, session: StreamSession, session_end: datetime):
        """End a stream session and update analytics."""
        try:
            async with self.database_service.transaction() as conn:
                await conn.execute(
                    """
                    UPDATE stream_sessions
                    SET session_end = $1, peak_viewers = $2, avg_viewers = $3, total_minutes = $4
                    WHERE streamer_id = $5 AND session_end IS NULL
                    """,
                    session_end,
                    session.peak_viewers,
                    session.avg_viewers,
                    session.total_minutes,
                    session.streamer_id
                )
        except Exception as e:
            logger.error(f"Failed to end stream session: {e}")

    async def _store_analytics_data(self, analytics_data: List[StreamerAnalyticsData]):
        """Store analytics data in database."""
        try:
            # Prepare batch insert data
            values = []
            for data in analytics_data:
                values.append((
                    data.streamer_id,
                    data.recorded_at,
                    data.viewers,
                    data.running,
                    data.assigned,
                    data.status
                ))

            # Batch insert using async transaction
            query = """
                INSERT INTO streamer_analytics (streamer_id, recorded_at, viewers, running, assigned, status)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (streamer_id, recorded_at) DO UPDATE SET
                    viewers = EXCLUDED.viewers,
                    running = EXCLUDED.running,
                    assigned = EXCLUDED.assigned,
                    status = EXCLUDED.status
            """

            async with self.database_service.transaction() as conn:
                for value_set in values:
                    await conn.execute(query, *value_set)

            logger.debug(f"Stored analytics data for {len(analytics_data)} streamers")

        except Exception as e:
            self._database_errors += 1
            logger.error(f"Failed to store analytics data: {e}")
            raise

    async def get_analytics_summary(self, hours: int = 24) -> Dict[str, any]:
        """Get analytics collection summary for the last N hours."""
        if not self._start_time:
            return {"error": "Service not started"}

        uptime = datetime.now(timezone.utc) - self._start_time

        # Get recent analytics data count
        since_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        try:
            async with self.database_service.transaction() as conn:
                result = await conn.fetch(
                    "SELECT COUNT(*) as count FROM streamer_analytics WHERE recorded_at > $1",
                    since_time
                )
                recent_records = result[0]['count'] if result else 0
        except Exception as e:
            logger.error(f"Failed to get analytics summary: {e}")
            recent_records = 0

        return {
            "service_status": "running" if self._is_running else "stopped",
            "uptime_seconds": int(uptime.total_seconds()),
            "collection_interval": self.collection_interval,
            "total_collections": self._total_collections,
            "successful_collections": self._successful_collections,
            "failed_collections": self._failed_collections,
            "database_errors": self._database_errors,
            "success_rate": (
                self._successful_collections / self._total_collections * 100
                if self._total_collections > 0 else 0
            ),
            "active_sessions": len(self._active_sessions),
            f"records_last_{hours}h": recent_records
        }

    async def cleanup_old_data(self, days_to_keep: int = 30):
        """Clean up old analytics data to prevent database bloat."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)

        try:
            async with self.database_service.transaction() as conn:
                # Clean old streamer_analytics data
                result = await conn.execute(
                    "DELETE FROM streamer_analytics WHERE recorded_at < $1",
                    cutoff_date
                )
                analytics_deleted = result

                # Clean old completed stream_sessions
                await conn.execute(
                    "DELETE FROM stream_sessions WHERE session_end < $1",
                    cutoff_date
                )

            logger.info(f"Cleaned up analytics data older than {days_to_keep} days")

        except Exception as e:
            logger.error(f"Failed to cleanup old analytics data: {e}")
            raise