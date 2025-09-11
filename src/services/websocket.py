"""
WebSocket service with live Kick.com Pusher connection.

Handles WebSocket connections to Kick.com's Pusher service for real-time
streamer status updates with automatic reconnection and event processing.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, Callable, List, Set
from urllib.parse import urljoin

import aiohttp
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed, InvalidURI, InvalidHandshake
from pydantic import BaseModel, Field

from .auth import KickOAuthService

logger = logging.getLogger(__name__)


class WebSocketError(Exception):
    """Base exception for WebSocket operations."""
    pass


class ConnectionError(WebSocketError):
    """WebSocket connection related errors."""
    pass


class PusherError(WebSocketError):
    """Pusher protocol related errors."""
    pass


class PusherEventType(str, Enum):
    """Pusher event types."""
    CONNECTION_ESTABLISHED = "pusher:connection_established"
    SUBSCRIBE = "pusher:subscribe"
    SUBSCRIPTION_SUCCEEDED = "pusher:subscription_succeeded"
    SUBSCRIPTION_ERROR = "pusher:subscription_error"
    PING = "pusher:ping"
    PONG = "pusher:pong"
    ERROR = "pusher:error"
    
    # Kick.com specific events
    STREAMER_IS_LIVE = "StreamerIsLive"
    STREAMER_OFFLINE = "StreamerOffline" 
    CHANNEL_UPDATE = "ChannelUpdated"


class PusherEvent(BaseModel):
    """Pusher event message model."""
    
    event: str = Field(..., description="Event type")
    data: Optional[Dict[str, Any]] = Field(None, description="Event data")
    channel: Optional[str] = Field(None, description="Channel name")
    
    @classmethod
    def from_json(cls, message: str) -> 'PusherEvent':
        """Parse Pusher event from JSON message."""
        try:
            data = json.loads(message)
            return cls(**data)
        except (json.JSONDecodeError, ValueError) as e:
            raise PusherError(f"Invalid Pusher event format: {e}") from e
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return self.json(exclude_none=True)


class PusherConfig:
    """Configuration for Pusher WebSocket connection."""
    
    def __init__(
        self,
        app_key: str,
        cluster: str = "us2",
        pusher_url: Optional[str] = None,
        auth_endpoint: Optional[str] = None,
        protocol: int = 7,
        client_name: str = "kick-monitor",
        version: str = "1.0.0",
        reconnect_interval: int = 5,
        max_reconnect_attempts: int = 10,
        ping_interval: int = 30,
        pong_timeout: int = 10
    ):
        self.app_key = app_key
        self.cluster = cluster
        self.pusher_url = pusher_url or f"wss://ws-{cluster}.pusherapp.com/app/{app_key}?protocol={protocol}&client={client_name}&version={version}"
        self.auth_endpoint = auth_endpoint
        self.protocol = protocol
        self.client_name = client_name
        self.version = version
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout


class StreamerChannel:
    """Represents a Kick.com streamer channel subscription."""
    
    def __init__(self, username: str, kick_user_id: str):
        self.username = username
        self.kick_user_id = kick_user_id
        self.channel_name = f"channel.{kick_user_id}"
        self.is_subscribed = False
        self.subscription_time: Optional[datetime] = None
    
    def __repr__(self) -> str:
        return f"<StreamerChannel(username='{self.username}', channel='{self.channel_name}', subscribed={self.is_subscribed})>"


class KickWebSocketService:
    """
    WebSocket service for Kick.com Pusher connections.
    
    Manages WebSocket connection to Kick.com's Pusher service with automatic
    reconnection, channel subscriptions, and event handling.
    """
    
    def __init__(
        self,
        pusher_config: PusherConfig,
        oauth_service: Optional[KickOAuthService] = None
    ):
        self.config = pusher_config
        self.oauth_service = oauth_service
        
        # Connection state
        self._websocket: Optional[WebSocketClientProtocol] = None
        self._socket_id: Optional[str] = None
        self._is_connected = False
        self._is_connecting = False
        
        # Subscriptions
        self._channels: Dict[str, StreamerChannel] = {}
        self._subscribed_channels: Set[str] = set()
        
        # Event handling
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()
        
        # Connection management
        self._reconnect_attempts = 0
        self._last_ping: Optional[datetime] = None
        self._last_pong: Optional[datetime] = None
        self._connection_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._message_handler_task: Optional[asyncio.Task] = None
        
        # Statistics
        self._total_messages_received = 0
        self._total_events_processed = 0
        self._connection_start_time: Optional[datetime] = None
    
    async def start(self) -> None:
        """Start the WebSocket service."""
        if self._is_connected or self._is_connecting:
            logger.warning("WebSocket service already started or connecting")
            return
        
        logger.info("Starting WebSocket service")
        
        # Start connection task
        self._connection_task = asyncio.create_task(self._connection_manager())
        
        # Start message handler
        self._message_handler_task = asyncio.create_task(self._message_handler())
        
        logger.info("WebSocket service started")
    
    async def stop(self) -> None:
        """Stop the WebSocket service."""
        logger.info("Stopping WebSocket service")
        
        # Cancel tasks
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        
        if self._message_handler_task:
            self._message_handler_task.cancel()
            try:
                await self._message_handler_task
            except asyncio.CancelledError:
                pass
        
        # Close connection
        await self._disconnect()
        
        logger.info("WebSocket service stopped")
    
    async def _connection_manager(self) -> None:
        """Manage WebSocket connection with automatic reconnection."""
        while True:
            try:
                if not self._is_connected:
                    await self._connect()
                
                # Wait for connection to close or error
                if self._websocket:
                    await self._websocket.wait_closed()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Connection manager error: {e}")
            
            finally:
                self._is_connected = False
                self._is_connecting = False
                
                # Stop ping task
                if self._ping_task:
                    self._ping_task.cancel()
                    try:
                        await self._ping_task
                    except asyncio.CancelledError:
                        pass
                    self._ping_task = None
            
            # Reconnection logic
            if self._reconnect_attempts < self.config.max_reconnect_attempts:
                self._reconnect_attempts += 1
                wait_time = min(self.config.reconnect_interval * self._reconnect_attempts, 60)
                logger.info(f"Reconnecting in {wait_time} seconds (attempt {self._reconnect_attempts}/{self.config.max_reconnect_attempts})")
                
                try:
                    await asyncio.sleep(wait_time)
                except asyncio.CancelledError:
                    break
            else:
                logger.error("Maximum reconnection attempts reached")
                break
    
    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._is_connected or self._is_connecting:
            return
        
        self._is_connecting = True
        logger.info(f"Connecting to Kick.com WebSocket: {self.config.pusher_url}")
        
        try:
            self._websocket = await websockets.connect(
                self.config.pusher_url,
                extra_headers={
                    "User-Agent": f"{self.config.client_name}/{self.config.version}"
                },
                ping_interval=None,  # We handle ping/pong manually
                ping_timeout=None,
                close_timeout=10
            )
            
            self._is_connected = True
            self._is_connecting = False
            self._connection_start_time = datetime.now(timezone.utc)
            self._reconnect_attempts = 0
            
            logger.info("WebSocket connected successfully")
            
            # Start message receiving
            asyncio.create_task(self._receive_messages())
            
            # Start ping task
            self._ping_task = asyncio.create_task(self._ping_handler())
            
        except (InvalidURI, InvalidHandshake, OSError) as e:
            self._is_connecting = False
            logger.error(f"WebSocket connection failed: {e}")
            raise ConnectionError(f"Failed to connect to WebSocket: {e}") from e
    
    async def _disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._websocket and not self._websocket.closed:
            logger.info("Closing WebSocket connection")
            await self._websocket.close()
        
        self._websocket = None
        self._socket_id = None
        self._is_connected = False
        self._subscribed_channels.clear()
        
        # Reset channel subscription states
        for channel in self._channels.values():
            channel.is_subscribed = False
            channel.subscription_time = None
    
    async def _receive_messages(self) -> None:
        """Receive messages from WebSocket."""
        try:
            async for message in self._websocket:
                self._total_messages_received += 1
                await self._message_queue.put(message)
        
        except ConnectionClosed:
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error receiving WebSocket messages: {e}")
    
    async def _message_handler(self) -> None:
        """Handle incoming WebSocket messages."""
        while True:
            try:
                # Get message from queue
                message = await self._message_queue.get()
                
                # Parse Pusher event
                try:
                    event = PusherEvent.from_json(message)
                    await self._handle_pusher_event(event)
                    self._total_events_processed += 1
                
                except PusherError as e:
                    logger.warning(f"Failed to parse Pusher event: {e}")
                    logger.debug(f"Raw message: {message}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Message handler error: {e}")
    
    async def _handle_pusher_event(self, event: PusherEvent) -> None:
        """Handle Pusher protocol events."""
        logger.debug(f"Received event: {event.event} on channel: {event.channel}")
        
        # Handle Pusher system events
        if event.event == PusherEventType.CONNECTION_ESTABLISHED:
            await self._handle_connection_established(event)
        
        elif event.event == PusherEventType.SUBSCRIPTION_SUCCEEDED:
            await self._handle_subscription_succeeded(event)
        
        elif event.event == PusherEventType.SUBSCRIPTION_ERROR:
            await self._handle_subscription_error(event)
        
        elif event.event == PusherEventType.PING:
            await self._handle_ping()
        
        elif event.event == PusherEventType.PONG:
            await self._handle_pong()
        
        elif event.event == PusherEventType.ERROR:
            await self._handle_error(event)
        
        # Handle Kick.com events
        elif event.event in [PusherEventType.STREAMER_IS_LIVE, PusherEventType.STREAMER_OFFLINE]:
            await self._handle_streamer_event(event)
        
        # Call registered event handlers
        await self._call_event_handlers(event)
    
    async def _handle_connection_established(self, event: PusherEvent) -> None:
        """Handle connection established event."""
        if event.data and "socket_id" in event.data:
            self._socket_id = event.data["socket_id"]
            logger.info(f"Connection established with socket ID: {self._socket_id}")
            
            # Resubscribe to channels
            await self._resubscribe_channels()
        else:
            logger.warning("Connection established event missing socket_id")
    
    async def _handle_subscription_succeeded(self, event: PusherEvent) -> None:
        """Handle subscription succeeded event."""
        if event.channel:
            self._subscribed_channels.add(event.channel)
            
            # Update channel state
            for channel in self._channels.values():
                if channel.channel_name == event.channel:
                    channel.is_subscribed = True
                    channel.subscription_time = datetime.now(timezone.utc)
                    break
            
            logger.info(f"Successfully subscribed to channel: {event.channel}")
    
    async def _handle_subscription_error(self, event: PusherEvent) -> None:
        """Handle subscription error event."""
        logger.error(f"Subscription error for channel {event.channel}: {event.data}")
    
    async def _handle_ping(self) -> None:
        """Handle ping from server."""
        await self._send_pong()
    
    async def _handle_pong(self) -> None:
        """Handle pong from server."""
        self._last_pong = datetime.now(timezone.utc)
    
    async def _handle_error(self, event: PusherEvent) -> None:
        """Handle error event."""
        logger.error(f"Pusher error: {event.data}")
    
    async def _handle_streamer_event(self, event: PusherEvent) -> None:
        """Handle streamer status events."""
        logger.info(f"Streamer event: {event.event} on {event.channel}")
        
        # Extract streamer info from channel name
        if event.channel and event.channel.startswith("channel."):
            kick_user_id = event.channel.split(".", 1)[1]
            
            # Find corresponding streamer
            streamer = None
            for channel in self._channels.values():
                if channel.kick_user_id == kick_user_id:
                    streamer = channel
                    break
            
            if streamer:
                logger.info(f"Status event for {streamer.username}: {event.event}")
            else:
                logger.warning(f"Received event for unknown streamer ID: {kick_user_id}")
    
    async def _ping_handler(self) -> None:
        """Handle periodic ping/pong."""
        while True:
            try:
                await asyncio.sleep(self.config.ping_interval)
                
                if self._is_connected:
                    await self._send_ping()
                    
                    # Check for pong timeout
                    if self._last_ping and self._last_pong:
                        if self._last_ping > self._last_pong:
                            time_since_ping = datetime.now(timezone.utc) - self._last_ping
                            if time_since_ping.total_seconds() > self.config.pong_timeout:
                                logger.warning("Pong timeout - connection may be stale")
                                await self._disconnect()
                                break
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ping handler error: {e}")
    
    async def _send_ping(self) -> None:
        """Send ping to server."""
        if self._websocket and not self._websocket.closed:
            ping_event = PusherEvent(event=PusherEventType.PING)
            await self._websocket.send(ping_event.to_json())
            self._last_ping = datetime.now(timezone.utc)
    
    async def _send_pong(self) -> None:
        """Send pong to server."""
        if self._websocket and not self._websocket.closed:
            pong_event = PusherEvent(event=PusherEventType.PONG)
            await self._websocket.send(pong_event.to_json())
    
    async def subscribe_to_streamer(self, username: str, kick_user_id: str) -> None:
        """Subscribe to streamer status updates."""
        channel = StreamerChannel(username, kick_user_id)
        self._channels[username] = channel
        
        if self._is_connected:
            await self._subscribe_to_channel(channel)
        
        logger.info(f"Added subscription for streamer: {username}")
    
    async def unsubscribe_from_streamer(self, username: str) -> None:
        """Unsubscribe from streamer status updates."""
        if username in self._channels:
            channel = self._channels[username]
            
            if self._is_connected and channel.is_subscribed:
                await self._unsubscribe_from_channel(channel)
            
            del self._channels[username]
            logger.info(f"Removed subscription for streamer: {username}")
    
    async def _subscribe_to_channel(self, channel: StreamerChannel) -> None:
        """Subscribe to a specific channel."""
        if not self._websocket or self._websocket.closed:
            return
        
        subscribe_event = PusherEvent(
            event=PusherEventType.SUBSCRIBE,
            data={"channel": channel.channel_name}
        )
        
        await self._websocket.send(subscribe_event.to_json())
        logger.debug(f"Sent subscription request for: {channel.channel_name}")
    
    async def _unsubscribe_from_channel(self, channel: StreamerChannel) -> None:
        """Unsubscribe from a specific channel."""
        if not self._websocket or self._websocket.closed:
            return
        
        # Note: Pusher doesn't have a standard unsubscribe event
        # We just remove from our tracking
        if channel.channel_name in self._subscribed_channels:
            self._subscribed_channels.remove(channel.channel_name)
        
        channel.is_subscribed = False
        channel.subscription_time = None
    
    async def _resubscribe_channels(self) -> None:
        """Resubscribe to all channels after reconnection."""
        for channel in self._channels.values():
            await self._subscribe_to_channel(channel)
    
    def add_event_handler(self, event_type: str, handler: Callable) -> None:
        """Add event handler for specific event type."""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        
        self._event_handlers[event_type].append(handler)
        logger.debug(f"Added event handler for: {event_type}")
    
    def remove_event_handler(self, event_type: str, handler: Callable) -> None:
        """Remove event handler."""
        if event_type in self._event_handlers:
            try:
                self._event_handlers[event_type].remove(handler)
            except ValueError:
                pass
    
    async def _call_event_handlers(self, event: PusherEvent) -> None:
        """Call registered event handlers."""
        handlers = self._event_handlers.get(event.event, [])
        
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")
    
    def get_connection_status(self) -> Dict[str, Any]:
        """Get connection status information."""
        uptime = None
        if self._connection_start_time:
            uptime = (datetime.now(timezone.utc) - self._connection_start_time).total_seconds()
        
        return {
            "is_connected": self._is_connected,
            "is_connecting": self._is_connecting,
            "socket_id": self._socket_id,
            "uptime_seconds": uptime,
            "reconnect_attempts": self._reconnect_attempts,
            "subscribed_channels": len(self._subscribed_channels),
            "total_channels": len(self._channels),
            "total_messages": self._total_messages_received,
            "total_events": self._total_events_processed
        }
    
    def get_channel_status(self) -> List[Dict[str, Any]]:
        """Get status of all subscribed channels."""
        channels = []
        
        for channel in self._channels.values():
            channels.append({
                "username": channel.username,
                "kick_user_id": channel.kick_user_id,
                "channel_name": channel.channel_name,
                "is_subscribed": channel.is_subscribed,
                "subscription_time": channel.subscription_time.isoformat() if channel.subscription_time else None
            })
        
        return channels