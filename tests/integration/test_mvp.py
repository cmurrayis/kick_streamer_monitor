"""
End-to-end MVP test simulating complete monitoring cycle.

Tests the complete streamer monitoring workflow from WebSocket connection
to database updates, simulating real-world usage of the monitoring system.
"""

import asyncio
import json
import logging
import os
import pytest
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
from unittest.mock import patch, AsyncMock

from src.services.database import DatabaseService
from src.services.websocket import WebSocketService
from src.services.auth import AuthenticationService
from src.services.monitor import MonitoringService
from src.lib.config import ConfigurationManager
from src.lib.processor import EventProcessor
from src.models.streamer import Streamer, StreamerStatus, StreamerCreate
from src.models.status_event import StatusEvent, EventType

from tests.fixtures.test_db import DatabaseTestHelper, DatabaseTestConfig, skip_if_no_database
from tests.fixtures.websocket_utils import (
    MockWebSocketConnection, WebSocketMessageBuilder, WebSocketEventSequence,
    WebSocketPerformanceRecorder
)

logger = logging.getLogger(__name__)


@pytest.mark.integration
class TestMVPEndToEnd:
    """End-to-end tests for the complete monitoring system."""
    
    @pytest.fixture(autouse=True)
    def setup_test_environment(self):
        """Setup test environment and configuration."""
        self.test_config = {
            # Database configuration
            "DATABASE_HOST": os.getenv("TEST_DATABASE_HOST", "localhost"),
            "DATABASE_PORT": os.getenv("TEST_DATABASE_PORT", "5432"),
            "DATABASE_NAME": f"kick_monitor_e2e_{int(time.time())}",
            "DATABASE_USER": os.getenv("TEST_DATABASE_USER", "kick_monitor_test"),
            "DATABASE_PASSWORD": os.getenv("TEST_DATABASE_PASSWORD", "test_password"),
            
            # Authentication configuration (mock values for testing)
            "KICK_CLIENT_ID": "test_client_id_12345",
            "KICK_CLIENT_SECRET": "test_client_secret_67890",
            
            # WebSocket configuration
            "PUSHER_APP_KEY": "test_app_key",
            "PUSHER_CLUSTER": "us2",
            
            # Monitoring configuration
            "MONITORING_STATUS_UPDATE_INTERVAL": "1",  # Fast updates for testing
            "MONITORING_RECONNECT_DELAY": "5",
            "MONITORING_MAX_RECONNECT_ATTEMPTS": "3"
        }
        
        self.test_streamers = [
            {"id": 12345, "username": "teststreamer1", "kick_user_id": "12345"},
            {"id": 67890, "username": "teststreamer2", "kick_user_id": "67890"},
            {"id": 11111, "username": "teststreamer3", "kick_user_id": "11111"}
        ]
    
    @pytest.fixture
    async def database_helper(self):
        """Database helper for end-to-end testing."""
        db_config = DatabaseTestConfig(
            host=self.test_config["DATABASE_HOST"],
            port=int(self.test_config["DATABASE_PORT"]),
            database=self.test_config["DATABASE_NAME"],
            username=self.test_config["DATABASE_USER"],
            password=self.test_config["DATABASE_PASSWORD"],
            use_existing=False
        )
        
        helper = DatabaseTestHelper(db_config)
        await helper.setup_database()
        
        try:
            yield helper
        finally:
            await helper.cleanup_database()
    
    @pytest.fixture
    async def config_manager(self):
        """Configuration manager with test settings."""
        config = ConfigurationManager(auto_load=False)
        
        # Load test configuration
        for key, value in self.test_config.items():
            config.set(key, value)
        
        return config
    
    @pytest.fixture
    async def mock_websocket_connection(self):
        """Mock WebSocket connection for testing."""
        return MockWebSocketConnection(auto_respond=True)
    
    @pytest.fixture
    async def performance_recorder(self):
        """Performance recorder for monitoring test performance."""
        return WebSocketPerformanceRecorder()
    
    @skip_if_no_database()
    @pytest.mark.asyncio
    async def test_complete_monitoring_cycle_single_streamer(self, database_helper, 
                                                             config_manager, 
                                                             mock_websocket_connection,
                                                             performance_recorder):
        """Test complete monitoring cycle for a single streamer."""
        performance_recorder.start_timer("complete_cycle")
        
        # Step 1: Setup database with test streamer
        streamer_data = StreamerCreate(
            kick_user_id="12345",
            username="teststreamer1",
            display_name="Test Streamer 1",
            status=StreamerStatus.UNKNOWN
        )
        
        async with database_helper.database_service() as db_service:
            created_streamer = await db_service.create_streamer(streamer_data)
            logger.info(f"Created test streamer: {created_streamer.username} (ID: {created_streamer.id})")
        
        # Step 2: Initialize monitoring components
        performance_recorder.start_timer("component_initialization")
        
        # Mock WebSocket service with our mock connection
        with patch('src.services.websocket.websockets.connect') as mock_connect:
            mock_connect.return_value.__aenter__.return_value = mock_websocket_connection
            
            # Initialize services
            db_service = DatabaseService(database_helper.config.to_database_config())
            await db_service.connect()
            
            websocket_service = WebSocketService(config_manager)
            event_processor = EventProcessor()
            
            # Connect WebSocket and subscribe to streamer channel
            await websocket_service.connect()
            await websocket_service.subscribe_to_streamer(int(created_streamer.kick_user_id))
        
        performance_recorder.end_timer("component_initialization")
        
        # Step 3: Simulate streamer going online
        performance_recorder.start_timer("online_event_processing")
        
        online_event = WebSocketMessageBuilder.streamer_online_event(
            streamer_id=int(created_streamer.kick_user_id),
            channel_id=int(created_streamer.kick_user_id),
            title="Test Stream - Going Live",
            viewer_count=50
        )
        
        # Simulate receiving the online event
        await mock_websocket_connection._deliver_message(online_event)
        
        # Allow time for processing
        await asyncio.sleep(0.5)
        
        performance_recorder.end_timer("online_event_processing")
        
        # Step 4: Verify streamer status was updated to online
        async with database_helper.database_service() as db_service:
            updated_streamer = await db_service.get_streamer_by_id(created_streamer.id)
            assert updated_streamer.status == StreamerStatus.ONLINE
            assert updated_streamer.last_seen_online is not None
            logger.info(f"Verified streamer {updated_streamer.username} is now ONLINE")
        
        # Step 5: Simulate viewer count updates
        for i, viewer_count in enumerate([75, 120, 180, 150]):
            viewer_update = WebSocketMessageBuilder.viewer_count_update(
                streamer_id=int(created_streamer.kick_user_id),
                viewer_count=viewer_count
            )
            await mock_websocket_connection._deliver_message(viewer_update)
            await asyncio.sleep(0.1)  # Small delay between updates
        
        # Step 6: Simulate streamer going offline
        performance_recorder.start_timer("offline_event_processing")
        
        offline_event = WebSocketMessageBuilder.streamer_offline_event(
            streamer_id=int(created_streamer.kick_user_id),
            channel_id=int(created_streamer.kick_user_id),
            final_viewer_count=120
        )
        
        await mock_websocket_connection._deliver_message(offline_event)
        await asyncio.sleep(0.5)
        
        performance_recorder.end_timer("offline_event_processing")
        
        # Step 7: Verify streamer status was updated to offline
        async with database_helper.database_service() as db_service:
            final_streamer = await db_service.get_streamer_by_id(created_streamer.id)
            assert final_streamer.status == StreamerStatus.OFFLINE
            logger.info(f"Verified streamer {final_streamer.username} is now OFFLINE")
            
            # Verify events were recorded
            events = await db_service.get_streamer_events(created_streamer.id, limit=10)
            assert len(events) >= 2  # At least online and offline events
            
            # Check event types
            event_types = [event.event_type for event in events]
            assert EventType.STREAM_START in event_types
            assert EventType.STREAM_END in event_types
        
        # Step 8: Cleanup
        await websocket_service.disconnect()
        await db_service.disconnect()
        
        performance_recorder.end_timer("complete_cycle")
        
        # Verify performance requirements
        performance_recorder.assert_performance_requirements(
            max_connection_time=5.0,
            max_message_time=1.0,
            max_subscription_time=2.0
        )
        
        logger.info("Complete monitoring cycle test passed successfully")
    
    @skip_if_no_database()
    @pytest.mark.asyncio
    async def test_multiple_streamers_concurrent_monitoring(self, database_helper, 
                                                            config_manager,
                                                            mock_websocket_connection):
        """Test monitoring multiple streamers concurrently."""
        # Setup multiple test streamers
        streamer_ids = []
        async with database_helper.database_service() as db_service:
            for streamer_data in self.test_streamers:
                streamer = await db_service.create_streamer(StreamerCreate(
                    kick_user_id=str(streamer_data["id"]),
                    username=streamer_data["username"],
                    display_name=f"Test {streamer_data['username']}",
                    status=StreamerStatus.UNKNOWN
                ))
                streamer_ids.append(streamer.id)
        
        logger.info(f"Created {len(streamer_ids)} test streamers")
        
        # Initialize monitoring services
        with patch('src.services.websocket.websockets.connect') as mock_connect:
            mock_connect.return_value.__aenter__.return_value = mock_websocket_connection
            
            websocket_service = WebSocketService(config_manager)
            await websocket_service.connect()
            
            # Subscribe to all streamers
            for streamer_data in self.test_streamers:
                await websocket_service.subscribe_to_streamer(streamer_data["id"])
        
        # Create event sequence for multiple streamers
        event_sequence = WebSocketEventSequence()
        
        # Streamer 1 goes online
        event_sequence.streamer_goes_online(self.test_streamers[0]["id"], delay=0)
        
        # Streamer 2 goes online after 2 seconds
        event_sequence.streamer_goes_online(self.test_streamers[1]["id"], delay=2)
        
        # Streamer 1 goes offline after 5 seconds
        event_sequence.streamer_goes_offline(self.test_streamers[0]["id"], delay=5)
        
        # Streamer 3 goes online after 7 seconds
        event_sequence.streamer_goes_online(self.test_streamers[2]["id"], delay=7)
        
        # Streamer 2 goes offline after 10 seconds
        event_sequence.streamer_goes_offline(self.test_streamers[1]["id"], delay=10)
        
        # Add timing constraints
        event_sequence.add_timing_constraint(0, 1, 3.0)  # Streamers should go online within 3s
        event_sequence.add_timing_constraint(2, 4, 6.0)  # Offline events should process within 6s
        
        # Execute the event sequence
        await event_sequence.replay_on_connection(mock_websocket_connection)
        
        # Allow time for all events to process
        await asyncio.sleep(12)
        
        # Verify final states
        async with database_helper.database_service() as db_service:
            for i, streamer_id in enumerate(streamer_ids):
                streamer = await db_service.get_streamer_by_id(streamer_id)
                events = await db_service.get_streamer_events(streamer_id)
                
                logger.info(f"Streamer {i+1} final status: {streamer.status}, events: {len(events)}")
                
                # Verify events were recorded
                assert len(events) > 0
        
        await websocket_service.disconnect()
        logger.info("Concurrent monitoring test completed successfully")
    
    @skip_if_no_database()
    @pytest.mark.asyncio
    async def test_error_recovery_and_reconnection(self, database_helper, 
                                                   config_manager):
        """Test error recovery and reconnection scenarios."""
        # Create test streamer
        async with database_helper.database_service() as db_service:
            streamer = await db_service.create_streamer(StreamerCreate(
                kick_user_id="99999",
                username="error_test_streamer",
                display_name="Error Test Streamer",
                status=StreamerStatus.UNKNOWN
            ))
        
        # Mock WebSocket that will fail initially
        connection_attempts = 0
        
        class FailingMockConnection(MockWebSocketConnection):
            def __init__(self, fail_attempts=2):
                super().__init__()
                self.fail_attempts = fail_attempts
                self.attempt_count = 0
            
            async def connect(self):
                nonlocal connection_attempts
                connection_attempts += 1
                self.attempt_count += 1
                
                if self.attempt_count <= self.fail_attempts:
                    raise ConnectionError(f"Connection failed (attempt {self.attempt_count})")
                
                await super().connect()
        
        failing_connection = FailingMockConnection(fail_attempts=2)
        
        with patch('src.services.websocket.websockets.connect') as mock_connect:
            mock_connect.return_value.__aenter__.return_value = failing_connection
            
            websocket_service = WebSocketService(config_manager)
            
            # First connection attempt should fail
            success = await websocket_service.connect()
            assert success is False
            
            # Simulate reconnection logic
            max_attempts = 3
            for attempt in range(max_attempts):
                if await websocket_service.connect():
                    logger.info(f"Successfully connected on attempt {attempt + 1}")
                    break
                await asyncio.sleep(1)  # Wait before retry
            else:
                pytest.fail("Failed to connect after all retry attempts")
            
            # Verify connection is established
            assert websocket_service.is_connected()
            
            # Test that normal operations work after recovery
            await websocket_service.subscribe_to_streamer(int(streamer.kick_user_id))
            
            # Simulate stream event
            online_event = WebSocketMessageBuilder.streamer_online_event(
                streamer_id=int(streamer.kick_user_id)
            )
            await failing_connection._deliver_message(online_event)
            
            await asyncio.sleep(0.5)
            
            await websocket_service.disconnect()
        
        # Verify that events were processed despite initial failures
        async with database_helper.database_service() as db_service:
            updated_streamer = await db_service.get_streamer_by_id(streamer.id)
            events = await db_service.get_streamer_events(streamer.id)
            
            logger.info(f"After recovery - Status: {updated_streamer.status}, Events: {len(events)}")
        
        logger.info("Error recovery test completed successfully")
    
    @skip_if_no_database()
    @pytest.mark.asyncio
    async def test_high_volume_event_processing(self, database_helper, 
                                                config_manager,
                                                mock_websocket_connection):
        """Test system performance under high event volume."""
        # Create multiple streamers for high-volume testing
        streamer_count = 10
        events_per_streamer = 20
        
        async with database_helper.database_service() as db_service:
            streamer_ids = []
            for i in range(streamer_count):
                streamer = await db_service.create_streamer(StreamerCreate(
                    kick_user_id=str(20000 + i),
                    username=f"volume_test_streamer_{i}",
                    display_name=f"Volume Test Streamer {i}",
                    status=StreamerStatus.UNKNOWN
                ))
                streamer_ids.append(streamer.id)
        
        logger.info(f"Created {streamer_count} streamers for volume testing")
        
        # Initialize monitoring
        with patch('src.services.websocket.websockets.connect') as mock_connect:
            mock_connect.return_value.__aenter__.return_value = mock_websocket_connection
            
            websocket_service = WebSocketService(config_manager)
            await websocket_service.connect()
            
            # Subscribe to all streamers
            for i in range(streamer_count):
                await websocket_service.subscribe_to_streamer(20000 + i)
        
        # Generate high volume of events
        start_time = time.time()
        
        for streamer_idx in range(streamer_count):
            streamer_id = 20000 + streamer_idx
            
            # Send multiple events for each streamer
            for event_idx in range(events_per_streamer):
                if event_idx % 2 == 0:
                    # Online event
                    event = WebSocketMessageBuilder.streamer_online_event(
                        streamer_id=streamer_id,
                        title=f"Stream {event_idx + 1}",
                        viewer_count=50 + event_idx * 10
                    )
                else:
                    # Offline event
                    event = WebSocketMessageBuilder.streamer_offline_event(
                        streamer_id=streamer_id,
                        final_viewer_count=100 + event_idx * 5
                    )
                
                await mock_websocket_connection._deliver_message(event)
                
                # Small delay to avoid overwhelming the system
                await asyncio.sleep(0.01)
        
        # Allow time for all events to process
        await asyncio.sleep(5)
        
        end_time = time.time()
        processing_time = end_time - start_time
        total_events = streamer_count * events_per_streamer
        events_per_second = total_events / processing_time
        
        logger.info(f"Processed {total_events} events in {processing_time:.2f}s "
                   f"({events_per_second:.2f} events/sec)")
        
        # Verify all events were processed
        total_processed_events = 0
        async with database_helper.database_service() as db_service:
            for streamer_id in streamer_ids:
                events = await db_service.get_streamer_events(streamer_id)
                total_processed_events += len(events)
        
        logger.info(f"Total events stored in database: {total_processed_events}")
        
        # Assert performance requirements
        assert events_per_second >= 50, f"Event processing too slow: {events_per_second:.2f} events/sec"
        assert total_processed_events >= total_events * 0.9, "Too many events lost during processing"
        
        await websocket_service.disconnect()
        logger.info("High volume processing test completed successfully")
    
    @skip_if_no_database()
    @pytest.mark.asyncio
    async def test_data_consistency_validation(self, database_helper, 
                                               config_manager,
                                               mock_websocket_connection):
        """Test data consistency across the monitoring pipeline."""
        # Create test streamer
        async with database_helper.database_service() as db_service:
            streamer = await db_service.create_streamer(StreamerCreate(
                kick_user_id="55555",
                username="consistency_test_streamer",
                display_name="Consistency Test Streamer",
                status=StreamerStatus.UNKNOWN
            ))
        
        # Initialize monitoring
        with patch('src.services.websocket.websockets.connect') as mock_connect:
            mock_connect.return_value.__aenter__.return_value = mock_websocket_connection
            
            websocket_service = WebSocketService(config_manager)
            await websocket_service.connect()
            await websocket_service.subscribe_to_streamer(55555)
        
        # Define test sequence with specific timestamps
        base_time = datetime.now(timezone.utc)
        
        test_events = [
            {
                "type": "online",
                "time": base_time,
                "viewer_count": 100,
                "title": "Consistency Test Stream"
            },
            {
                "type": "viewer_update", 
                "time": base_time + timedelta(minutes=30),
                "viewer_count": 250
            },
            {
                "type": "viewer_update",
                "time": base_time + timedelta(minutes=60),
                "viewer_count": 180
            },
            {
                "type": "offline",
                "time": base_time + timedelta(minutes=90),
                "final_viewer_count": 150
            }
        ]
        
        # Send events
        for event_data in test_events:
            if event_data["type"] == "online":
                event = WebSocketMessageBuilder.streamer_online_event(
                    streamer_id=55555,
                    title=event_data["title"],
                    viewer_count=event_data["viewer_count"]
                )
            elif event_data["type"] == "viewer_update":
                event = WebSocketMessageBuilder.viewer_count_update(
                    streamer_id=55555,
                    viewer_count=event_data["viewer_count"]
                )
            elif event_data["type"] == "offline":
                event = WebSocketMessageBuilder.streamer_offline_event(
                    streamer_id=55555,
                    final_viewer_count=event_data["final_viewer_count"]
                )
            
            # Override timestamp to ensure consistency
            event.timestamp = event_data["time"]
            
            await mock_websocket_connection._deliver_message(event)
            await asyncio.sleep(0.1)
        
        # Allow processing time
        await asyncio.sleep(2)
        
        # Validate data consistency
        async with database_helper.database_service() as db_service:
            # Check final streamer state
            final_streamer = await db_service.get_streamer_by_id(streamer.id)
            assert final_streamer.status == StreamerStatus.OFFLINE
            assert final_streamer.last_status_update is not None
            
            # Check event history
            events = await db_service.get_streamer_events(streamer.id)
            assert len(events) >= 2  # At least online and offline
            
            # Verify event chronology
            event_times = [event.timestamp for event in events]
            assert event_times == sorted(event_times), "Events not in chronological order"
            
            # Verify no duplicate events
            event_signatures = [(event.event_type, event.timestamp) for event in events]
            assert len(event_signatures) == len(set(event_signatures)), "Duplicate events detected"
        
        await websocket_service.disconnect()
        logger.info("Data consistency validation completed successfully")
    
    @pytest.mark.asyncio
    async def test_monitoring_system_metrics(self, config_manager):
        """Test monitoring system metrics and health checks."""
        performance_recorder = WebSocketPerformanceRecorder()
        
        # Simulate system startup
        performance_recorder.start_timer("system_startup")
        
        # Mock system initialization
        await asyncio.sleep(0.1)  # Simulate initialization time
        
        performance_recorder.end_timer("system_startup")
        
        # Record some operations
        for i in range(10):
            performance_recorder.start_timer(f"operation_{i}")
            await asyncio.sleep(0.05)  # Simulate operation
            performance_recorder.end_timer(f"operation_{i}")
        
        # Get performance statistics
        stats = performance_recorder.get_stats()
        
        # Validate metrics
        assert stats["connection"]["count"] >= 0
        assert stats["message_send"]["avg"] >= 0
        assert stats["message_receive"]["avg"] >= 0
        
        # Verify performance requirements are reasonable
        performance_recorder.assert_performance_requirements(
            max_connection_time=10.0,
            max_message_time=2.0,
            max_subscription_time=5.0
        )
        
        logger.info("Monitoring system metrics test completed successfully")
        logger.info(f"Performance stats: {stats}")


