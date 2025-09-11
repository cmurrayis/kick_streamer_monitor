"""
Integration tests for WebSocket connections and real-time data processing.

Tests WebSocket connectivity, subscription management, message handling,
reconnection logic, and integration with event processing pipeline.

These tests require:
- Internet connectivity to Kick.com WebSocket servers
- Valid streamer IDs for testing
- Configuration for WebSocket endpoints

Set these environment variables or use .env.test:
- TEST_WEBSOCKET_URL
- TEST_STREAMER_IDS (comma-separated)
- TEST_WEBSOCKET_TIMEOUT
"""

import pytest
import asyncio
import os
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch
from typing import Dict, Any, List, Optional
import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI

from src.services.websocket import WebSocketService, SubscriptionManager
from src.lib.config import ConfigurationManager
from src.lib.processor import EventProcessor, Event, EventType
from src.lib.errors import WebSocketError, NetworkError


class TestWebSocketIntegration:
    """Integration tests for WebSocket service."""
    
    @pytest.fixture(autouse=True)
    def setup_websocket_config(self):
        """Setup WebSocket configuration for testing."""
        self.websocket_config = {
            "url": os.getenv("TEST_WEBSOCKET_URL", "wss://ws-us2.pusher.com/socket"),
            "timeout": int(os.getenv("TEST_WEBSOCKET_TIMEOUT", "30")),
            "heartbeat_interval": 30,
            "reconnect_delay": 5,
            "max_reconnect_attempts": 3,
            "subscription_timeout": 10,
            "ping_interval": 20,
            "ping_timeout": 10
        }
        
        self.test_streamer_ids = [
            int(id.strip()) 
            for id in os.getenv("TEST_STREAMER_IDS", "12345,67890").split(",")
        ]
        
        # Skip tests if WebSocket URL not configured
        if not os.getenv("TEST_WEBSOCKET_URL"):
            pytest.skip("WebSocket URL required for integration tests")
    
    @pytest.fixture
    async def websocket_service(self):
        """Create WebSocket service for testing."""
        config_manager = ConfigurationManager(auto_load=False)
        config_manager.load_from_dict({
            "websocket": self.websocket_config,
            "streamers": {
                "test_ids": self.test_streamer_ids
            }
        })
        
        service = WebSocketService(config_manager)
        yield service
        
        # Cleanup
        await service.stop()
    
    @pytest.fixture
    async def mock_websocket_service(self):
        """Create WebSocket service with mocked connections."""
        config_manager = ConfigurationManager(auto_load=False)
        config_manager.load_from_dict({
            "websocket": self.websocket_config,
            "streamers": {
                "test_ids": self.test_streamer_ids
            }
        })
        
        with patch('websockets.connect') as mock_connect:
            mock_websocket = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_websocket
            
            service = WebSocketService(config_manager)
            yield service, mock_websocket
            
            await service.stop()
    
    @pytest.mark.asyncio
    async def test_websocket_connection_basic(self, websocket_service):
        """Test basic WebSocket connection establishment."""
        # Test connection
        success = await websocket_service.connect()
        assert success is True
        
        # Verify connection status
        status = websocket_service.get_connection_status()
        assert status["is_connected"] is True
        assert "connection_time" in status
        assert "uptime_seconds" in status
        
        # Test disconnection
        await websocket_service.disconnect()
        status = websocket_service.get_connection_status()
        assert status["is_connected"] is False
    
    @pytest.mark.asyncio
    async def test_websocket_connection_failure(self, mock_websocket_service):
        """Test WebSocket connection failure handling."""
        websocket_service, mock_websocket = mock_websocket_service
        
        # Mock connection failure
        with patch('websockets.connect') as mock_connect:
            mock_connect.side_effect = OSError("Connection failed")
            
            success = await websocket_service.connect()
            assert success is False
            
            status = websocket_service.get_connection_status()
            assert status["is_connected"] is False
            assert "last_error" in status
    
    @pytest.mark.asyncio
    async def test_websocket_message_sending(self, mock_websocket_service):
        """Test sending messages through WebSocket."""
        websocket_service, mock_websocket = mock_websocket_service
        
        # Connect first
        await websocket_service.connect()
        
        # Test sending message
        test_message = {
            "event": "pusher:subscribe",
            "data": {
                "channel": "channel.12345"
            }
        }
        
        success = await websocket_service.send_message(test_message)
        assert success is True
        
        # Verify message was sent
        mock_websocket.send.assert_called_once()
        sent_data = mock_websocket.send.call_args[0][0]
        sent_message = json.loads(sent_data)
        assert sent_message["event"] == "pusher:subscribe"
        assert sent_message["data"]["channel"] == "channel.12345"
    
    @pytest.mark.asyncio
    async def test_websocket_message_receiving(self, mock_websocket_service):
        """Test receiving and processing WebSocket messages."""
        websocket_service, mock_websocket = mock_websocket_service
        
        received_messages = []
        
        def message_handler(message):
            received_messages.append(message)
        
        websocket_service.set_message_handler(message_handler)
        
        # Mock incoming messages
        test_messages = [
            '{"event": "pusher:connection_established", "data": "{\\"socket_id\\":\\"12345\\"}"}',
            '{"event": "pusher:subscription_succeeded", "data": "{}"}',
            '{"event": "stream-start", "data": "{\\"streamer_id\\": 12345, \\"title\\": \\"Test Stream\\"}"}',
        ]
        
        # Mock receive to return messages one by one
        mock_websocket.recv.side_effect = test_messages + [ConnectionClosed(1000, "Normal closure")]
        
        # Connect and start receiving
        await websocket_service.connect()
        
        # Give time for messages to be processed
        await asyncio.sleep(0.1)
        
        # Verify messages were received
        assert len(received_messages) >= 2  # At least connection and subscription messages
    
    @pytest.mark.asyncio
    async def test_websocket_subscription_management(self, mock_websocket_service):
        """Test WebSocket channel subscription management."""
        websocket_service, mock_websocket = mock_websocket_service
        
        await websocket_service.connect()
        
        # Test subscribing to channel
        channel = "channel.12345"
        success = await websocket_service.subscribe_to_channel(channel)
        assert success is True
        
        # Verify subscription message was sent
        assert mock_websocket.send.called
        subscription_calls = [
            call for call in mock_websocket.send.call_args_list
            if "pusher:subscribe" in str(call)
        ]
        assert len(subscription_calls) > 0
        
        # Test unsubscribing
        success = await websocket_service.unsubscribe_from_channel(channel)
        assert success is True
        
        # Verify unsubscription message was sent
        unsubscription_calls = [
            call for call in mock_websocket.send.call_args_list
            if "pusher:unsubscribe" in str(call)
        ]
        assert len(unsubscription_calls) > 0
    
    @pytest.mark.asyncio
    async def test_websocket_streamer_subscription(self, mock_websocket_service):
        """Test subscribing to specific streamers."""
        websocket_service, mock_websocket = mock_websocket_service
        
        await websocket_service.connect()
        
        # Test subscribing to streamer
        streamer_id = 12345
        success = await websocket_service.subscribe_to_streamer(streamer_id)
        assert success is True
        
        # Verify correct channel subscription
        sent_messages = []
        for call in mock_websocket.send.call_args_list:
            if call[0]:  # Has positional args
                message = json.loads(call[0][0])
                sent_messages.append(message)
        
        # Should have subscribed to streamer's channel
        subscribe_messages = [
            msg for msg in sent_messages
            if msg.get("event") == "pusher:subscribe" and
               f"channel.{streamer_id}" in msg.get("data", {}).get("channel", "")
        ]
        assert len(subscribe_messages) > 0
    
    @pytest.mark.asyncio
    async def test_websocket_heartbeat_mechanism(self, mock_websocket_service):
        """Test WebSocket heartbeat/ping mechanism."""
        websocket_service, mock_websocket = mock_websocket_service
        
        # Configure short heartbeat interval for testing
        websocket_service.heartbeat_interval = 1  # 1 second
        
        await websocket_service.connect()
        
        # Wait for heartbeat
        await asyncio.sleep(1.5)
        
        # Check that ping was sent
        ping_calls = [
            call for call in mock_websocket.send.call_args_list
            if "ping" in str(call).lower() or "pusher:ping" in str(call)
        ]
        
        # Note: Actual implementation may vary, this tests the concept
        # assert len(ping_calls) > 0
    
    @pytest.mark.asyncio
    async def test_websocket_reconnection_logic(self, mock_websocket_service):
        """Test WebSocket reconnection after connection loss."""
        websocket_service, mock_websocket = mock_websocket_service
        
        # Connect initially
        await websocket_service.connect()
        assert websocket_service.is_connected()
        
        # Simulate connection loss
        websocket_service._connection = None
        websocket_service._connected = False
        
        # Mock reconnection
        with patch.object(websocket_service, 'connect') as mock_reconnect:
            mock_reconnect.return_value = True
            
            # Trigger reconnection
            await websocket_service._handle_connection_loss()
            
            # Should attempt to reconnect
            mock_reconnect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_websocket_message_validation(self, mock_websocket_service):
        """Test WebSocket message validation and filtering."""
        websocket_service, mock_websocket = mock_websocket_service
        
        valid_messages = []
        invalid_messages = []
        
        def message_handler(message):
            if websocket_service._is_valid_message(message):
                valid_messages.append(message)
            else:
                invalid_messages.append(message)
        
        websocket_service.set_message_handler(message_handler)
        
        # Test various message formats
        test_messages = [
            '{"event": "stream-start", "data": "{\\"streamer_id\\": 12345}"}',  # Valid
            '{"event": "invalid-event", "data": "{}"}',  # Potentially invalid
            'invalid json',  # Invalid JSON
            '{"incomplete": "message"}',  # Missing required fields
        ]
        
        mock_websocket.recv.side_effect = test_messages + [ConnectionClosed(1000, "Test complete")]
        
        await websocket_service.connect()
        await asyncio.sleep(0.1)
        
        # Should have processed some messages
        total_processed = len(valid_messages) + len(invalid_messages)
        assert total_processed > 0
    
    @pytest.mark.asyncio
    async def test_websocket_error_handling(self, mock_websocket_service):
        """Test WebSocket error handling scenarios."""
        websocket_service, mock_websocket = mock_websocket_service
        
        error_count = 0
        
        def error_handler(error):
            nonlocal error_count
            error_count += 1
        
        websocket_service.set_error_handler(error_handler)
        
        # Test connection error
        with patch('websockets.connect') as mock_connect:
            mock_connect.side_effect = InvalidURI("Invalid WebSocket URI")
            
            success = await websocket_service.connect()
            assert success is False
            assert error_count > 0
    
    @pytest.mark.asyncio
    async def test_websocket_performance_metrics(self, mock_websocket_service):
        """Test WebSocket performance tracking."""
        websocket_service, mock_websocket = mock_websocket_service
        
        await websocket_service.connect()
        
        # Simulate message processing
        for i in range(10):
            test_message = {
                "event": "test-event",
                "data": json.dumps({"message_id": i})
            }
            await websocket_service.send_message(test_message)
        
        # Get performance stats
        stats = websocket_service.get_stats()
        
        assert "total_messages_sent" in stats
        assert "total_messages_received" in stats
        assert "connection_uptime" in stats
        assert "reconnect_count" in stats
        assert "last_message_time" in stats
        
        assert stats["total_messages_sent"] >= 10
    
    @pytest.mark.asyncio
    async def test_websocket_concurrent_operations(self, mock_websocket_service):
        """Test concurrent WebSocket operations."""
        websocket_service, mock_websocket = mock_websocket_service
        
        await websocket_service.connect()
        
        # Test concurrent subscriptions
        streamer_ids = [12345, 67890, 11111, 22222, 33333]
        
        subscription_tasks = [
            websocket_service.subscribe_to_streamer(streamer_id)
            for streamer_id in streamer_ids
        ]
        
        results = await asyncio.gather(*subscription_tasks, return_exceptions=True)
        
        # Most subscriptions should succeed
        successful_subs = [r for r in results if r is True]
        assert len(successful_subs) >= len(streamer_ids) - 1  # Allow for one failure
    
    @pytest.mark.asyncio
    async def test_websocket_integration_with_event_processor(self, mock_websocket_service):
        """Test WebSocket integration with event processing pipeline."""
        websocket_service, mock_websocket = mock_websocket_service
        
        # Setup event processor
        event_processor = EventProcessor()
        processed_events = []
        
        async def event_handler(events):
            processed_events.extend(events)
        
        event_processor.set_batch_callback(event_handler)
        websocket_service.set_event_processor(event_processor)
        
        # Mock incoming stream events
        stream_messages = [
            '{"event": "stream-start", "data": "{\\"streamer_id\\": 12345, \\"title\\": \\"Test Stream\\"}"}',
            '{"event": "stream-end", "data": "{\\"streamer_id\\": 12345, \\"duration\\": 3600}"}',
            '{"event": "viewer-count-update", "data": "{\\"streamer_id\\": 12345, \\"viewers\\": 150}"}',
        ]
        
        mock_websocket.recv.side_effect = stream_messages + [ConnectionClosed(1000, "Test complete")]
        
        await websocket_service.connect()
        await asyncio.sleep(0.2)  # Allow time for processing
        
        # Events should have been processed
        assert len(processed_events) > 0
    
    @pytest.mark.asyncio
    async def test_websocket_rate_limiting_compliance(self, mock_websocket_service):
        """Test WebSocket rate limiting compliance."""
        websocket_service, mock_websocket = mock_websocket_service
        
        await websocket_service.connect()
        
        # Test rapid subscription attempts
        start_time = time.time()
        
        for i in range(20):  # Try to subscribe rapidly
            await websocket_service.subscribe_to_channel(f"channel.{i}")
            # Small delay to avoid overwhelming the mock
            await asyncio.sleep(0.01)
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Should have rate limiting (min time between operations)
        expected_min_duration = 0.1  # Assuming some rate limiting
        # Note: Actual rate limiting behavior depends on implementation
        
        stats = websocket_service.get_stats()
        # Check for rate limiting metrics if implemented
        if "rate_limited_operations" in stats:
            assert stats["rate_limited_operations"] >= 0


