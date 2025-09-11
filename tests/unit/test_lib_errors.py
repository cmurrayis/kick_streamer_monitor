"""
Unit tests for error handling and retry logic system.

Tests cover error categorization, retry mechanisms, circuit breaker patterns,
and comprehensive error tracking functionality.
"""

import pytest
import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch

from src.lib.errors import (
    ErrorSeverity,
    ErrorCategory,
    RetryPolicy,
    ErrorContext,
    RetryConfig,
    MonitoringError,
    NetworkError,
    DatabaseError,
    AuthenticationError,
    WebSocketError,
    RateLimitError,
    ErrorTracker,
    CircuitBreaker,
    ErrorHandler,
    retry_with_backoff,
    get_error_handler,
    create_retry_config,
)


class TestErrorSeverity:
    """Test ErrorSeverity enum."""
    
    def test_severity_values(self):
        """Test that all severity values are correct."""
        assert ErrorSeverity.LOW == "low"
        assert ErrorSeverity.MEDIUM == "medium"
        assert ErrorSeverity.HIGH == "high"
        assert ErrorSeverity.CRITICAL == "critical"


class TestErrorCategory:
    """Test ErrorCategory enum."""
    
    def test_category_values(self):
        """Test that all category values are correct."""
        assert ErrorCategory.NETWORK == "network"
        assert ErrorCategory.DATABASE == "database"
        assert ErrorCategory.AUTHENTICATION == "authentication"
        assert ErrorCategory.WEBSOCKET == "websocket"
        assert ErrorCategory.VALIDATION == "validation"
        assert ErrorCategory.CONFIGURATION == "configuration"
        assert ErrorCategory.RATE_LIMIT == "rate_limit"
        assert ErrorCategory.EXTERNAL_API == "external_api"
        assert ErrorCategory.INTERNAL == "internal"


class TestRetryPolicy:
    """Test RetryPolicy enum."""
    
    def test_policy_values(self):
        """Test that all policy values are correct."""
        assert RetryPolicy.EXPONENTIAL_BACKOFF == "exponential_backoff"
        assert RetryPolicy.LINEAR_BACKOFF == "linear_backoff"
        assert RetryPolicy.FIXED_INTERVAL == "fixed_interval"
        assert RetryPolicy.NO_RETRY == "no_retry"


class TestErrorContext:
    """Test ErrorContext data class."""
    
    def test_context_creation(self):
        """Test creating error context."""
        context = ErrorContext(
            operation="test_operation",
            component="test_component",
            user_id="user123",
            streamer_id=456
        )
        
        assert context.operation == "test_operation"
        assert context.component == "test_component"
        assert context.user_id == "user123"
        assert context.streamer_id == 456
        assert isinstance(context.timestamp, datetime)
    
    def test_context_to_dict(self):
        """Test converting context to dictionary."""
        context = ErrorContext(
            operation="test_op",
            additional_data={"key": "value"}
        )
        
        data = context.to_dict()
        assert data['operation'] == "test_op"
        assert data['key'] == "value"
        assert 'timestamp' in data
        assert isinstance(data['timestamp'], str)


