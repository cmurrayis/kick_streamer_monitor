"""
Error handling and retry logic with exponential backoff.

Provides comprehensive error handling, retry mechanisms, and resilience
patterns for the Kick streamer monitoring application.
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timezone, timedelta
from typing import (
    Any, Callable, Dict, List, Optional, Type, Union, TypeVar, Generic,
    Awaitable, Tuple
)
from enum import Enum
from dataclasses import dataclass, field
from functools import wraps
import traceback

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ErrorSeverity(str, Enum):
    """Error severity levels."""
    LOW = "low"           # Minor issues, service can continue
    MEDIUM = "medium"     # Moderate issues, some functionality affected
    HIGH = "high"         # Major issues, service functionality impacted
    CRITICAL = "critical" # Critical issues, service may need to stop


class ErrorCategory(str, Enum):
    """Error categories for classification."""
    NETWORK = "network"             # Network connectivity issues
    DATABASE = "database"           # Database connection/query issues
    AUTHENTICATION = "authentication"  # OAuth/auth related issues
    WEBSOCKET = "websocket"         # WebSocket connection issues
    VALIDATION = "validation"       # Data validation errors
    CONFIGURATION = "configuration"  # Configuration issues
    RATE_LIMIT = "rate_limit"       # API rate limiting
    EXTERNAL_API = "external_api"   # External API issues
    INTERNAL = "internal"           # Internal application errors


class RetryPolicy(str, Enum):
    """Retry policy types."""
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    LINEAR_BACKOFF = "linear_backoff"
    FIXED_INTERVAL = "fixed_interval"
    NO_RETRY = "no_retry"


@dataclass
class ErrorContext:
    """Context information for errors."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    operation: Optional[str] = None
    component: Optional[str] = None
    user_id: Optional[str] = None
    streamer_id: Optional[int] = None
    additional_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'operation': self.operation,
            'component': self.component,
            'user_id': self.user_id,
            'streamer_id': self.streamer_id,
            **self.additional_data
        }


@dataclass
class RetryConfig:
    """Configuration for retry logic."""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    retry_on: Tuple[Type[Exception], ...] = (Exception,)
    dont_retry_on: Tuple[Type[Exception], ...] = ()
    policy: RetryPolicy = RetryPolicy.EXPONENTIAL_BACKOFF
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number."""
        if self.policy == RetryPolicy.NO_RETRY:
            return 0.0
        
        if self.policy == RetryPolicy.FIXED_INTERVAL:
            delay = self.base_delay
        elif self.policy == RetryPolicy.LINEAR_BACKOFF:
            delay = self.base_delay * attempt
        else:  # EXPONENTIAL_BACKOFF
            delay = self.base_delay * (self.backoff_multiplier ** (attempt - 1))
        
        # Apply maximum delay limit
        delay = min(delay, self.max_delay)
        
        # Add jitter to prevent thundering herd
        if self.jitter:
            jitter_range = delay * 0.1  # 10% jitter
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)
    
    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """Check if should retry based on exception and attempt count."""
        if attempt >= self.max_attempts:
            return False
        
        if self.policy == RetryPolicy.NO_RETRY:
            return False
        
        # Check if exception is in dont_retry_on list
        if any(isinstance(exception, exc_type) for exc_type in self.dont_retry_on):
            return False
        
        # Check if exception is in retry_on list
        return any(isinstance(exception, exc_type) for exc_type in self.retry_on)


class MonitoringError(Exception):
    """Base exception for monitoring-related errors."""
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.INTERNAL,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[ErrorContext] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.context = context or ErrorContext()
        self.cause = cause
        self.timestamp = datetime.now(timezone.utc)


class NetworkError(MonitoringError):
    """Network-related errors."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )


class DatabaseError(MonitoringError):
    """Database-related errors."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.DATABASE,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )


class AuthenticationError(MonitoringError):
    """Authentication-related errors."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.AUTHENTICATION,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )


class WebSocketError(MonitoringError):
    """WebSocket-related errors."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.WEBSOCKET,
            severity=ErrorSeverity.MEDIUM,
            **kwargs
        )


class RateLimitError(MonitoringError):
    """Rate limiting errors."""
    
    def __init__(self, message: str, retry_after: Optional[int] = None, **kwargs):
        super().__init__(
            message,
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            **kwargs
        )
        self.retry_after = retry_after


class ErrorTracker:
    """Tracks errors and provides statistics."""
    
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._error_history: List[MonitoringError] = []
        self._error_counts: Dict[str, int] = {}
        self._last_errors: Dict[str, datetime] = {}
    
    def record_error(self, error: MonitoringError) -> None:
        """Record an error in the tracker."""
        # Add to history
        self._error_history.append(error)
        
        # Maintain max history size
        if len(self._error_history) > self.max_history:
            self._error_history = self._error_history[-self.max_history:]
        
        # Update counts
        error_key = f"{error.category}:{type(error).__name__}"
        self._error_counts[error_key] = self._error_counts.get(error_key, 0) + 1
        self._last_errors[error_key] = error.timestamp
        
        # Log the error
        logger.error(
            f"Error recorded: {error.message}",
            extra={
                'error_category': error.category,
                'error_severity': error.severity.value,
                'error_context': error.context.to_dict() if error.context else {}
            }
        )
    
    def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics."""
        now = datetime.now(timezone.utc)
        
        # Count errors by time periods
        last_hour = now - timedelta(hours=1)
        last_day = now - timedelta(days=1)
        
        recent_errors = [e for e in self._error_history if e.timestamp >= last_hour]
        daily_errors = [e for e in self._error_history if e.timestamp >= last_day]
        
        return {
            'total_errors': len(self._error_history),
            'errors_last_hour': len(recent_errors),
            'errors_last_day': len(daily_errors),
            'error_counts_by_type': self._error_counts.copy(),
            'most_recent_errors': [
                {
                    'type': error_type,
                    'last_occurred': last_time.isoformat(),
                    'count': self._error_counts[error_type]
                }
                for error_type, last_time in sorted(
                    self._last_errors.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:10]
            ]
        }
    
    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent errors as dictionaries."""
        recent = self._error_history[-limit:] if self._error_history else []
        
        return [
            {
                'message': error.message,
                'category': error.category,
                'severity': error.severity.value,
                'timestamp': error.timestamp.isoformat(),
                'context': error.context.to_dict() if error.context else {}
            }
            for error in reversed(recent)
        ]
    
    def clear_history(self) -> None:
        """Clear error history."""
        self._error_history.clear()
        self._error_counts.clear()
        self._last_errors.clear()


class CircuitBreaker:
    """Circuit breaker pattern implementation."""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._state = "closed"  # closed, open, half-open
    
    async def call(self, func: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        """Execute function with circuit breaker protection."""
        if self._state == "open":
            if self._should_attempt_reset():
                self._state = "half-open"
            else:
                raise MonitoringError(
                    "Circuit breaker is OPEN",
                    category=ErrorCategory.INTERNAL,
                    severity=ErrorSeverity.HIGH
                )
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        
        except self.expected_exception as e:
            self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Check if should attempt to reset the circuit breaker."""
        if not self._last_failure_time:
            return True
        
        return datetime.now(timezone.utc) >= self._last_failure_time + timedelta(seconds=self.recovery_timeout)
    
    def _on_success(self) -> None:
        """Handle successful execution."""
        self._failure_count = 0
        self._state = "closed"
    
    def _on_failure(self) -> None:
        """Handle failed execution."""
        self._failure_count += 1
        self._last_failure_time = datetime.now(timezone.utc)
        
        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.warning(f"Circuit breaker opened after {self._failure_count} failures")
    
    @property
    def state(self) -> str:
        """Get current circuit breaker state."""
        return self._state
    
    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        return self._failure_count