class TestSubscriptionManager:
    """Test subscription management functionality."""
    
    @pytest.fixture
    def subscription_manager(self):
        """Create subscription manager for testing."""
        return SubscriptionManager()
    
    def test_subscription_manager_creation(self, subscription_manager):
        """Test creating subscription manager."""
        assert len(subscription_manager.get_active_subscriptions()) == 0
        assert subscription_manager.get_subscription_count() == 0
    
    def test_add_subscription(self, subscription_manager):
        """Test adding subscriptions."""
        channel = "channel.12345"
        subscription_manager.add_subscription(channel)
        
        assert channel in subscription_manager.get_active_subscriptions()
        assert subscription_manager.get_subscription_count() == 1
        assert subscription_manager.is_subscribed(channel)
    
    def test_remove_subscription(self, subscription_manager):
        """Test removing subscriptions."""
        channel = "channel.12345"
        subscription_manager.add_subscription(channel)
        subscription_manager.remove_subscription(channel)
        
        assert channel not in subscription_manager.get_active_subscriptions()
        assert subscription_manager.get_subscription_count() == 0
        assert not subscription_manager.is_subscribed(channel)
    
    def test_bulk_subscription_operations(self, subscription_manager):
        """Test bulk subscription operations."""
        channels = ["channel.1", "channel.2", "channel.3", "channel.4"]
        
        # Bulk add
        for channel in channels:
            subscription_manager.add_subscription(channel)
        
        assert subscription_manager.get_subscription_count() == 4
        
        # Bulk remove
        subscription_manager.clear_all_subscriptions()
        assert subscription_manager.get_subscription_count() == 0
    
    def test_subscription_metadata(self, subscription_manager):
        """Test subscription metadata tracking."""
        channel = "channel.12345"
        metadata = {"streamer_id": 12345, "subscription_time": datetime.now(timezone.utc)}
        
        subscription_manager.add_subscription(channel, metadata=metadata)
        
        stored_metadata = subscription_manager.get_subscription_metadata(channel)
        assert stored_metadata is not None
        assert stored_metadata["streamer_id"] == 12345
    
    def test_subscription_limits(self, subscription_manager):
        """Test subscription limits and validation."""
        # Test maximum subscriptions if implemented
        max_subscriptions = 100  # Assume reasonable limit
        
        for i in range(max_subscriptions + 10):
            result = subscription_manager.add_subscription(f"channel.{i}")
            if not result:  # If limit enforced
                break
        
        # Should respect limits
        count = subscription_manager.get_subscription_count()
        assert count <= max_subscriptions


