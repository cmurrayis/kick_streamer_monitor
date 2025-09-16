"""
Snags database service for worker assignment data integration.

Connects to the external 'snags' database to retrieve worker assignment
information for streamers. This data is used to calculate "Assigned Viewers"
and "Humans" metrics in the dashboard.
"""

import asyncio
import asyncpg
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List, Any
import os

logger = logging.getLogger(__name__)


class SnagsDatabaseService:
    """
    Service for connecting to the external snags database and retrieving worker assignment data.

    The snags database contains a 'worker' table with assignments of viewers/workers to streamers.
    """

    def __init__(self):
        self._connection_pool: Optional[asyncpg.Pool] = None
        self._is_connected = False

        # Database configuration (uses same credentials as main database)
        self._db_config = {
            'host': os.getenv('DATABASE_HOST', 'localhost'),
            'port': int(os.getenv('DATABASE_PORT', 5432)),
            'user': os.getenv('DATABASE_USER'),
            'password': os.getenv('DATABASE_PASSWORD'),
            'database': 'snags'  # Different database name
        }

        # Validation
        if not self._db_config['user'] or not self._db_config['password']:
            raise ValueError("DATABASE_USER and DATABASE_PASSWORD must be set for snags database connection")

    async def connect(self) -> None:
        """Establish connection pool to snags database."""
        if self._is_connected:
            logger.debug("Snags database already connected")
            return

        try:
            logger.info(f"Connecting to snags database at {self._db_config['host']}:{self._db_config['port']}")

            # Create connection pool
            self._connection_pool = await asyncpg.create_pool(
                **self._db_config,
                min_size=1,
                max_size=5,
                command_timeout=30
            )

            # Test connection
            async with self._connection_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

            self._is_connected = True
            logger.info("Successfully connected to snags database")

        except Exception as e:
            logger.error(f"Failed to connect to snags database: {e}")
            if self._connection_pool:
                await self._connection_pool.close()
                self._connection_pool = None
            raise

    async def disconnect(self) -> None:
        """Close database connection pool."""
        if not self._is_connected:
            return

        try:
            if self._connection_pool:
                await self._connection_pool.close()
                self._connection_pool = None

            self._is_connected = False
            logger.info("Disconnected from snags database")

        except Exception as e:
            logger.error(f"Error disconnecting from snags database: {e}")

    async def get_assigned_viewers_for_streamer(self, username: str) -> int:
        """
        Get total assigned viewers/workers for a specific streamer.

        Args:
            username: Streamer username (target in worker table)

        Returns:
            Total number of assigned workers/viewers for this streamer
        """
        if not self._is_connected:
            await self.connect()

        try:
            async with self._connection_pool.acquire() as conn:
                query = """
                SELECT SUM(
                    CASE
                        WHEN wrk_count ~ '^[0-9]+$' THEN wrk_count::integer
                        ELSE 0
                    END
                ) as total_assigned
                FROM worker
                WHERE target = $1 AND target IS NOT NULL
                """

                result = await conn.fetchval(query, username)
                return result or 0

        except Exception as e:
            logger.error(f"Error getting assigned viewers for {username}: {e}")
            return 0

    async def get_assigned_viewers_for_multiple_streamers(self, usernames: List[str]) -> Dict[str, int]:
        """
        Get assigned viewers for multiple streamers efficiently.

        Args:
            usernames: List of streamer usernames

        Returns:
            Dictionary mapping username to assigned viewer count
        """
        if not self._is_connected:
            await self.connect()

        if not usernames:
            return {}

        try:
            async with self._connection_pool.acquire() as conn:
                query = """
                SELECT
                    target,
                    SUM(
                        CASE
                            WHEN wrk_count ~ '^[0-9]+$' THEN wrk_count::integer
                            ELSE 0
                        END
                    ) as total_assigned
                FROM worker
                WHERE target = ANY($1) AND target IS NOT NULL
                GROUP BY target
                """

                rows = await conn.fetch(query, usernames)

                # Create result dictionary with all usernames (default to 0)
                result = {username: 0 for username in usernames}

                # Update with actual values
                for row in rows:
                    result[row['target']] = row['total_assigned'] or 0

                return result

        except Exception as e:
            logger.error(f"Error getting assigned viewers for multiple streamers: {e}")
            return {username: 0 for username in usernames}

    async def get_all_worker_assignments(self) -> List[Dict[str, Any]]:
        """
        Get all current worker assignments for analytics.

        Returns:
            List of worker assignment records with metadata
        """
        if not self._is_connected:
            await self.connect()

        try:
            async with self._connection_pool.acquire() as conn:
                query = """
                SELECT
                    id,
                    target,
                    CASE
                        WHEN wrk_count ~ '^[0-9]+$' THEN wrk_count::integer
                        ELSE 0
                    END as assigned_count,
                    hostname,
                    str_status as stream_status,
                    wrk_status as worker_status,
                    updated_at,
                    service,
                    failure_count
                FROM worker
                WHERE target IS NOT NULL AND target != '' AND target != 'none'
                ORDER BY target, updated_at DESC
                """

                rows = await conn.fetch(query)

                return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting all worker assignments: {e}")
            return []

    async def get_worker_assignment_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics of worker assignments.

        Returns:
            Summary statistics including total workers, unique targets, etc.
        """
        if not self._is_connected:
            await self.connect()

        try:
            async with self._connection_pool.acquire() as conn:
                summary_query = """
                SELECT
                    COUNT(*) as total_worker_records,
                    COUNT(DISTINCT target) as unique_targets,
                    SUM(
                        CASE
                            WHEN wrk_count ~ '^[0-9]+$' THEN wrk_count::integer
                            ELSE 0
                        END
                    ) as total_assigned_viewers,
                    COUNT(CASE WHEN str_status = 'online' THEN 1 END) as online_targets,
                    COUNT(CASE WHEN str_status = 'offline' THEN 1 END) as offline_targets
                FROM worker
                WHERE target IS NOT NULL AND target != '' AND target != 'none'
                """

                summary_row = await conn.fetchrow(summary_query)

                # Get top targets
                top_targets_query = """
                SELECT
                    target,
                    COUNT(*) as worker_count,
                    SUM(
                        CASE
                            WHEN wrk_count ~ '^[0-9]+$' THEN wrk_count::integer
                            ELSE 0
                        END
                    ) as total_assigned
                FROM worker
                WHERE target IS NOT NULL AND target != '' AND target != 'none'
                GROUP BY target
                ORDER BY total_assigned DESC
                LIMIT 10
                """

                top_targets = await conn.fetch(top_targets_query)

                return {
                    'total_worker_records': summary_row['total_worker_records'],
                    'unique_targets': summary_row['unique_targets'],
                    'total_assigned_viewers': summary_row['total_assigned_viewers'],
                    'online_targets': summary_row['online_targets'],
                    'offline_targets': summary_row['offline_targets'],
                    'top_targets': [dict(row) for row in top_targets],
                    'last_updated': datetime.now(timezone.utc)
                }

        except Exception as e:
            logger.error(f"Error getting worker assignment summary: {e}")
            return {
                'total_worker_records': 0,
                'unique_targets': 0,
                'total_assigned_viewers': 0,
                'online_targets': 0,
                'offline_targets': 0,
                'top_targets': [],
                'last_updated': datetime.now(timezone.utc),
                'error': str(e)
            }

    async def health_check(self) -> bool:
        """
        Perform health check on snags database connection.

        Returns:
            True if connection is healthy, False otherwise
        """
        try:
            if not self._is_connected:
                await self.connect()

            async with self._connection_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                return True

        except Exception as e:
            logger.error(f"Snags database health check failed: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        """Check if service is connected to database."""
        return self._is_connected

    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            'is_connected': self._is_connected,
            'database': self._db_config['database'],
            'host': self._db_config['host'],
            'port': self._db_config['port']
        }

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()