class TestRetryConfig:
    """Test RetryConfig data class."""
    
    def test_default_config(self):
        """Test default retry configuration."""
        config = RetryConfig()
        
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.backoff_multiplier == 2.0
        assert config.jitter is True
        assert config.policy == RetryPolicy.EXPONENTIAL_BACKOFF
    
    def test_calculate_exponential_delay(self):
        """Test exponential backoff delay calculation."""
        config = RetryConfig(
            base_delay=2.0,
            backoff_multiplier=2.0,
            jitter=False
        )
        
        assert config.calculate_delay(1) == 2.0
        assert config.calculate_delay(2) == 4.0
        assert config.calculate_delay(3) == 8.0
    
    def test_calculate_linear_delay(self):
        """Test linear backoff delay calculation."""
        config = RetryConfig(
            base_delay=3.0,
            policy=RetryPolicy.LINEAR_BACKOFF,
            jitter=False
        )
        
        assert config.calculate_delay(1) == 3.0
        assert config.calculate_delay(2) == 6.0
        assert config.calculate_delay(3) == 9.0
    
    def test_calculate_fixed_delay(self):
        """Test fixed interval delay calculation."""
        config = RetryConfig(
            base_delay=5.0,
            policy=RetryPolicy.FIXED_INTERVAL,
            jitter=False
        )
        
        assert config.calculate_delay(1) == 5.0
        assert config.calculate_delay(2) == 5.0
        assert config.calculate_delay(3) == 5.0
    
    def test_calculate_no_retry_delay(self):
        """Test no retry policy."""
        config = RetryConfig(policy=RetryPolicy.NO_RETRY)
        assert config.calculate_delay(1) == 0.0
    
    def test_max_delay_limit(self):
        """Test maximum delay limit."""
        config = RetryConfig(
            base_delay=10.0,
            max_delay=20.0,
            backoff_multiplier=3.0,
            jitter=False
        )
        
        # Should be capped at max_delay
        assert config.calculate_delay(5) == 20.0
    
    def test_jitter_application(self):
        """Test jitter is applied to delays."""
        config = RetryConfig(
            base_delay=10.0,
            jitter=True
        )
        
        # With jitter, delays should vary slightly
        delays = [config.calculate_delay(1) for _ in range(10)]
        assert len(set(delays)) > 1  # Should have some variation
        assert all(9.0 <= delay <= 11.0 for delay in delays)  # Within jitter range
    
    def test_should_retry_basic(self):
        """Test basic retry decision logic."""
        config = RetryConfig(max_attempts=3)
        
        assert config.should_retry(Exception(), 1)
        assert config.should_retry(Exception(), 2)
        assert not config.should_retry(Exception(), 3)
        assert not config.should_retry(Exception(), 4)
    
    def test_should_retry_no_retry_policy(self):
        """Test no retry policy."""
        config = RetryConfig(policy=RetryPolicy.NO_RETRY)
        assert not config.should_retry(Exception(), 1)
    
    def test_should_retry_with_exception_types(self):
        """Test retry decision with specific exception types."""
        config = RetryConfig(
            retry_on=(NetworkError,),
            dont_retry_on=(AuthenticationError,)
        )
        
        # Should retry on NetworkError
        assert config.should_retry(NetworkError("test"), 1)
        
        # Should not retry on AuthenticationError
        assert not config.should_retry(AuthenticationError("test"), 1)
        
        # Should not retry on other exceptions
        assert not config.should_retry(ValueError("test"), 1)


class TestMonitoringError:
    """Test MonitoringError and subclasses."""
    
    def test_monitoring_error_creation(self):
        """Test creating monitoring errors."""
        context = ErrorContext(operation="test_op")
        error = MonitoringError(
            "Test error",
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.HIGH,
            context=context
        )
        
        assert str(error) == "Test error"
        assert error.category == ErrorCategory.NETWORK
        assert error.severity == ErrorSeverity.HIGH
        assert error.context == context
        assert isinstance(error.timestamp, datetime)
    
    def test_network_error(self):
        """Test NetworkError default values."""
        error = NetworkError("Connection failed")
        
        assert error.category == ErrorCategory.NETWORK
        assert error.severity == ErrorSeverity.HIGH
        assert "Connection failed" in str(error)
    
    def test_database_error(self):
        """Test DatabaseError default values."""
        error = DatabaseError("Query failed")
        
        assert error.category == ErrorCategory.DATABASE
        assert error.severity == ErrorSeverity.HIGH
    
    def test_authentication_error(self):
        """Test AuthenticationError default values."""
        error = AuthenticationError("Auth failed")
        
        assert error.category == ErrorCategory.AUTHENTICATION
        assert error.severity == ErrorSeverity.HIGH
    
    def test_websocket_error(self):
        """Test WebSocketError default values."""
        error = WebSocketError("WS connection lost")
        
        assert error.category == ErrorCategory.WEBSOCKET
        assert error.severity == ErrorSeverity.MEDIUM
    
    def test_rate_limit_error(self):
        """Test RateLimitError with retry_after."""
        error = RateLimitError("Rate limited", retry_after=60)
        
        assert error.category == ErrorCategory.RATE_LIMIT
        assert error.severity == ErrorSeverity.MEDIUM
        assert error.retry_after == 60


