"""
Production WebSocket client testing utilities.

Provides utilities for testing WebSocket connections, message handling, and integration
with Kick.com's Pusher service for production-ready testing scenarios.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Callable, AsyncGenerator, Tuple
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager
import pytest
import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI, WebSocketException

from src.services.websocket import WebSocketService, SubscriptionManager
from src.lib.config import ConfigurationManager
from src.lib.processor import EventProcessor, Event, EventType

logger = logging.getLogger(__name__)


class MockWebSocketMessage:
    """Mock WebSocket message for testing."""
    
    def __init__(self, event: str, data: Dict[str, Any], channel: Optional[str] = None):
        self.event = event
        self.data = data
        self.channel = channel
        self.timestamp = datetime.now(timezone.utc)
        self.message_id = str(uuid.uuid4())
    
    def to_json(self) -> str:
        """Convert message to JSON string."""
        message = {
            "event": self.event,
            "data": json.dumps(self.data) if isinstance(self.data, dict) else self.data
        }
        if self.channel:
            message["channel"] = self.channel
        
        return json.dumps(message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary."""
        message = {
            "event": self.event,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
            "message_id": self.message_id
        }
        if self.channel:
            message["channel"] = self.channel
        
        return message


class WebSocketMessageBuilder:
    """Builder for creating various types of WebSocket messages."""
    
    @staticmethod
    def pusher_connection_established(socket_id: str = None) -> MockWebSocketMessage:
        """Create Pusher connection established message."""
        if not socket_id:
            socket_id = f"{int(time.time())}.{uuid.uuid4().hex[:6]}"
        
        return MockWebSocketMessage(
            event="pusher:connection_established",
            data={
                "socket_id": socket_id,
                "activity_timeout": 120
            }
        )
    
    @staticmethod
    def pusher_subscription_succeeded(channel: str, data: Dict[str, Any] = None) -> MockWebSocketMessage:
        """Create Pusher subscription succeeded message."""
        return MockWebSocketMessage(
            event="pusher:subscription_succeeded",
            data=data or {},
            channel=channel
        )
    
    @staticmethod
    def pusher_subscription_error(channel: str, error_code: int = 4001, 
                                  error_message: str = "Subscription error") -> MockWebSocketMessage:
        """Create Pusher subscription error message."""
        return MockWebSocketMessage(
            event="pusher:subscription_error",
            data={
                "type": "PusherError",
                "error": error_message,
                "status": error_code
            },
            channel=channel
        )
    
    @staticmethod
    def pusher_ping() -> MockWebSocketMessage:
        """Create Pusher ping message."""
        return MockWebSocketMessage(
            event="pusher:ping",
            data={}
        )
    
    @staticmethod
    def pusher_pong() -> MockWebSocketMessage:
        """Create Pusher pong message."""
        return MockWebSocketMessage(
            event="pusher:pong",
            data={}
        )
    
    @staticmethod
    def pusher_error(error_code: int = 4000, error_message: str = "Connection error") -> MockWebSocketMessage:
        """Create Pusher error message."""
        return MockWebSocketMessage(
            event="pusher:error",
            data={
                "message": error_message,
                "code": error_code
            }
        )
    
    @staticmethod
    def streamer_online_event(streamer_id: int, channel_id: int = None, 
                              title: str = "Test Stream", viewer_count: int = 0) -> MockWebSocketMessage:
        """Create StreamerIsLive event message."""
        if not channel_id:
            channel_id = streamer_id
        
        return MockWebSocketMessage(
            event="App\\Events\\StreamerIsLive",
            data={
                "channel_id": channel_id,
                "user_id": streamer_id,
                "session": {
                    "id": f"session_{uuid.uuid4().hex[:8]}",
                    "title": title,
                    "is_live": True,
                    "viewer_count": viewer_count,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "categories": [
                        {
                            "id": 1,
                            "name": "Gaming"
                        }
                    ]
                }
            },
            channel=f"channel.{channel_id}"
        )
    
    @staticmethod
    def streamer_offline_event(streamer_id: int, channel_id: int = None, 
                               final_viewer_count: int = 0) -> MockWebSocketMessage:
        """Create StreamerOffline event message."""
        if not channel_id:
            channel_id = streamer_id
        
        return MockWebSocketMessage(
            event="App\\Events\\StreamerOffline",
            data={
                "channel_id": channel_id,
                "user_id": streamer_id,
                "session_ended_at": datetime.now(timezone.utc).isoformat(),
                "final_viewer_count": final_viewer_count
            },
            channel=f"channel.{channel_id}"
        )
    
    @staticmethod
    def viewer_count_update(streamer_id: int, viewer_count: int, 
                            channel_id: int = None) -> MockWebSocketMessage:
        """Create viewer count update message."""
        if not channel_id:
            channel_id = streamer_id
        
        return MockWebSocketMessage(
            event="App\\Events\\ViewerCountUpdate",
            data={
                "channel_id": channel_id,
                "user_id": streamer_id,
                "viewer_count": viewer_count,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            channel=f"channel.{channel_id}"
        )


class MockWebSocketConnection:
    """Mock WebSocket connection for testing."""
    
    def __init__(self, auto_respond: bool = True):
        self.auto_respond = auto_respond
        self.sent_messages: List[str] = []
        self.received_messages: List[MockWebSocketMessage] = []
        self.is_connected = False
        self.closed = False
        self.close_code = None
        self.close_reason = None
        
        # Message handlers
        self.on_message_callback: Optional[Callable] = None
        self.on_close_callback: Optional[Callable] = None
        self.on_error_callback: Optional[Callable] = None
        
        # Auto-response settings
        self.socket_id = f"{int(time.time())}.{uuid.uuid4().hex[:6]}"
        self._subscription_channels: set = set()
    
    async def connect(self) -> None:
        """Simulate connection establishment."""
        self.is_connected = True
        
        if self.auto_respond:
            # Send connection established message
            conn_message = WebSocketMessageBuilder.pusher_connection_established(self.socket_id)
            await self._deliver_message(conn_message)
    
    async def send(self, message: str) -> None:
        """Simulate sending a message."""
        if not self.is_connected:
            raise ConnectionClosed(1006, "Connection not established")
        
        self.sent_messages.append(message)
        
        if self.auto_respond:
            await self._handle_auto_response(message)
    
    async def recv(self) -> str:
        """Simulate receiving a message."""
        if not self.is_connected:
            raise ConnectionClosed(1006, "Connection closed")
        
        if self.received_messages:
            message = self.received_messages.pop(0)
            return message.to_json()
        
        # Simulate waiting for message
        await asyncio.sleep(0.1)
        raise asyncio.TimeoutError("No message received")
    
    async def close(self, code: int = 1000, reason: str = "Normal closure") -> None:
        """Simulate closing connection."""
        self.is_connected = False
        self.closed = True
        self.close_code = code
        self.close_reason = reason
        
        if self.on_close_callback:
            await self.on_close_callback(code, reason)
    
    async def _deliver_message(self, message: MockWebSocketMessage) -> None:
        """Deliver a message to the connection."""
        self.received_messages.append(message)
        
        if self.on_message_callback:
            await self.on_message_callback(message.to_json())
    
    async def _handle_auto_response(self, message_str: str) -> None:
        """Handle automatic responses to sent messages."""
        try:
            message = json.loads(message_str)
            event = message.get("event")
            
            if event == "pusher:subscribe":
                channel = message.get("data", {}).get("channel")
                if channel:
                    self._subscription_channels.add(channel)
                    response = WebSocketMessageBuilder.pusher_subscription_succeeded(channel)
                    await self._deliver_message(response)
            
            elif event == "pusher:unsubscribe":
                channel = message.get("data", {}).get("channel")
                if channel:
                    self._subscription_channels.discard(channel)
            
            elif event == "pusher:ping":
                response = WebSocketMessageBuilder.pusher_pong()
                await self._deliver_message(response)
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in auto-response handler: {message_str}")
    
    def simulate_streamer_online(self, streamer_id: int, channel_id: int = None) -> None:
        """Simulate a streamer going online."""
        message = WebSocketMessageBuilder.streamer_online_event(streamer_id, channel_id)
        asyncio.create_task(self._deliver_message(message))
    
    def simulate_streamer_offline(self, streamer_id: int, channel_id: int = None) -> None:
        """Simulate a streamer going offline."""
        message = WebSocketMessageBuilder.streamer_offline_event(streamer_id, channel_id)
        asyncio.create_task(self._deliver_message(message))
    
    def simulate_connection_error(self, error_code: int = 4000, 
                                  error_message: str = "Connection error") -> None:
        """Simulate a connection error."""
        message = WebSocketMessageBuilder.pusher_error(error_code, error_message)
        asyncio.create_task(self._deliver_message(message))
    
    def get_sent_subscription_messages(self) -> List[Dict[str, Any]]:
        """Get all subscription messages that were sent."""
        subscriptions = []
        for msg_str in self.sent_messages:
            try:
                msg = json.loads(msg_str)
                if msg.get("event") == "pusher:subscribe":
                    subscriptions.append(msg)
            except json.JSONDecodeError:
                continue
        return subscriptions
    
    def get_subscribed_channels(self) -> set:
        """Get set of currently subscribed channels."""
        return self._subscription_channels.copy()


class WebSocketTestServer:
    """Test server for WebSocket integration testing."""
    
    def __init__(self, host: str = "localhost", port: int = 0):
        self.host = host
        self.port = port
        self.server = None
        self.clients: List[MockWebSocketConnection] = []
        self.message_handlers: Dict[str, Callable] = {}
        
    async def start(self) -> Tuple[str, int]:
        """Start the test WebSocket server."""
        # This would typically start a real WebSocket server
        # For testing purposes, we'll simulate it
        self.port = self.port or 8765
        logger.info(f"Mock WebSocket test server started on {self.host}:{self.port}")
        return self.host, self.port
    
    async def stop(self) -> None:
        """Stop the test WebSocket server."""
        for client in self.clients:
            await client.close()
        self.clients.clear()
        logger.info("Mock WebSocket test server stopped")
    
    def add_message_handler(self, event: str, handler: Callable) -> None:
        """Add a message handler for specific events."""
        self.message_handlers[event] = handler
    
    @asynccontextmanager
    async def client_connection(self) -> AsyncGenerator[MockWebSocketConnection, None]:
        """Context manager for client connections."""
        client = MockWebSocketConnection()
        self.clients.append(client)
        
        try:
            await client.connect()
            yield client
        finally:
            await client.close()
            if client in self.clients:
                self.clients.remove(client)


class WebSocketTestFixtures:
    """Collection of WebSocket test fixtures and utilities."""
    
    @staticmethod
    @pytest.fixture
    def mock_websocket_connection():
        """Pytest fixture for mock WebSocket connection."""
        return MockWebSocketConnection()
    
    @staticmethod
    @pytest.fixture
    def websocket_message_builder():
        """Pytest fixture for WebSocket message builder."""
        return WebSocketMessageBuilder()
    
    @staticmethod
    @pytest.fixture
    async def websocket_test_server():
        """Pytest fixture for WebSocket test server."""
        server = WebSocketTestServer()
        await server.start()
        try:
            yield server
        finally:
            await server.stop()
    
    @staticmethod
    @pytest.fixture
    def websocket_test_config():
        """Pytest fixture for WebSocket test configuration."""
        return {
            "url": "wss://localhost:8765/socket",
            "timeout": 30.0,
            "heartbeat_interval": 30,
            "reconnect_delay": 5,
            "max_reconnect_attempts": 3,
            "subscription_timeout": 10,
            "ping_interval": 20,
            "ping_timeout": 10
        }
    
    @staticmethod
    @pytest.fixture
    def test_streamers():
        """Pytest fixture for test streamer data."""
        return [
            {"id": 12345, "username": "teststreamer1", "channel_id": 12345},
            {"id": 67890, "username": "teststreamer2", "channel_id": 67890},
            {"id": 11111, "username": "teststreamer3", "channel_id": 11111},
        ]


class WebSocketEventSequence:
    """Utility for creating and validating WebSocket event sequences."""
    
    def __init__(self):
        self.events: List[MockWebSocketMessage] = []
        self.timing_constraints: List[Tuple[int, int, float]] = []  # (from_idx, to_idx, max_delay)
    
    def add_event(self, event: MockWebSocketMessage, delay: float = 0) -> 'WebSocketEventSequence':
        """Add an event to the sequence."""
        if delay > 0:
            event.timestamp = event.timestamp + timedelta(seconds=delay)
        self.events.append(event)
        return self
    
    def add_timing_constraint(self, from_event_idx: int, to_event_idx: int, 
                              max_delay_seconds: float) -> 'WebSocketEventSequence':
        """Add timing constraint between events."""
        self.timing_constraints.append((from_event_idx, to_event_idx, max_delay_seconds))
        return self
    
    def streamer_goes_online(self, streamer_id: int, delay: float = 0) -> 'WebSocketEventSequence':
        """Add streamer online event."""
        event = WebSocketMessageBuilder.streamer_online_event(streamer_id)
        return self.add_event(event, delay)
    
    def streamer_goes_offline(self, streamer_id: int, delay: float = 0) -> 'WebSocketEventSequence':
        """Add streamer offline event."""
        event = WebSocketMessageBuilder.streamer_offline_event(streamer_id)
        return self.add_event(event, delay)
    
    def subscription_to_channel(self, channel: str, delay: float = 0) -> 'WebSocketEventSequence':
        """Add subscription success event."""
        event = WebSocketMessageBuilder.pusher_subscription_succeeded(channel)
        return self.add_event(event, delay)
    
    async def replay_on_connection(self, connection: MockWebSocketConnection) -> None:
        """Replay the event sequence on a connection."""
        for event in self.events:
            # Calculate delay from start
            now = datetime.now(timezone.utc)
            delay = (event.timestamp - now).total_seconds()
            
            if delay > 0:
                await asyncio.sleep(delay)
            
            await connection._deliver_message(event)
    
    def validate_timing_constraints(self, received_events: List[Dict[str, Any]]) -> List[str]:
        """Validate timing constraints against received events."""
        violations = []
        
        for from_idx, to_idx, max_delay in self.timing_constraints:
            if from_idx >= len(received_events) or to_idx >= len(received_events):
                violations.append(f"Insufficient events for constraint {from_idx}->{to_idx}")
                continue
            
            from_time = datetime.fromisoformat(received_events[from_idx]['timestamp'])
            to_time = datetime.fromisoformat(received_events[to_idx]['timestamp'])
            actual_delay = (to_time - from_time).total_seconds()
            
            if actual_delay > max_delay:
                violations.append(
                    f"Timing constraint violated: {from_idx}->{to_idx} "
                    f"took {actual_delay:.2f}s (max: {max_delay:.2f}s)"
                )
        
        return violations


class WebSocketPerformanceRecorder:
    """Records performance metrics for WebSocket operations."""
    
    def __init__(self):
        self.connection_times: List[float] = []
        self.message_send_times: List[float] = []
        self.message_receive_times: List[float] = []
        self.subscription_times: List[float] = []
        self.reconnection_times: List[float] = []
        
        self._start_times: Dict[str, float] = {}
    
    def start_timer(self, operation: str) -> None:
        """Start timing an operation."""
        self._start_times[operation] = time.time()
    
    def end_timer(self, operation: str) -> float:
        """End timing an operation and return duration."""
        if operation not in self._start_times:
            return 0.0
        
        duration = time.time() - self._start_times[operation]
        del self._start_times[operation]
        
        # Record duration based on operation type
        if "connection" in operation.lower():
            self.connection_times.append(duration)
        elif "send" in operation.lower():
            self.message_send_times.append(duration)
        elif "receive" in operation.lower():
            self.message_receive_times.append(duration)
        elif "subscription" in operation.lower():
            self.subscription_times.append(duration)
        elif "reconnect" in operation.lower():
            self.reconnection_times.append(duration)
        
        return duration
    
    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        def calc_stats(times: List[float]) -> Dict[str, float]:
            if not times:
                return {"count": 0, "avg": 0.0, "min": 0.0, "max": 0.0}
            
            return {
                "count": len(times),
                "avg": sum(times) / len(times),
                "min": min(times),
                "max": max(times)
            }
        
        return {
            "connection": calc_stats(self.connection_times),
            "message_send": calc_stats(self.message_send_times),
            "message_receive": calc_stats(self.message_receive_times),
            "subscription": calc_stats(self.subscription_times),
            "reconnection": calc_stats(self.reconnection_times)
        }
    
    def assert_performance_requirements(self, max_connection_time: float = 5.0,
                                        max_message_time: float = 1.0,
                                        max_subscription_time: float = 2.0) -> None:
        """Assert that performance requirements are met."""
        stats = self.get_stats()
        
        if stats["connection"]["avg"] > max_connection_time:
            raise AssertionError(
                f"Average connection time {stats['connection']['avg']:.2f}s "
                f"exceeds maximum {max_connection_time}s"
            )
        
        if stats["message_send"]["avg"] > max_message_time:
            raise AssertionError(
                f"Average message send time {stats['message_send']['avg']:.2f}s "
                f"exceeds maximum {max_message_time}s"
            )
        
        if stats["subscription"]["avg"] > max_subscription_time:
            raise AssertionError(
                f"Average subscription time {stats['subscription']['avg']:.2f}s "
                f"exceeds maximum {max_subscription_time}s"
            )


# Export commonly used fixtures and utilities
__all__ = [
    'MockWebSocketMessage',
    'WebSocketMessageBuilder', 
    'MockWebSocketConnection',
    'WebSocketTestServer',
    'WebSocketTestFixtures',
    'WebSocketEventSequence',
    'WebSocketPerformanceRecorder'
]


# Pytest fixtures (can be imported in conftest.py)
@pytest.fixture
def mock_websocket_connection():
    """Mock WebSocket connection fixture."""
    return MockWebSocketConnection()


@pytest.fixture
def websocket_message_builder():
    """WebSocket message builder fixture."""
    return WebSocketMessageBuilder()


@pytest.fixture
async def websocket_test_server():
    """WebSocket test server fixture."""
    server = WebSocketTestServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def websocket_performance_recorder():
    """WebSocket performance recorder fixture."""
    return WebSocketPerformanceRecorder()


@pytest.fixture
def test_streamers():
    """Test streamer data fixture."""
    return [
        {"id": 12345, "username": "teststreamer1", "channel_id": 12345},
        {"id": 67890, "username": "teststreamer2", "channel_id": 67890},
        {"id": 11111, "username": "teststreamer3", "channel_id": 11111},
    ]