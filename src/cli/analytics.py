"""
CLI analytics commands for Kick Streamer Status Monitor.

Provides commands for viewing analytics data, managing collection,
and exporting analytics reports.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

from services import DatabaseService, DatabaseConfig, KickOAuthService, OAuthConfig
from services.analytics import AnalyticsService
from lib.logging import get_logger


class AnalyticsCommandError(Exception):
    """Analytics command specific errors."""
    pass


class AnalyticsCommands:
    """Analytics management CLI commands."""

    def __init__(self):
        self.logger = get_logger(__name__)

    async def status(self, args: argparse.Namespace) -> int:
        """Show analytics collection status and statistics."""
        try:
            # Load configuration
            db_config, oauth_config = await self._load_configs(args)

            # Connect to services
            db_service = DatabaseService(db_config)
            oauth_service = KickOAuthService(oauth_config)

            # Create analytics service for status info
            analytics_service = AnalyticsService(db_service, oauth_service)

            await db_service.connect()
            await oauth_service.start()

            # Get analytics summary
            summary = await analytics_service.get_analytics_summary(hours=24)

            # Get database statistics
            db_stats = await self._get_database_stats(db_service)

            output_format = getattr(args, 'format', 'table')

            if output_format == 'json':
                output_data = {
                    'analytics_service': summary,
                    'database_stats': db_stats
                }
                print(json.dumps(output_data, indent=2, default=str))
            else:
                self._output_analytics_status_table(summary, db_stats)

            # OAuth service doesn't have stop method
            await db_service.disconnect()
            return 0

        except Exception as e:
            self.logger.error(f"Analytics status check failed: {e}")
            print(f"Error: {e}")
            return 1

    async def query(self, args: argparse.Namespace) -> int:
        """Query analytics data with filters."""
        try:
            # Parse arguments
            streamer = getattr(args, 'streamer', None)
            hours = getattr(args, 'hours', 24)
            limit = getattr(args, 'limit', 100)
            output_format = getattr(args, 'format', 'table')

            # Load configuration
            db_config, oauth_config = await self._load_configs(args)

            # Connect to database
            db_service = DatabaseService(db_config)
            await db_service.connect()

            # Build query
            since_time = datetime.now(timezone.utc) - timedelta(hours=hours)

            async with db_service.transaction() as conn:
                if streamer:
                    # Get specific streamer analytics
                    query = """
                        SELECT sa.*, s.username, s.display_name
                        FROM streamer_analytics sa
                        JOIN streamer s ON sa.streamer_id = s.id
                        WHERE s.username = $1 AND sa.recorded_at > $2
                        ORDER BY sa.recorded_at DESC
                        LIMIT $3
                    """
                    results = await conn.fetch(query, streamer, since_time, limit)
                else:
                    # Get all streamers analytics
                    query = """
                        SELECT sa.*, s.username, s.display_name
                        FROM streamer_analytics sa
                        JOIN streamer s ON sa.streamer_id = s.id
                        WHERE sa.recorded_at > $1
                        ORDER BY sa.recorded_at DESC
                        LIMIT $2
                    """
                    results = await conn.fetch(query, since_time, limit)

            if output_format == 'json':
                print(json.dumps([dict(row) for row in results], indent=2, default=str))
            elif output_format == 'csv':
                self._output_analytics_csv(results)
            else:
                self._output_analytics_table(results, streamer, hours)

            await db_service.disconnect()
            return 0

        except Exception as e:
            self.logger.error(f"Analytics query failed: {e}")
            print(f"Error: {e}")
            return 1

    async def sessions(self, args: argparse.Namespace) -> int:
        """Show stream session analytics."""
        try:
            # Parse arguments
            streamer = getattr(args, 'streamer', None)
            days = getattr(args, 'days', 7)
            limit = getattr(args, 'limit', 50)
            output_format = getattr(args, 'format', 'table')
            active_only = getattr(args, 'active', False)

            # Load configuration
            db_config, oauth_config = await self._load_configs(args)

            # Connect to database
            db_service = DatabaseService(db_config)
            await db_service.connect()

            # Build query
            since_time = datetime.now(timezone.utc) - timedelta(days=days)

            if active_only:
                # Only active sessions
                where_clause = "WHERE ss.session_end IS NULL"
                time_params = []
            else:
                # All sessions in time range
                where_clause = "WHERE ss.session_start > $2"
                time_params = [since_time]

            if streamer:
                query = f"""
                    SELECT ss.*, s.username, s.display_name
                    FROM stream_sessions ss
                    JOIN streamer s ON ss.streamer_id = s.id
                    WHERE s.username = $1 {"AND ss.session_start > $3" if not active_only else ""}
                    ORDER BY ss.session_start DESC
                    LIMIT ${3 if not active_only else 2}
                """
                params = [streamer] + time_params + [limit]
            else:
                query = f"""
                    SELECT ss.*, s.username, s.display_name
                    FROM stream_sessions ss
                    JOIN streamer s ON ss.streamer_id = s.id
                    {where_clause}
                    ORDER BY ss.session_start DESC
                    LIMIT ${len(time_params) + 1}
                """
                params = time_params + [limit]

            results = await db_service.execute_query(query, *params)

            if output_format == 'json':
                print(json.dumps([dict(row) for row in results], indent=2, default=str))
            elif output_format == 'csv':
                self._output_sessions_csv(results)
            else:
                self._output_sessions_table(results, streamer, days, active_only)

            await db_service.disconnect()
            return 0

        except Exception as e:
            self.logger.error(f"Sessions query failed: {e}")
            print(f"Error: {e}")
            return 1

    async def export(self, args: argparse.Namespace) -> int:
        """Export analytics data to file."""
        try:
            # Parse arguments
            output_file = getattr(args, 'output', 'analytics_export.json')
            streamer = getattr(args, 'streamer', None)
            days = getattr(args, 'days', 30)
            format_type = getattr(args, 'format', 'json')

            # Load configuration
            db_config, oauth_config = await self._load_configs(args)

            # Connect to database
            db_service = DatabaseService(db_config)
            await db_service.connect()

            print(f"Exporting analytics data (last {days} days)...")

            # Export analytics data
            since_time = datetime.now(timezone.utc) - timedelta(days=days)

            if streamer:
                analytics_query = """
                    SELECT sa.*, s.username, s.display_name
                    FROM streamer_analytics sa
                    JOIN streamer s ON sa.streamer_id = s.id
                    WHERE s.username = $1 AND sa.recorded_at > $2
                    ORDER BY sa.recorded_at DESC
                """
                analytics_data = await db_service.execute_query(analytics_query, streamer, since_time)

                sessions_query = """
                    SELECT ss.*, s.username, s.display_name
                    FROM stream_sessions ss
                    JOIN streamer s ON ss.streamer_id = s.id
                    WHERE s.username = $1 AND ss.session_start > $2
                    ORDER BY ss.session_start DESC
                """
                sessions_data = await db_service.execute_query(sessions_query, streamer, since_time)
            else:
                analytics_query = """
                    SELECT sa.*, s.username, s.display_name
                    FROM streamer_analytics sa
                    JOIN streamer s ON sa.streamer_id = s.id
                    WHERE sa.recorded_at > $1
                    ORDER BY sa.recorded_at DESC
                """
                analytics_data = await db_service.execute_query(analytics_query, since_time)

                sessions_query = """
                    SELECT ss.*, s.username, s.display_name
                    FROM stream_sessions ss
                    JOIN streamer s ON ss.streamer_id = s.id
                    WHERE ss.session_start > $1
                    ORDER BY ss.session_start DESC
                """
                sessions_data = await db_service.execute_query(sessions_query, since_time)

            # Prepare export data
            export_data = {
                'export_timestamp': datetime.now(timezone.utc).isoformat(),
                'export_params': {
                    'streamer': streamer,
                    'days': days,
                    'since': since_time.isoformat()
                },
                'analytics_records': len(analytics_data),
                'session_records': len(sessions_data),
                'analytics_data': [dict(row) for row in analytics_data],
                'sessions_data': [dict(row) for row in sessions_data]
            }

            # Write to file
            output_path = Path(output_file)
            if format_type == 'json':
                with open(output_path, 'w') as f:
                    json.dump(export_data, f, indent=2, default=str)
            else:
                print(f"Error: Unsupported export format: {format_type}")
                return 1

            print(f"✓ Exported {len(analytics_data)} analytics records and {len(sessions_data)} session records to {output_path}")

            await db_service.disconnect()
            return 0

        except Exception as e:
            self.logger.error(f"Export failed: {e}")
            print(f"Error: {e}")
            return 1

    async def cleanup(self, args: argparse.Namespace) -> int:
        """Clean up old analytics data."""
        try:
            # Parse arguments
            days = getattr(args, 'days', 30)
            dry_run = getattr(args, 'dry_run', False)

            # Load configuration
            db_config, oauth_config = await self._load_configs(args)

            # Connect to services
            db_service = DatabaseService(db_config)
            oauth_service = KickOAuthService(oauth_config)
            analytics_service = AnalyticsService(db_service, oauth_service)

            await db_service.connect()

            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            print(f"Cleaning up analytics data older than {days} days (before {cutoff_date.isoformat()})")

            if dry_run:
                # Show what would be deleted
                analytics_count = await db_service.execute_query(
                    "SELECT COUNT(*) as count FROM streamer_analytics WHERE recorded_at < $1",
                    cutoff_date
                )
                sessions_count = await db_service.execute_query(
                    "SELECT COUNT(*) as count FROM stream_sessions WHERE session_end < $1",
                    cutoff_date
                )

                print(f"Would delete:")
                print(f"- {analytics_count[0]['count']} analytics records")
                print(f"- {sessions_count[0]['count']} completed session records")
                print("Run without --dry-run to perform actual cleanup")
            else:
                # Perform actual cleanup
                await analytics_service.cleanup_old_data(days_to_keep=days)
                print("✓ Cleanup completed")

            await db_service.disconnect()
            return 0

        except Exception as e:
            self.logger.error(f"Cleanup failed: {e}")
            print(f"Error: {e}")
            return 1

    async def _load_configs(self, args) -> tuple:
        """Load database and OAuth configurations."""
        # Load .env file if available
        if DOTENV_AVAILABLE:
            env_file = Path('.env')
            if not env_file.exists():
                env_file = Path('../.env')
            if env_file.exists():
                load_dotenv(env_file)

        # Load configurations from environment
        db_config = DatabaseConfig(
            host=os.getenv('DATABASE_HOST', 'localhost'),
            port=int(os.getenv('DATABASE_PORT', 5432)),
            database=os.getenv('DATABASE_NAME', 'kick_mon'),
            username=os.getenv('DATABASE_USER', 'postgres'),
            password=os.getenv('DATABASE_PASSWORD', '')
        )

        oauth_config = OAuthConfig(
            client_id=os.getenv('KICK_CLIENT_ID', ''),
            client_secret=os.getenv('KICK_CLIENT_SECRET', ''),
            api_base_url=os.getenv('KICK_API_BASE_URL', 'https://kick.com/api/v1')
        )

        return db_config, oauth_config

    async def _get_database_stats(self, db_service: DatabaseService) -> Dict[str, Any]:
        """Get analytics database statistics."""
        try:
            async with db_service.transaction() as conn:
                # Analytics table stats
                analytics_total = await conn.fetch(
                    "SELECT COUNT(*) as count FROM streamer_analytics"
                )

                analytics_24h = await conn.fetch(
                    "SELECT COUNT(*) as count FROM streamer_analytics WHERE recorded_at > $1",
                    datetime.now(timezone.utc) - timedelta(hours=24)
                )

                # Sessions stats
                sessions_total = await conn.fetch(
                    "SELECT COUNT(*) as count FROM stream_sessions"
                )

                active_sessions = await conn.fetch(
                    "SELECT COUNT(*) as count FROM stream_sessions WHERE session_end IS NULL"
                )

                # Latest data timestamp
                latest_data = await conn.fetch(
                    "SELECT MAX(recorded_at) as latest FROM streamer_analytics"
                )

                return {
                    'analytics_total_records': analytics_total[0]['count'],
                    'analytics_24h_records': analytics_24h[0]['count'],
                    'sessions_total': sessions_total[0]['count'],
                    'active_sessions': active_sessions[0]['count'],
                    'latest_data_timestamp': latest_data[0]['latest']
                }

        except Exception as e:
            self.logger.error(f"Failed to get database stats: {e}")
            return {}

    def _output_analytics_status_table(self, summary: Dict[str, Any], db_stats: Dict[str, Any]):
        """Output analytics status in table format."""
        print("\nAnalytics Service Status")
        print("=" * 50)
        print(f"Service Status: {summary.get('service_status', 'unknown')}")
        print(f"Collection Interval: {summary.get('collection_interval', 0)}s")
        print(f"Success Rate: {summary.get('success_rate', 0):.1f}%")
        print(f"Active Sessions: {summary.get('active_sessions', 0)}")

        if summary.get('uptime_seconds', 0) > 0:
            uptime = summary['uptime_seconds']
            hours = uptime // 3600
            minutes = (uptime % 3600) // 60
            print(f"Uptime: {hours}h {minutes}m")

        print("\nDatabase Statistics")
        print("-" * 30)
        print(f"Total Analytics Records: {db_stats.get('analytics_total_records', 0):,}")
        print(f"Records (24h): {db_stats.get('analytics_24h_records', 0):,}")
        print(f"Total Sessions: {db_stats.get('sessions_total', 0):,}")
        print(f"Active Sessions: {db_stats.get('active_sessions', 0)}")

        latest = db_stats.get('latest_data_timestamp')
        if latest:
            print(f"Latest Data: {latest}")

    def _output_analytics_table(self, results: List, streamer: Optional[str], hours: int):
        """Output analytics data in table format."""
        if not results:
            print("No analytics data found")
            return

        print(f"\nAnalytics Data - Last {hours} hours" + (f" for {streamer}" if streamer else ""))
        print("=" * 80)
        print(f"{'Time':<19} {'Streamer':<20} {'Viewers':<8} {'Running':<8} {'Assigned':<9} {'Status':<8}")
        print("-" * 80)

        for row in results:
            recorded_time = row['recorded_at'].strftime('%m-%d %H:%M')
            username = row['username'][:19]
            viewers = row['viewers']
            running = 'Yes' if row['running'] else 'No'
            assigned = row['assigned']
            status = row['status']

            print(f"{recorded_time:<19} {username:<20} {viewers:<8} {running:<8} {assigned:<9} {status:<8}")

    def _output_sessions_table(self, results: List, streamer: Optional[str], days: int, active_only: bool):
        """Output session data in table format."""
        if not results:
            print("No session data found")
            return

        title = "Active Sessions" if active_only else f"Stream Sessions - Last {days} days"
        if streamer:
            title += f" for {streamer}"

        print(f"\n{title}")
        print("=" * 90)
        print(f"{'Streamer':<20} {'Start Time':<16} {'Duration':<10} {'Peak':<6} {'Avg':<6} {'Status':<8}")
        print("-" * 90)

        for row in results:
            username = row['username'][:19]
            start_time = row['session_start'].strftime('%m-%d %H:%M')

            if row['session_end']:
                duration = f"{row['total_minutes'] or 0}m"
                status = "Ended"
            else:
                # Calculate current duration for active sessions
                now = datetime.now(timezone.utc)
                duration_mins = int((now - row['session_start']).total_seconds() / 60)
                duration = f"{duration_mins}m"
                status = "Live"

            peak = row['peak_viewers'] or 0
            avg = row['avg_viewers'] or 0

            print(f"{username:<20} {start_time:<16} {duration:<10} {peak:<6} {avg:<6} {status:<8}")

    def _output_analytics_csv(self, results: List):
        """Output analytics data in CSV format."""
        if not results:
            return

        # CSV header
        print("recorded_at,streamer,viewers,running,assigned,status")

        for row in results:
            recorded_at = row['recorded_at'].isoformat()
            streamer = row['username']
            viewers = row['viewers']
            running = 1 if row['running'] else 0
            assigned = row['assigned']
            status = row['status']

            print(f"{recorded_at},{streamer},{viewers},{running},{assigned},{status}")

    def _output_sessions_csv(self, results: List):
        """Output session data in CSV format."""
        if not results:
            return

        # CSV header
        print("streamer,session_start,session_end,duration_minutes,peak_viewers,avg_viewers,status")

        for row in results:
            streamer = row['username']
            session_start = row['session_start'].isoformat()
            session_end = row['session_end'].isoformat() if row['session_end'] else ""
            duration = row['total_minutes'] or 0
            peak = row['peak_viewers'] or 0
            avg = row['avg_viewers'] or 0
            status = "ended" if row['session_end'] else "active"

            print(f"{streamer},{session_start},{session_end},{duration},{peak},{avg},{status}")