class TestErrorTracker:
    """Test ErrorTracker functionality."""
    
    def test_error_tracker_creation(self):
        """Test creating error tracker."""
        tracker = ErrorTracker(max_history=100)
        assert tracker.max_history == 100
        assert len(tracker._error_history) == 0
    
    def test_record_error(self):
        """Test recording errors."""
        tracker = ErrorTracker()
        error = NetworkError("Test error")
        
        tracker.record_error(error)
        
        assert len(tracker._error_history) == 1
        assert tracker._error_history[0] == error
    
    def test_max_history_limit(self):
        """Test error history size limit."""
        tracker = ErrorTracker(max_history=2)
        
        for i in range(5):
            error = NetworkError(f"Error {i}")
            tracker.record_error(error)
        
        assert len(tracker._error_history) == 2
        assert "Error 3" in str(tracker._error_history[0])
        assert "Error 4" in str(tracker._error_history[1])
    
    def test_error_stats(self):
        """Test getting error statistics."""
        tracker = ErrorTracker()
        
        # Add some errors
        tracker.record_error(NetworkError("Network error 1"))
        tracker.record_error(NetworkError("Network error 2"))
        tracker.record_error(DatabaseError("DB error"))
        
        stats = tracker.get_error_stats()
        
        assert stats['total_errors'] == 3
        assert 'errors_last_hour' in stats
        assert 'errors_last_day' in stats
        assert 'error_counts_by_type' in stats
        assert 'most_recent_errors' in stats
    
    def test_recent_errors(self):
        """Test getting recent errors."""
        tracker = ErrorTracker()
        
        error1 = NetworkError("Error 1")
        error2 = DatabaseError("Error 2")
        tracker.record_error(error1)
        tracker.record_error(error2)
        
        recent = tracker.get_recent_errors(limit=1)
        
        assert len(recent) == 1
        assert recent[0]['message'] == "Error 2"  # Most recent first
    
    def test_clear_history(self):
        """Test clearing error history."""
        tracker = ErrorTracker()
        tracker.record_error(NetworkError("Test error"))
        
        tracker.clear_history()
        
        assert len(tracker._error_history) == 0
        assert len(tracker._error_counts) == 0
        assert len(tracker._last_errors) == 0


