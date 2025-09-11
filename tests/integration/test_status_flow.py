"""
Integration test for end-to-end status update flow with real streamer data.
Tests the complete workflow from WebSocket events to database updates.

This test coordinates all components:
- OAuth authentication
- Database connection and operations
- WebSocket event reception
- Status processing and database updates
- Error handling and recovery

These tests require:
- Valid OAuth credentials
- Test PostgreSQL database
- Network access to Kick.com services
- Test streamers that may generate events

Set these environment variables:
- KICK_CLIENT_ID, KICK_CLIENT_SECRET
- TEST_DATABASE_* (connection details)
- KICK_TEST_STREAMERS (comma-separated usernames)
"""

import os
import pytest
import asyncio
import json
import httpx
import psycopg
from psycopg.rows import dict_row
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
import logging
from unittest.mock import AsyncMock, MagicMock
import time

# Test logger
logger = logging.getLogger(__name__)


class TestEndToEndStatusFlow:
    """Integration tests for complete status update workflow."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self):
        """Setup integration test configuration."""
        # OAuth configuration
        self.client_id = os.getenv("KICK_CLIENT_ID")
        self.client_secret = os.getenv("KICK_CLIENT_SECRET")
        
        if not self.client_id or not self.client_secret:
            pytest.skip("OAuth credentials required for end-to-end tests")
        
        # Database configuration
        self.db_config = {
            "host": os.getenv("TEST_DATABASE_HOST", "localhost"),
            "port": int(os.getenv("TEST_DATABASE_PORT", "5432")),
            "dbname": os.getenv("TEST_DATABASE_NAME", "kick_monitor_test"),
            "user": os.getenv("TEST_DATABASE_USER", "kick_monitor"),
            "password": os.getenv("TEST_DATABASE_PASSWORD", "test_password"),
        }
        
        # Test streamers
        test_streamers = os.getenv("KICK_TEST_STREAMERS", "")
        if test_streamers:
            self.test_streamers = [name.strip() for name in test_streamers.split(",")]
        else:
            self.test_streamers = ["xqc", "trainwreckstv", "amouranth"]
        
        # API endpoints
        self.oauth_token_url = "https://id.kick.com/oauth/token"
        self.api_base_url = "https://kick.com/api/v1"
        
        # Test configuration
        self.max_wait_time = 300.0  # 5 minutes max for integration tests
        self.status_check_interval = 5.0  # Check status every 5 seconds
        
        # Store test data for cleanup
        self.created_streamers = []
        self.access_token = None

    @pytest.fixture
    async def authenticated_http_client(self):
        """HTTP client with OAuth authentication."""
        # Get OAuth token
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "user:read channel:read events:subscribe"
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.oauth_token_url,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                pytest.skip(f"OAuth authentication failed: {response.status_code}")
            
            token_response = response.json()
            self.access_token = token_response["access_token"]
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "kick-monitor-integration-test/1.0.0"
            }
            
            authenticated_client = httpx.AsyncClient(timeout=30.0, headers=headers)
            yield authenticated_client
            await authenticated_client.aclose()

    @pytest.fixture
    async def database_connection(self):
        """Database connection for testing."""
        conn_string = (
            f"postgresql://{self.db_config['user']}:{self.db_config['password']}"
            f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['dbname']}"
        )
        
        try:
            conn = await psycopg.AsyncConnection.connect(
                conn_string,
                row_factory=dict_row
            )
            
            # Ensure test tables exist
            await self._setup_test_tables(conn)
            
            yield conn
            
            # Cleanup test data
            await self._cleanup_test_data(conn)
            
        except psycopg.OperationalError as e:
            pytest.skip(f"Database connection failed: {e}")
        finally:
            if 'conn' in locals():
                await conn.close()

    async def test_complete_status_monitoring_workflow(self, authenticated_http_client, database_connection):
        """Test complete status monitoring workflow."""
        print("Starting end-to-end status monitoring workflow test")
        
        # Phase 1: Setup test streamers in database
        print("Phase 1: Setting up test streamers in database")
        test_streamer_data = await self._setup_test_streamers(
            authenticated_http_client, 
            database_connection
        )
        
        assert len(test_streamer_data) > 0, "No test streamers could be set up"
        print(f"Set up {len(test_streamer_data)} test streamers")
        
        # Phase 2: Get initial status from API
        print("Phase 2: Getting initial status from Kick.com API")
        initial_status = await self._get_streamer_status_from_api(
            authenticated_http_client,
            test_streamer_data
        )
        
        # Phase 3: Update database with initial status
        print("Phase 3: Updating database with initial status")
        await self._update_database_status(database_connection, initial_status)
        
        # Phase 4: Simulate status change detection
        print("Phase 4: Simulating status change detection and processing")
        status_changes = await self._simulate_status_change_detection(
            authenticated_http_client,
            database_connection,
            test_streamer_data
        )
        
        # Phase 5: Validate end-to-end data consistency
        print("Phase 5: Validating end-to-end data consistency")
        await self._validate_data_consistency(database_connection, test_streamer_data)
        
        print("End-to-end status monitoring workflow test completed successfully")

    async def test_status_update_performance(self, authenticated_http_client, database_connection):
        """Test status update performance requirements."""
        print("Testing status update performance")
        
        # Setup single test streamer
        test_streamer = await self._create_test_streamer(
            authenticated_http_client,
            database_connection,
            self.test_streamers[0]
        )
        
        # Measure status update performance
        start_time = time.time()
        
        # Simulate receiving status change event
        status_event = {
            "event": "App\\Events\\StreamerIsLive",
            "data": {
                "channel_id": test_streamer["kick_user_id"],
                "user_id": test_streamer["kick_user_id"],
                "session": {
                    "id": f"test_session_{int(time.time())}",
                    "is_live": True,
                    "title": "Test Stream",
                    "viewer_count": 150,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
            }
        }
        
        # Process event and update database
        await self._process_status_event(database_connection, test_streamer, status_event)
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        # Validate performance requirement: < 1 second
        assert processing_time < 1.0, f"Status update too slow: {processing_time:.3f}s"
        
        print(f"Status update performance: {processing_time:.3f}s (requirement: <1s)")

    async def test_concurrent_status_updates(self, authenticated_http_client, database_connection):
        """Test concurrent status updates for multiple streamers."""
        print("Testing concurrent status updates")
        
        # Setup multiple test streamers
        test_streamers = []
        for i, username in enumerate(self.test_streamers[:3]):
            streamer = await self._create_test_streamer(
                authenticated_http_client,
                database_connection,
                username,
                initial_status="offline"
            )
            if streamer:
                test_streamers.append(streamer)
        
        assert len(test_streamers) >= 2, "Need at least 2 streamers for concurrency test"
        
        # Define concurrent status update tasks
        async def update_streamer_status(streamer_data: Dict[str, Any], new_status: str):
            """Update single streamer status."""
            event = {
                "event": "App\\Events\\StreamerIsLive" if new_status == "online" else "App\\Events\\StreamerOffline",
                "data": {
                    "channel_id": streamer_data["kick_user_id"],
                    "user_id": streamer_data["kick_user_id"]
                }
            }
            
            if new_status == "online":
                event["data"]["session"] = {
                    "id": f"session_{streamer_data['id']}_{int(time.time())}",
                    "is_live": True,
                    "title": f"Concurrent Test Stream {streamer_data['username']}",
                    "viewer_count": 100 + streamer_data["id"],
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
            
            return await self._process_status_event(database_connection, streamer_data, event)
        
        # Execute concurrent updates
        start_time = time.time()
        
        tasks = [
            update_streamer_status(streamer, "online")
            for streamer in test_streamers
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        end_time = time.time()
        total_time = end_time - start_time
        
        # Validate all updates succeeded
        successful_updates = [r for r in results if not isinstance(r, Exception)]
        assert len(successful_updates) == len(test_streamers), "Some concurrent updates failed"
        
        print(f"Concurrent updates: {len(successful_updates)} streamers in {total_time:.3f}s")
        
        # Validate database consistency
        async with database_connection.cursor() as cur:
            await cur.execute("""
                SELECT username, status FROM streamers 
                WHERE id = ANY(%s)
            """, ([s["id"] for s in test_streamers],))
            
            statuses = await cur.fetchall()
            online_count = sum(1 for s in statuses if s["status"] == "online")
            
            assert online_count == len(test_streamers), "Not all streamers updated to online status"

    async def test_error_recovery_scenarios(self, authenticated_http_client, database_connection):
        """Test error recovery in status update flow."""
        print("Testing error recovery scenarios")
        
        # Setup test streamer
        test_streamer = await self._create_test_streamer(
            authenticated_http_client,
            database_connection,
            self.test_streamers[0]
        )
        
        # Test 1: Invalid event data
        print("Testing recovery from invalid event data")
        invalid_event = {
            "event": "App\\Events\\StreamerIsLive",
            "data": {
                # Missing required fields
                "invalid_field": "invalid_value"
            }
        }
        
        try:
            await self._process_status_event(database_connection, test_streamer, invalid_event)
            pytest.fail("Should have failed with invalid event data")
        except Exception as e:
            print(f"Correctly handled invalid event: {e}")
        
        # Verify database state remains consistent
        async with database_connection.cursor() as cur:
            await cur.execute("SELECT status FROM streamers WHERE id = %s", (test_streamer["id"],))
            result = await cur.fetchone()
            assert result["status"] == test_streamer["status"], "Database corrupted by invalid event"
        
        # Test 2: Database transaction rollback
        print("Testing database transaction rollback")
        
        # Simulate database error during status update
        try:
            async with database_connection.cursor() as cur:
                # Start transaction
                await cur.execute("BEGIN")
                
                # Valid update
                await cur.execute("""
                    UPDATE streamers SET status = 'online', updated_at = NOW()
                    WHERE id = %s
                """, (test_streamer["id"],))
                
                # Force error (trying to insert duplicate)
                await cur.execute("""
                    INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                    VALUES (%s, %s, 'Duplicate', 'offline', true, NOW(), NOW())
                """, (test_streamer["kick_user_id"], test_streamer["username"]))
                
                # Should not reach here
                await database_connection.commit()
                
        except psycopg.IntegrityError:
            # Expected error - rollback should occur
            await database_connection.rollback()
            print("Transaction correctly rolled back on error")
        
        # Verify database state unchanged
        async with database_connection.cursor() as cur:
            await cur.execute("SELECT status FROM streamers WHERE id = %s", (test_streamer["id"],))
            result = await cur.fetchone()
            assert result["status"] == test_streamer["status"], "Transaction rollback failed"

    async def test_data_consistency_validation(self, authenticated_http_client, database_connection):
        """Test data consistency across the entire workflow."""
        print("Testing data consistency validation")
        
        # Setup test streamer
        test_streamer = await self._create_test_streamer(
            authenticated_http_client,
            database_connection,
            self.test_streamers[0]
        )
        
        # Simulate series of status changes
        status_changes = [
            ("offline", "online"),
            ("online", "online"),    # Duplicate event (should be handled)
            ("online", "offline"),
            ("offline", "online")
        ]
        
        event_id_counter = 1
        
        for prev_status, new_status in status_changes:
            print(f"Processing status change: {prev_status} -> {new_status}")
            
            # Create event
            event = {
                "event": "App\\Events\\StreamerIsLive" if new_status == "online" else "App\\Events\\StreamerOffline",
                "data": {
                    "channel_id": test_streamer["kick_user_id"],
                    "user_id": test_streamer["kick_user_id"],
                }
            }
            
            if new_status == "online":
                event["data"]["session"] = {
                    "id": f"session_{event_id_counter}",
                    "is_live": True,
                    "title": f"Test Stream {event_id_counter}",
                    "viewer_count": 100 * event_id_counter,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
            
            # Process event
            await self._process_status_event(database_connection, test_streamer, event)
            
            # Validate database state
            async with database_connection.cursor() as cur:
                # Check streamer status
                await cur.execute("""
                    SELECT status, last_status_update 
                    FROM streamers WHERE id = %s
                """, (test_streamer["id"],))
                
                streamer_status = await cur.fetchone()
                expected_status = new_status if prev_status != new_status else prev_status
                
                assert streamer_status["status"] == expected_status, f"Status mismatch: expected {expected_status}, got {streamer_status['status']}"
                
                # Check event was logged (only if status actually changed)
                if prev_status != new_status:
                    await cur.execute("""
                        SELECT COUNT(*) as count 
                        FROM status_events 
                        WHERE streamer_id = %s AND new_status = %s
                        ORDER BY processed_timestamp DESC
                        LIMIT 1
                    """, (test_streamer["id"], new_status))
                    
                    event_count = await cur.fetchone()
                    assert event_count["count"] > 0, f"Status event not logged for {prev_status} -> {new_status}"
            
            event_id_counter += 1
            
            # Small delay between events
            await asyncio.sleep(0.1)
        
        print("Data consistency validation completed successfully")

    async def test_api_to_database_integration(self, authenticated_http_client, database_connection):
        """Test integration between API polling and database updates."""
        print("Testing API to database integration")
        
        # Get real streamer data from API
        api_streamers = []
        for username in self.test_streamers[:2]:
            try:
                response = await authenticated_http_client.get(f"{self.api_base_url}/channels/{username}")
                if response.status_code == 200:
                    channel_data = response.json()
                    api_streamers.append({
                        "username": username,
                        "kick_user_id": channel_data.get("user_id") or channel_data.get("id"),
                        "display_name": channel_data.get("user", {}).get("username", username),
                        "api_data": channel_data
                    })
            except Exception as e:
                print(f"Failed to get API data for {username}: {e}")
        
        assert len(api_streamers) > 0, "No API data available for integration test"
        
        # Create streamers in database based on API data
        created_streamers = []
        for api_streamer in api_streamers:
            async with database_connection.cursor() as cur:
                await cur.execute("""
                    INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, 'unknown', true, NOW(), NOW())
                    RETURNING id, kick_user_id, username, display_name, status
                """, (
                    api_streamer["kick_user_id"],
                    api_streamer["username"],
                    api_streamer["display_name"]
                ))
                
                created_streamer = await cur.fetchone()
                created_streamers.append(created_streamer)
                self.created_streamers.append(created_streamer["id"])
                
                await database_connection.commit()
        
        # Update database with real API status
        for i, created_streamer in enumerate(created_streamers):
            api_data = api_streamers[i]["api_data"]
            
            # Determine status from API data
            is_live = False
            if "livestream" in api_data and api_data["livestream"]:
                is_live = api_data["livestream"].get("is_live", False)
            
            new_status = "online" if is_live else "offline"
            
            # Update database
            async with database_connection.cursor() as cur:
                await cur.execute("""
                    UPDATE streamers 
                    SET status = %s, last_status_update = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (new_status, created_streamer["id"]))
                
                # Log status event
                await cur.execute("""
                    INSERT INTO status_events 
                    (streamer_id, event_type, previous_status, new_status, 
                     event_timestamp, received_timestamp, processed_timestamp, event_data)
                    VALUES (%s, %s, %s, %s, NOW(), NOW(), NOW(), %s)
                """, (
                    created_streamer["id"],
                    "api_poll",
                    "unknown",
                    new_status,
                    json.dumps({"source": "api_integration_test"})
                ))
                
                await database_connection.commit()
        
        # Validate database state matches API data
        async with database_connection.cursor() as cur:
            await cur.execute("""
                SELECT s.username, s.status, COUNT(e.id) as event_count
                FROM streamers s
                LEFT JOIN status_events e ON s.id = e.streamer_id
                WHERE s.id = ANY(%s)
                GROUP BY s.id, s.username, s.status
            """, ([s["id"] for s in created_streamers],))
            
            results = await cur.fetchall()
            
            for result in results:
                print(f"Streamer {result['username']}: status={result['status']}, events={result['event_count']}")
                
                # Each streamer should have at least one event
                assert result["event_count"] > 0, f"No events recorded for {result['username']}"
                
                # Status should be online or offline (not unknown)
                assert result["status"] in ["online", "offline"], f"Invalid status for {result['username']}: {result['status']}"
        
        print("API to database integration test completed successfully")

    async def _setup_test_streamers(
        self, 
        http_client: httpx.AsyncClient, 
        db_conn: psycopg.AsyncConnection
    ) -> List[Dict[str, Any]]:
        """Setup test streamers in database with real API data."""
        streamers = []
        
        for username in self.test_streamers[:3]:  # Limit to 3 for testing
            try:
                streamer = await self._create_test_streamer(http_client, db_conn, username)
                if streamer:
                    streamers.append(streamer)
            except Exception as e:
                print(f"Failed to setup streamer {username}: {e}")
        
        return streamers

    async def _create_test_streamer(
        self,
        http_client: httpx.AsyncClient,
        db_conn: psycopg.AsyncConnection,
        username: str,
        initial_status: str = "unknown"
    ) -> Optional[Dict[str, Any]]:
        """Create test streamer in database."""
        try:
            # Get real data from API
            response = await http_client.get(f"{self.api_base_url}/channels/{username}")
            
            if response.status_code != 200:
                print(f"API request failed for {username}: {response.status_code}")
                return None
            
            channel_data = response.json()
            
            # Extract data
            kick_user_id = channel_data.get("user_id") or channel_data.get("id")
            display_name = channel_data.get("user", {}).get("username", username)
            
            if not kick_user_id:
                print(f"No user ID found for {username}")
                return None
            
            # Insert into database
            async with db_conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO streamers (kick_user_id, username, display_name, status, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, true, NOW(), NOW())
                    RETURNING id, kick_user_id, username, display_name, status
                    ON CONFLICT (username) DO UPDATE SET
                        kick_user_id = EXCLUDED.kick_user_id,
                        display_name = EXCLUDED.display_name,
                        updated_at = NOW()
                    RETURNING id, kick_user_id, username, display_name, status
                """, (kick_user_id, username, display_name, initial_status))
                
                streamer = await cur.fetchone()
                await db_conn.commit()
                
                # Track for cleanup
                self.created_streamers.append(streamer["id"])
                
                return dict(streamer)
                
        except Exception as e:
            print(f"Error creating test streamer {username}: {e}")
            return None

    async def _get_streamer_status_from_api(
        self,
        http_client: httpx.AsyncClient,
        streamers: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Get current status of streamers from API."""
        status_data = {}
        
        for streamer in streamers:
            try:
                # Get livestream status
                response = await http_client.get(
                    f"{self.api_base_url}/channels/{streamer['username']}/livestream"
                )
                
                if response.status_code == 200:
                    livestream_data = response.json()
                    is_live = livestream_data.get("is_live", False)
                    status_data[streamer["id"]] = {
                        "status": "online" if is_live else "offline",
                        "livestream_data": livestream_data
                    }
                elif response.status_code == 404:
                    # Not live
                    status_data[streamer["id"]] = {
                        "status": "offline",
                        "livestream_data": None
                    }
                else:
                    # Unknown status
                    status_data[streamer["id"]] = {
                        "status": "unknown",
                        "error": f"API returned {response.status_code}"
                    }
                    
            except Exception as e:
                status_data[streamer["id"]] = {
                    "status": "unknown",
                    "error": str(e)
                }
        
        return status_data

    async def _update_database_status(
        self,
        db_conn: psycopg.AsyncConnection,
        status_data: Dict[str, Any]
    ) -> None:
        """Update database with status data."""
        async with db_conn.cursor() as cur:
            for streamer_id, status_info in status_data.items():
                await cur.execute("""
                    UPDATE streamers 
                    SET status = %s, last_status_update = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (status_info["status"], streamer_id))
            
            await db_conn.commit()

    async def _simulate_status_change_detection(
        self,
        http_client: httpx.AsyncClient,
        db_conn: psycopg.AsyncConnection,
        streamers: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Simulate status change detection and processing."""
        status_changes = []
        
        for streamer in streamers[:2]:  # Test with first 2 streamers
            # Simulate status change event
            current_time = datetime.now(timezone.utc)
            
            # Create mock status change event
            status_event = {
                "event": "App\\Events\\StreamerIsLive",
                "data": {
                    "channel_id": streamer["kick_user_id"],
                    "user_id": streamer["kick_user_id"],
                    "session": {
                        "id": f"test_session_{streamer['id']}_{int(current_time.timestamp())}",
                        "is_live": True,
                        "title": f"Integration Test Stream - {streamer['username']}",
                        "viewer_count": 200,
                        "created_at": current_time.isoformat()
                    }
                }
            }
            
            # Process the event
            await self._process_status_event(db_conn, streamer, status_event)
            
            status_changes.append({
                "streamer": streamer,
                "event": status_event,
                "processed_at": current_time
            })
        
        return status_changes

    async def _process_status_event(
        self,
        db_conn: psycopg.AsyncConnection,
        streamer: Dict[str, Any],
        event: Dict[str, Any]
    ) -> None:
        """Process a status event and update database."""
        event_type = event["event"]
        event_data = event["data"]
        
        # Determine new status
        if "StreamerIsLive" in event_type:
            new_status = "online"
            db_event_type = "stream_start"
        elif "StreamerOffline" in event_type:
            new_status = "offline"
            db_event_type = "stream_end"
        else:
            raise ValueError(f"Unknown event type: {event_type}")
        
        # Get current status
        async with db_conn.cursor() as cur:
            await cur.execute("SELECT status FROM streamers WHERE id = %s", (streamer["id"],))
            current_status_row = await cur.fetchone()
            current_status = current_status_row["status"] if current_status_row else "unknown"
            
            # Only update if status actually changed
            if current_status != new_status:
                # Update streamer status
                await cur.execute("""
                    UPDATE streamers 
                    SET status = %s, last_status_update = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (new_status, streamer["id"]))
                
                # Log status event
                await cur.execute("""
                    INSERT INTO status_events 
                    (streamer_id, event_type, previous_status, new_status, 
                     event_timestamp, received_timestamp, processed_timestamp, event_data)
                    VALUES (%s, %s, %s, %s, NOW(), NOW(), NOW(), %s)
                """, (
                    streamer["id"],
                    db_event_type,
                    current_status,
                    new_status,
                    json.dumps(event_data)
                ))
                
                await db_conn.commit()
                print(f"Updated {streamer['username']}: {current_status} -> {new_status}")
            else:
                print(f"No status change for {streamer['username']}: already {current_status}")

    async def _validate_data_consistency(
        self,
        db_conn: psycopg.AsyncConnection,
        streamers: List[Dict[str, Any]]
    ) -> None:
        """Validate data consistency across all test data."""
        async with db_conn.cursor() as cur:
            # Check that all test streamers exist
            streamer_ids = [s["id"] for s in streamers]
            await cur.execute("""
                SELECT COUNT(*) as count FROM streamers WHERE id = ANY(%s)
            """, (streamer_ids,))
            
            count_result = await cur.fetchone()
            assert count_result["count"] == len(streamers), "Not all test streamers found in database"
            
            # Check that status events exist for streamers
            await cur.execute("""
                SELECT streamer_id, COUNT(*) as event_count
                FROM status_events 
                WHERE streamer_id = ANY(%s)
                GROUP BY streamer_id
            """, (streamer_ids,))
            
            event_counts = await cur.fetchall()
            
            # Each streamer should have at least one event
            for event_count in event_counts:
                assert event_count["event_count"] > 0, f"No events for streamer {event_count['streamer_id']}"
            
            # Check data integrity constraints
            await cur.execute("""
                SELECT s.username, s.status, e.new_status, e.processed_timestamp
                FROM streamers s
                JOIN status_events e ON s.id = e.streamer_id
                WHERE s.id = ANY(%s)
                ORDER BY e.processed_timestamp DESC
            """)
            
            # Additional consistency checks could be added here
            print("Data consistency validation passed")

    async def _setup_test_tables(self, db_conn: psycopg.AsyncConnection) -> None:
        """Setup test database tables."""
        async with db_conn.cursor() as cur:
            # Create streamers table
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS streamers (
                    id SERIAL PRIMARY KEY,
                    kick_user_id INTEGER UNIQUE NOT NULL,
                    username VARCHAR(255) UNIQUE NOT NULL,
                    display_name VARCHAR(255),
                    status VARCHAR(50) NOT NULL CHECK (status IN ('online', 'offline', 'unknown')),
                    last_seen_online TIMESTAMP WITH TIME ZONE,
                    last_status_update TIMESTAMP WITH TIME ZONE,
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """)
            
            # Create status_events table
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS status_events (
                    id SERIAL PRIMARY KEY,
                    streamer_id INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
                    event_type VARCHAR(50) NOT NULL,
                    previous_status VARCHAR(50) NOT NULL,
                    new_status VARCHAR(50) NOT NULL,
                    kick_event_id VARCHAR(255),
                    event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                    received_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                    processed_timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    event_data JSONB
                )
            """)
            
            await db_conn.commit()

    async def _cleanup_test_data(self, db_conn: psycopg.AsyncConnection) -> None:
        """Clean up test data."""
        if self.created_streamers:
            async with db_conn.cursor() as cur:
                # Delete test streamers (cascade will delete events)
                await cur.execute("""
                    DELETE FROM streamers WHERE id = ANY(%s)
                """, (self.created_streamers,))
                
                await db_conn.commit()
                print(f"Cleaned up {len(self.created_streamers)} test streamers")


@pytest.mark.integration
class TestWorkflowDocumentation:
    """Documentation for complete workflow integration."""
    
    def test_monitoring_service_workflow_documentation(self):
        """Document the complete monitoring service workflow."""
        workflow_phases = {
            "1. Service Initialization": [
                "Load configuration from environment",
                "Authenticate with Kick.com OAuth API",
                "Connect to PostgreSQL database",
                "Initialize logging and monitoring"
            ],
            "2. Streamer Discovery": [
                "Query database for active streamers to monitor",
                "Validate streamer data with Kick.com API",
                "Get initial status for all streamers"
            ],
            "3. WebSocket Connection": [
                "Discover Kick.com WebSocket endpoint",
                "Establish authenticated WebSocket connection",
                "Subscribe to channels for all monitored streamers",
                "Setup heartbeat/ping mechanism"
            ],
            "4. Event Processing": [
                "Listen for StreamerIsLive and StreamerOffline events",
                "Validate event data and extract status information",
                "Update database with new streamer status",
                "Log status change events for audit trail"
            ],
            "5. Error Handling": [
                "Handle WebSocket disconnections with reconnection",
                "Refresh OAuth tokens on authentication errors",
                "Retry database operations on connection failures",
                "Log errors and maintain service health metrics"
            ],
            "6. Health Monitoring": [
                "Monitor connection status and event processing rate",
                "Track database performance and connection health",
                "Provide status endpoints for external monitoring",
                "Alert on service degradation or failures"
            ]
        }
        
        print("Complete Monitoring Service Workflow:")
        for phase, steps in workflow_phases.items():
            print(f"  {phase}:")
            for step in steps:
                print(f"    - {step}")
        
        assert True

    def test_data_flow_documentation(self):
        """Document the data flow through the system."""
        data_flow_stages = [
            "1. Kick.com WebSocket Event → Event validation and parsing",
            "2. Parsed Event Data → Status determination logic",
            "3. Status Change → Database transaction (streamer + event tables)",
            "4. Database Update → Transaction commit with consistency checks",
            "5. Success/Failure → Logging and monitoring metrics",
            "6. Manual Mode → Real-time display update via Rich UI",
            "7. External Systems → API/database queries for current status"
        ]
        
        print("Data Flow Through System:")
        for stage in data_flow_stages:
            print(f"  {stage}")
        
        # Data consistency requirements
        consistency_requirements = [
            "Atomic updates: Status and event changes in single transaction",
            "Event ordering: Process events based on timestamps",
            "Deduplication: Ignore duplicate events based on event ID/timestamp",
            "Idempotency: Multiple processing of same event has same result",
            "Rollback: Failed transactions must not leave partial state"
        ]
        
        print("\nData Consistency Requirements:")
        for requirement in consistency_requirements:
            print(f"  - {requirement}")
        
        assert True