def retry_with_backoff(
    config: Optional[RetryConfig] = None,
    error_tracker: Optional[ErrorTracker] = None
):
    """Decorator for adding retry logic with exponential backoff."""
    
    if config is None:
        config = RetryConfig()
    
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(1, config.max_attempts + 1):
                try:
                    result = await func(*args, **kwargs)
                    
                    # Log successful retry if not first attempt
                    if attempt > 1:
                        logger.info(f"Operation succeeded on attempt {attempt}")
                    
                    return result
                
                except Exception as e:
                    last_exception = e
                    
                    # Check if we should retry
                    if not config.should_retry(e, attempt):
                        break
                    
                    # Calculate delay
                    delay = config.calculate_delay(attempt)
                    
                    # Log retry attempt
                    logger.warning(
                        f"Operation failed on attempt {attempt}/{config.max_attempts}: {e}. "
                        f"Retrying in {delay:.2f} seconds..."
                    )
                    
                    # Record error if tracker provided
                    if error_tracker and isinstance(e, MonitoringError):
                        error_tracker.record_error(e)
                    
                    # Wait before retry
                    if delay > 0:
                        await asyncio.sleep(delay)
            
            # All retries exhausted
            if last_exception:
                logger.error(f"Operation failed after {config.max_attempts} attempts: {last_exception}")
                raise last_exception
            
            # This shouldn't happen, but just in case
            raise MonitoringError("Retry logic failed unexpectedly")
        
        return wrapper
    return decorator


class ErrorHandler:
    """Centralized error handling system."""
    
    def __init__(self):
        self.error_tracker = ErrorTracker()
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._handlers: Dict[Type[Exception], List[Callable]] = {}
    
    def register_handler(self, exception_type: Type[Exception], handler: Callable) -> None:
        """Register error handler for specific exception type."""
        if exception_type not in self._handlers:
            self._handlers[exception_type] = []
        self._handlers[exception_type].append(handler)
    
    def get_circuit_breaker(self, name: str, **kwargs) -> CircuitBreaker:
        """Get or create circuit breaker by name."""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(**kwargs)
        return self._circuit_breakers[name]
    
    async def handle_error(self, error: Exception, context: Optional[ErrorContext] = None) -> None:
        """Handle error with registered handlers."""
        # Convert to MonitoringError if needed
        if not isinstance(error, MonitoringError):
            monitoring_error = MonitoringError(
                str(error),
                context=context,
                cause=error
            )
        else:
            monitoring_error = error
        
        # Record error
        self.error_tracker.record_error(monitoring_error)
        
        # Call registered handlers
        for exc_type, handlers in self._handlers.items():
            if isinstance(error, exc_type):
                for handler in handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            await handler(monitoring_error)
                        else:
                            handler(monitoring_error)
                    except Exception as handler_error:
                        logger.error(f"Error handler failed: {handler_error}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive error statistics."""
        return {
            'error_tracker': self.error_tracker.get_error_stats(),
            'circuit_breakers': {
                name: {
                    'state': cb.state,
                    'failure_count': cb.failure_count
                }
                for name, cb in self._circuit_breakers.items()
            }
        }


# Global error handler instance
_global_error_handler: Optional[ErrorHandler] = None


def get_error_handler() -> ErrorHandler:
    """Get global error handler instance."""
    global _global_error_handler
    if _global_error_handler is None:
        _global_error_handler = ErrorHandler()
    return _global_error_handler


def create_retry_config(
    category: ErrorCategory,
    severity: ErrorSeverity
) -> RetryConfig:
    """Create retry configuration based on error category and severity."""
    
    if category == ErrorCategory.NETWORK:
        return RetryConfig(
            max_attempts=5,
            base_delay=2.0,
            max_delay=30.0,
            retry_on=(NetworkError, ConnectionError, TimeoutError)
        )
    
    elif category == ErrorCategory.DATABASE:
        return RetryConfig(
            max_attempts=3,
            base_delay=1.0,
            max_delay=10.0,
            retry_on=(DatabaseError,)
        )
    
    elif category == ErrorCategory.AUTHENTICATION:
        return RetryConfig(
            max_attempts=2,
            base_delay=5.0,
            max_delay=15.0,
            retry_on=(AuthenticationError,),
            dont_retry_on=(AuthenticationError,) if severity == ErrorSeverity.CRITICAL else ()
        )
    
    elif category == ErrorCategory.WEBSOCKET:
        return RetryConfig(
            max_attempts=10,
            base_delay=1.0,
            max_delay=60.0,
            retry_on=(WebSocketError, ConnectionError)
        )
    
    elif category == ErrorCategory.RATE_LIMIT:
        return RetryConfig(
            max_attempts=3,
            base_delay=10.0,
            max_delay=120.0,
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            retry_on=(RateLimitError,)
        )
    
    else:
        # Default configuration
        return RetryConfig(
            max_attempts=3,
            base_delay=1.0,
            max_delay=30.0
        )