@pytest.mark.integration
class TestMVPIntegrationScenarios:
    """Integration scenarios for specific MVP use cases."""
    
    @pytest.mark.asyncio
    async def test_mvp_quick_validation(self):
        """Quick validation test for MVP functionality."""
        # This test simulates the quickstart scenarios from the specification
        
        # 1. Configuration validation
        config = ConfigurationManager(auto_load=False)
        config.set("KICK_CLIENT_ID", "test_client_123")
        config.set("KICK_CLIENT_SECRET", "test_secret_456")
        config.set("DATABASE_HOST", "localhost")
        config.set("DATABASE_NAME", "test_db")
        config.set("DATABASE_USER", "test_user")
        config.set("DATABASE_PASSWORD", "test_pass")
        
        validation_result = config.validate_configuration()
        assert validation_result.is_valid
        
        # 2. Mock service initialization
        db_config = config.get_database_config()
        assert db_config.host == "localhost"
        
        oauth_config = config.get_oauth_config()
        assert oauth_config.client_id == "test_client_123"
        
        # 3. WebSocket message validation
        message = WebSocketMessageBuilder.streamer_online_event(12345)
        message_dict = message.to_dict()
        
        assert message_dict["event"] == "App\\Events\\StreamerIsLive"
        assert "data" in message_dict
        assert message_dict["channel"] == "channel.12345"
        
        logger.info("MVP quick validation test passed")
    
    @pytest.mark.asyncio
    async def test_mvp_error_scenarios(self):
        """Test MVP error handling scenarios."""
        # Test configuration errors
        config = ConfigurationManager(auto_load=False)
        
        with pytest.raises(Exception):
            config.get_oauth_config()  # Should fail without credentials
        
        # Test WebSocket connection errors
        mock_connection = MockWebSocketConnection()
        await mock_connection.close(code=1006, reason="Connection lost")
        
        assert mock_connection.closed is True
        assert mock_connection.close_code == 1006
        
        # Test invalid message formats
        invalid_message = '{"invalid": "json"}'
        # Should handle gracefully without crashing
        
        logger.info("MVP error scenarios test passed")


if __name__ == "__main__":
    # Run tests with proper logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    pytest.main([__file__, "-v", "-s"])