"""
Services package for Kick Streamer Status Monitor.

This package contains all service classes that handle external integrations
and core business logic.
"""

from .database import (
    DatabaseService,
    DatabaseConfig,
    DatabaseError,
    ConnectionError as DatabaseConnectionError,
    TransactionError,
)

from .auth import (
    KickOAuthService,
    OAuthConfig,
    TokenResponse,
    AuthenticationError,
    TokenExpiredError,
    InvalidCredentialsError,
    RateLimitError,
)

from .websocket import (
    KickWebSocketService,
    PusherConfig,
    PusherEvent,
    PusherEventType,
    StreamerChannel,
    WebSocketError,
    ConnectionError as WebSocketConnectionError,
    PusherError,
)

from .monitor import (
    KickMonitorService,
    StreamerMonitorState,
    MonitoringMode,
    MonitoringError,
    ServiceNotStartedError,
)

__all__ = [
    # Database service
    "DatabaseService",
    "DatabaseConfig", 
    "DatabaseError",
    "DatabaseConnectionError",
    "TransactionError",
    
    # OAuth authentication service
    "KickOAuthService",
    "OAuthConfig",
    "TokenResponse",
    "AuthenticationError",
    "TokenExpiredError",
    "InvalidCredentialsError",
    "RateLimitError",
    
    # WebSocket service
    "KickWebSocketService",
    "PusherConfig",
    "PusherEvent",
    "PusherEventType",
    "StreamerChannel",
    "WebSocketError",
    "WebSocketConnectionError",
    "PusherError",
    
    # Main monitoring service
    "KickMonitorService",
    "StreamerMonitorState",
    "MonitoringMode",
    "MonitoringError",
    "ServiceNotStartedError",
]