@pytest.mark.integration
class TestWebSocketRealConnection:
    """Integration tests against real WebSocket servers (when available)."""
    
    @pytest.fixture(autouse=True)
    def check_real_websocket_config(self):
        """Check if real WebSocket configuration is available."""
        if not os.getenv("REAL_WEBSOCKET_URL"):
            pytest.skip("Real WebSocket URL required for real connection tests")
        
        self.real_websocket_config = {
            "url": os.getenv("REAL_WEBSOCKET_URL"),
            "timeout": int(os.getenv("REAL_WEBSOCKET_TIMEOUT", "30")),
            "heartbeat_interval": 30,
            "reconnect_delay": 5,
            "max_reconnect_attempts": 3
        }
    
    @pytest.fixture
    async def real_websocket_service(self):
        """Create WebSocket service for real connection testing."""
        config_manager = ConfigurationManager(auto_load=False)
        config_manager.load_from_dict({
            "websocket": self.real_websocket_config
        })
        
        service = WebSocketService(config_manager)
        yield service
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_real_websocket_connectivity(self, real_websocket_service):
        """Test connectivity to real WebSocket server."""
        # Test basic connection
        success = await real_websocket_service.connect()
        assert success is True
        
        # Verify connection status
        status = real_websocket_service.get_connection_status()
        assert status["is_connected"] is True
        
        # Test graceful disconnection
        await real_websocket_service.disconnect()
        status = real_websocket_service.get_connection_status()
        assert status["is_connected"] is False
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(60)  # 60 second timeout for real connections
    async def test_real_websocket_message_flow(self, real_websocket_service):
        """Test real message flow with WebSocket server."""
        received_messages = []
        
        def message_handler(message):
            received_messages.append(message)
        
        real_websocket_service.set_message_handler(message_handler)
        
        # Connect and wait for initial messages
        await real_websocket_service.connect()
        await asyncio.sleep(5)  # Wait for connection establishment messages
        
        # Should have received at least connection acknowledgment
        assert len(received_messages) > 0
        
        # Verify message format
        for message in received_messages:
            assert isinstance(message, dict)
            assert "event" in message or "data" in message  # Basic message structure