class TestCircuitBreaker:
    """Test CircuitBreaker functionality."""
    
    def test_circuit_breaker_creation(self):
        """Test creating circuit breaker."""
        cb = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=30.0,
            expected_exception=NetworkError
        )
        
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 30.0
        assert cb.expected_exception == NetworkError
        assert cb.state == "closed"
        assert cb.failure_count == 0
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_success(self):
        """Test circuit breaker with successful calls."""
        cb = CircuitBreaker()
        
        async def success_func():
            return "success"
        
        result = await cb.call(success_func)
        assert result == "success"
        assert cb.state == "closed"
        assert cb.failure_count == 0
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_failure_accumulation(self):
        """Test circuit breaker failure accumulation."""
        cb = CircuitBreaker(failure_threshold=2)
        
        async def failing_func():
            raise NetworkError("Test failure")
        
        # First failure
        with pytest.raises(NetworkError):
            await cb.call(failing_func)
        assert cb.state == "closed"
        assert cb.failure_count == 1
        
        # Second failure - should open circuit
        with pytest.raises(NetworkError):
            await cb.call(failing_func)
        assert cb.state == "open"
        assert cb.failure_count == 2
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_open_state(self):
        """Test circuit breaker in open state."""
        cb = CircuitBreaker(failure_threshold=1)
        
        async def failing_func():
            raise NetworkError("Test failure")
        
        # Trigger circuit breaker
        with pytest.raises(NetworkError):
            await cb.call(failing_func)
        
        # Should now reject calls with MonitoringError
        with pytest.raises(MonitoringError) as exc_info:
            await cb.call(failing_func)
        assert "Circuit breaker is OPEN" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_recovery(self):
        """Test circuit breaker half-open recovery."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        
        async def failing_func():
            raise NetworkError("Test failure")
        
        async def success_func():
            return "recovered"
        
        # Trigger circuit breaker
        with pytest.raises(NetworkError):
            await cb.call(failing_func)
        assert cb.state == "open"
        
        # Wait for recovery timeout
        await asyncio.sleep(0.2)
        
        # Should allow one call (half-open)
        result = await cb.call(success_func)
        assert result == "recovered"
        assert cb.state == "closed"
        assert cb.failure_count == 0


class TestRetryWithBackoff:
    """Test retry_with_backoff decorator."""
    
    @pytest.mark.asyncio
    async def test_retry_success_first_attempt(self):
        """Test successful function on first attempt."""
        config = RetryConfig(max_attempts=3)
        
        @retry_with_backoff(config)
        async def success_func():
            return "success"
        
        result = await success_func()
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_retry_success_after_failures(self):
        """Test successful function after some failures."""
        config = RetryConfig(max_attempts=3, base_delay=0.01)
        call_count = 0
        
        @retry_with_backoff(config)
        async def intermittent_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise NetworkError("Temporary failure")
            return "success"
        
        result = await intermittent_func()
        assert result == "success"
        assert call_count == 3
    
    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        """Test retry exhaustion."""
        config = RetryConfig(max_attempts=2, base_delay=0.01)
        
        @retry_with_backoff(config)
        async def always_fails():
            raise NetworkError("Always fails")
        
        with pytest.raises(NetworkError):
            await always_fails()
    
    @pytest.mark.asyncio
    async def test_retry_with_non_retryable_exception(self):
        """Test retry with non-retryable exception."""
        config = RetryConfig(
            max_attempts=3,
            retry_on=(NetworkError,),
            dont_retry_on=(AuthenticationError,)
        )
        
        @retry_with_backoff(config)
        async def auth_failure():
            raise AuthenticationError("Auth failed")
        
        with pytest.raises(AuthenticationError):
            await auth_failure()
    
    @pytest.mark.asyncio
    async def test_retry_with_error_tracker(self):
        """Test retry with error tracking."""
        tracker = ErrorTracker()
        config = RetryConfig(max_attempts=2, base_delay=0.01)
        
        @retry_with_backoff(config, tracker)
        async def failing_func():
            raise NetworkError("Test failure")
        
        with pytest.raises(NetworkError):
            await failing_func()
        
        # Should have recorded the error
        assert len(tracker._error_history) > 0


class TestErrorHandler:
    """Test ErrorHandler functionality."""
    
    def test_error_handler_creation(self):
        """Test creating error handler."""
        handler = ErrorHandler()
        
        assert isinstance(handler.error_tracker, ErrorTracker)
        assert len(handler._circuit_breakers) == 0
        assert len(handler._handlers) == 0
    
    def test_register_handler(self):
        """Test registering error handlers."""
        handler = ErrorHandler()
        
        def test_handler(error):
            pass
        
        handler.register_handler(NetworkError, test_handler)
        
        assert NetworkError in handler._handlers
        assert test_handler in handler._handlers[NetworkError]
    
    def test_get_circuit_breaker(self):
        """Test getting circuit breaker by name."""
        handler = ErrorHandler()
        
        cb1 = handler.get_circuit_breaker("test_cb", failure_threshold=5)
        cb2 = handler.get_circuit_breaker("test_cb")
        
        assert cb1 is cb2  # Should return same instance
        assert cb1.failure_threshold == 5
    
    @pytest.mark.asyncio
    async def test_handle_error(self):
        """Test error handling."""
        handler = ErrorHandler()
        handled_errors = []
        
        def test_handler(error):
            handled_errors.append(error)
        
        handler.register_handler(NetworkError, test_handler)
        
        error = NetworkError("Test error")
        await handler.handle_error(error)
        
        assert len(handled_errors) == 1
        assert handled_errors[0] == error
        assert len(handler.error_tracker._error_history) == 1
    
    @pytest.mark.asyncio
    async def test_handle_non_monitoring_error(self):
        """Test handling non-monitoring errors."""
        handler = ErrorHandler()
        
        error = ValueError("Regular error")
        await handler.handle_error(error)
        
        # Should convert to MonitoringError
        recorded_errors = handler.error_tracker._error_history
        assert len(recorded_errors) == 1
        assert isinstance(recorded_errors[0], MonitoringError)
        assert recorded_errors[0].cause == error
    
    def test_get_stats(self):
        """Test getting error handler statistics."""
        handler = ErrorHandler()
        handler.get_circuit_breaker("test_cb")
        
        stats = handler.get_stats()
        
        assert 'error_tracker' in stats
        assert 'circuit_breakers' in stats
        assert 'test_cb' in stats['circuit_breakers']


class TestUtilityFunctions:
    """Test utility functions."""
    
    def test_get_error_handler_singleton(self):
        """Test global error handler singleton."""
        handler1 = get_error_handler()
        handler2 = get_error_handler()
        
        assert handler1 is handler2
        assert isinstance(handler1, ErrorHandler)
    
    def test_create_retry_config_network(self):
        """Test creating retry config for network errors."""
        config = create_retry_config(ErrorCategory.NETWORK, ErrorSeverity.HIGH)
        
        assert config.max_attempts == 5
        assert config.base_delay == 2.0
        assert NetworkError in config.retry_on
    
    def test_create_retry_config_database(self):
        """Test creating retry config for database errors."""
        config = create_retry_config(ErrorCategory.DATABASE, ErrorSeverity.MEDIUM)
        
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert DatabaseError in config.retry_on
    
    def test_create_retry_config_authentication(self):
        """Test creating retry config for authentication errors."""
        config = create_retry_config(ErrorCategory.AUTHENTICATION, ErrorSeverity.HIGH)
        
        assert config.max_attempts == 2
        assert config.base_delay == 5.0
    
    def test_create_retry_config_websocket(self):
        """Test creating retry config for websocket errors."""
        config = create_retry_config(ErrorCategory.WEBSOCKET, ErrorSeverity.MEDIUM)
        
        assert config.max_attempts == 10
        assert config.base_delay == 1.0
        assert WebSocketError in config.retry_on
    
    def test_create_retry_config_rate_limit(self):
        """Test creating retry config for rate limit errors."""
        config = create_retry_config(ErrorCategory.RATE_LIMIT, ErrorSeverity.MEDIUM)
        
        assert config.max_attempts == 3
        assert config.base_delay == 10.0
        assert config.policy == RetryPolicy.EXPONENTIAL_BACKOFF
    
    def test_create_retry_config_default(self):
        """Test creating default retry config."""
        config = create_retry_config(ErrorCategory.INTERNAL, ErrorSeverity.LOW)
        
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 30.0


@pytest.fixture
def sample_error_tracker():
    """Fixture providing a sample error tracker with some data."""
    tracker = ErrorTracker()
    
    # Add some sample errors
    tracker.record_error(NetworkError("Network failure"))
    tracker.record_error(DatabaseError("DB connection lost"))
    tracker.record_error(NetworkError("Another network issue"))
    
    return tracker


class TestErrorTrackerFixture:
    """Test using the error tracker fixture."""
    
    def test_fixture_usage(self, sample_error_tracker):
        """Test that fixture provides valid error tracker."""
        assert len(sample_error_tracker._error_history) == 3
        
        stats = sample_error_tracker.get_error_stats()
        assert stats['total_errors'] == 3
    
    def test_fixture_error_types(self, sample_error_tracker):
        """Test error types in fixture."""
        recent_errors = sample_error_tracker.get_recent_errors(limit=10)
        
        # Should have both network and database errors
        error_types = [error['category'] for error in recent_errors]
        assert ErrorCategory.NETWORK in error_types
        assert ErrorCategory.DATABASE in error_types