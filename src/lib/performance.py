"""
Production-ready performance optimization utilities.

Provides connection pooling, rate limiting, caching, metrics collection,
and performance monitoring for the Kick streamer monitoring system.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Callable, AsyncGenerator, Tuple
import weakref
from functools import wraps
import psutil
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics collection."""
    
    # Connection metrics
    database_connections_active: int = 0
    database_connections_total: int = 0
    websocket_connections_active: int = 0
    websocket_reconnects: int = 0
    
    # Request metrics
    api_requests_total: int = 0
    api_requests_successful: int = 0
    api_requests_failed: int = 0
    api_response_times: deque = field(default_factory=lambda: deque(maxlen=1000))
    
    # Event processing metrics
    events_processed_total: int = 0
    events_processed_per_second: float = 0.0
    event_processing_times: deque = field(default_factory=lambda: deque(maxlen=1000))
    events_queue_size: int = 0
    
    # System metrics
    memory_usage_mb: float = 0.0
    cpu_usage_percent: float = 0.0
    disk_usage_percent: float = 0.0
    
    # Timing metrics
    startup_time: Optional[datetime] = None
    uptime_seconds: float = 0.0
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def update_system_metrics(self) -> None:
        """Update system resource metrics."""
        process = psutil.Process()
        
        # Memory usage
        memory_info = process.memory_info()
        self.memory_usage_mb = memory_info.rss / 1024 / 1024
        
        # CPU usage
        self.cpu_usage_percent = process.cpu_percent()
        
        # Disk usage
        disk_usage = psutil.disk_usage('/')
        self.disk_usage_percent = (disk_usage.used / disk_usage.total) * 100
        
        # Uptime
        if self.startup_time:
            self.uptime_seconds = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
        
        self.last_update = datetime.now(timezone.utc)
    
    def add_api_response_time(self, response_time: float) -> None:
        """Add API response time measurement."""
        self.api_response_times.append(response_time)
    
    def add_event_processing_time(self, processing_time: float) -> None:
        """Add event processing time measurement."""
        self.event_processing_times.append(processing_time)
    
    def get_average_api_response_time(self) -> float:
        """Get average API response time."""
        if not self.api_response_times:
            return 0.0
        return sum(self.api_response_times) / len(self.api_response_times)
    
    def get_average_event_processing_time(self) -> float:
        """Get average event processing time."""
        if not self.event_processing_times:
            return 0.0
        return sum(self.event_processing_times) / len(self.event_processing_times)
    
    def get_api_success_rate(self) -> float:
        """Get API request success rate."""
        if self.api_requests_total == 0:
            return 0.0
        return (self.api_requests_successful / self.api_requests_total) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "connections": {
                "database_active": self.database_connections_active,
                "database_total": self.database_connections_total,
                "websocket_active": self.websocket_connections_active,
                "websocket_reconnects": self.websocket_reconnects
            },
            "api": {
                "requests_total": self.api_requests_total,
                "requests_successful": self.api_requests_successful,
                "requests_failed": self.api_requests_failed,
                "success_rate_percent": self.get_api_success_rate(),
                "average_response_time_ms": self.get_average_api_response_time() * 1000
            },
            "events": {
                "processed_total": self.events_processed_total,
                "processed_per_second": self.events_processed_per_second,
                "average_processing_time_ms": self.get_average_event_processing_time() * 1000,
                "queue_size": self.events_queue_size
            },
            "system": {
                "memory_usage_mb": round(self.memory_usage_mb, 2),
                "cpu_usage_percent": round(self.cpu_usage_percent, 2),
                "disk_usage_percent": round(self.disk_usage_percent, 2),
                "uptime_seconds": round(self.uptime_seconds, 2)
            },
            "timestamp": self.last_update.isoformat()
        }


class RateLimiter:
    """Token bucket rate limiter for API requests."""
    
    def __init__(self, max_requests: int, time_window: float):
        self.max_requests = max_requests
        self.time_window = time_window
        self.tokens = max_requests
        self.last_refill = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> bool:
        """Acquire tokens for rate limiting."""
        async with self._lock:
            now = time.time()
            
            # Refill tokens based on elapsed time
            elapsed = now - self.last_refill
            self.tokens = min(
                self.max_requests,
                self.tokens + (elapsed * self.max_requests / self.time_window)
            )
            self.last_refill = now
            
            # Check if we have enough tokens
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            return False
    
    async def wait_for_token(self, tokens: int = 1) -> None:
        """Wait until tokens are available."""
        while not await self.acquire(tokens):
            await asyncio.sleep(0.1)
    
    def get_status(self) -> Dict[str, Any]:
        """Get rate limiter status."""
        return {
            "max_requests": self.max_requests,
            "time_window": self.time_window,
            "current_tokens": round(self.tokens, 2),
            "utilization_percent": round((1 - self.tokens / self.max_requests) * 100, 2)
        }


class ConnectionPool:
    """Generic connection pool with health monitoring."""
    
    def __init__(self, 
                 create_connection: Callable,
                 close_connection: Callable,
                 validate_connection: Callable,
                 min_connections: int = 5,
                 max_connections: int = 20,
                 connection_timeout: float = 30.0):
        
        self.create_connection = create_connection
        self.close_connection = close_connection
        self.validate_connection = validate_connection
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.connection_timeout = connection_timeout
        
        self._pool: deque = deque()
        self._active_connections: weakref.WeakSet = weakref.WeakSet()
        self._lock = asyncio.Lock()
        self._closed = False
        
        # Pool statistics
        self.created_connections = 0
        self.closed_connections = 0
        self.validation_failures = 0
    
    async def initialize(self) -> None:
        """Initialize the connection pool."""
        async with self._lock:
            for _ in range(self.min_connections):
                try:
                    conn = await self.create_connection()
                    self._pool.append(conn)
                    self.created_connections += 1
                except Exception as e:
                    logger.error(f"Failed to create initial connection: {e}")
    
    @asynccontextmanager
    async def get_connection(self):
        """Get connection from pool with automatic return."""
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        
        connection = None
        try:
            connection = await self._acquire_connection()
            self._active_connections.add(connection)
            yield connection
        finally:
            if connection:
                await self._return_connection(connection)
                self._active_connections.discard(connection)
    
    async def _acquire_connection(self):
        """Acquire connection from pool."""
        async with self._lock:
            # Try to get existing connection
            while self._pool:
                conn = self._pool.popleft()
                
                # Validate connection
                try:
                    if await self.validate_connection(conn):
                        return conn
                    else:
                        await self.close_connection(conn)
                        self.validation_failures += 1
                except Exception:
                    self.validation_failures += 1
                    continue
            
            # Create new connection if under limit
            if len(self._active_connections) < self.max_connections:
                try:
                    conn = await asyncio.wait_for(
                        self.create_connection(),
                        timeout=self.connection_timeout
                    )
                    self.created_connections += 1
                    return conn
                except Exception as e:
                    logger.error(f"Failed to create new connection: {e}")
                    raise
            
            # Wait for connection to become available
            raise RuntimeError("No connections available and pool at maximum capacity")
    
    async def _return_connection(self, connection) -> None:
        """Return connection to pool."""
        async with self._lock:
            if not self._closed and len(self._pool) < self.max_connections:
                # Validate before returning to pool
                try:
                    if await self.validate_connection(connection):
                        self._pool.append(connection)
                        return
                except Exception:
                    pass
            
            # Close connection if pool is full or validation failed
            try:
                await self.close_connection(connection)
                self.closed_connections += 1
            except Exception:
                pass
    
    async def close(self) -> None:
        """Close all connections in pool."""
        async with self._lock:
            self._closed = True
            
            # Close pooled connections
            while self._pool:
                conn = self._pool.popleft()
                try:
                    await self.close_connection(conn)
                    self.closed_connections += 1
                except Exception:
                    pass
            
            # Close active connections
            for conn in list(self._active_connections):
                try:
                    await self.close_connection(conn)
                    self.closed_connections += 1
                except Exception:
                    pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        return {
            "pooled_connections": len(self._pool),
            "active_connections": len(self._active_connections),
            "total_created": self.created_connections,
            "total_closed": self.closed_connections,
            "validation_failures": self.validation_failures,
            "pool_utilization": len(self._active_connections) / self.max_connections * 100
        }


class EventBatcher:
    """Batch events for efficient processing."""
    
    def __init__(self,
                 batch_size: int = 50,
                 max_wait_time: float = 5.0,
                 processor: Optional[Callable] = None):
        
        self.batch_size = batch_size
        self.max_wait_time = max_wait_time
        self.processor = processor
        
        self._batch: List = []
        self._last_flush = time.time()
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
    
    async def add_event(self, event: Any) -> None:
        """Add event to batch."""
        async with self._lock:
            self._batch.append(event)
            
            # Check if we should flush
            if (len(self._batch) >= self.batch_size or
                time.time() - self._last_flush >= self.max_wait_time):
                await self._flush_batch()
    
    async def _flush_batch(self) -> None:
        """Flush current batch."""
        if not self._batch:
            return
        
        batch_to_process = self._batch[:]
        self._batch.clear()
        self._last_flush = time.time()
        
        if self.processor:
            try:
                await self.processor(batch_to_process)
            except Exception as e:
                logger.error(f"Error processing batch: {e}")
    
    async def force_flush(self) -> None:
        """Force flush current batch."""
        async with self._lock:
            await self._flush_batch()
    
    def start_periodic_flush(self) -> None:
        """Start periodic flush task."""
        if self._flush_task and not self._flush_task.done():
            return
        
        self._flush_task = asyncio.create_task(self._periodic_flush())
    
    async def _periodic_flush(self) -> None:
        """Periodic flush task."""
        while True:
            try:
                await asyncio.sleep(self.max_wait_time)
                async with self._lock:
                    if time.time() - self._last_flush >= self.max_wait_time:
                        await self._flush_batch()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic flush: {e}")
    
    async def stop(self) -> None:
        """Stop batcher and flush remaining events."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        await self.force_flush()


class CacheManager:
    """LRU cache with TTL support."""
    
    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0):
        self.max_size = max_size
        self.default_ttl = default_ttl
        
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._access_order: deque = deque()
        self._lock = asyncio.Lock()
        
        # Statistics
        self.hits = 0
        self.misses = 0
        self.evictions = 0
    
    async def get(self, key: str, default: Any = None) -> Any:
        """Get value from cache."""
        async with self._lock:
            if key in self._cache:
                value, expires_at = self._cache[key]
                
                # Check if expired
                if time.time() > expires_at:
                    del self._cache[key]
                    self._access_order.remove(key)
                    self.misses += 1
                    return default
                
                # Update access order
                self._access_order.remove(key)
                self._access_order.append(key)
                self.hits += 1
                return value
            
            self.misses += 1
            return default
    
    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Set value in cache."""
        async with self._lock:
            expires_at = time.time() + (ttl or self.default_ttl)
            
            # Remove if already exists
            if key in self._cache:
                self._access_order.remove(key)
            
            # Add to cache
            self._cache[key] = (value, expires_at)
            self._access_order.append(key)
            
            # Evict if over size limit
            while len(self._cache) > self.max_size:
                oldest_key = self._access_order.popleft()
                del self._cache[oldest_key]
                self.evictions += 1
    
    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._access_order.remove(key)
                return True
            return False
    
    async def clear(self) -> None:
        """Clear all cache entries."""
        async with self._lock:
            self._cache.clear()
            self._access_order.clear()
    
    async def cleanup_expired(self) -> int:
        """Remove expired entries and return count."""
        async with self._lock:
            current_time = time.time()
            expired_keys = []
            
            for key, (_, expires_at) in self._cache.items():
                if current_time > expires_at:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._cache[key]
                self._access_order.remove(key)
            
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": round(hit_rate, 2),
            "evictions": self.evictions
        }


class PerformanceMonitor:
    """Centralized performance monitoring and optimization."""
    
    def __init__(self):
        self.metrics = PerformanceMetrics()
        self.rate_limiters: Dict[str, RateLimiter] = {}
        self.caches: Dict[str, CacheManager] = {}
        self.batchers: Dict[str, EventBatcher] = {}
        
        self._monitoring_task: Optional[asyncio.Task] = None
        self._metrics_lock = asyncio.Lock()
        
        # Thread pool for CPU-bound tasks
        self.thread_pool = ThreadPoolExecutor(max_workers=4)
    
    def create_rate_limiter(self, name: str, max_requests: int, time_window: float) -> RateLimiter:
        """Create and register rate limiter."""
        limiter = RateLimiter(max_requests, time_window)
        self.rate_limiters[name] = limiter
        return limiter
    
    def create_cache(self, name: str, max_size: int = 1000, default_ttl: float = 300.0) -> CacheManager:
        """Create and register cache manager."""
        cache = CacheManager(max_size, default_ttl)
        self.caches[name] = cache
        return cache
    
    def create_batcher(self, name: str, batch_size: int = 50, 
                      max_wait_time: float = 5.0, processor: Optional[Callable] = None) -> EventBatcher:
        """Create and register event batcher."""
        batcher = EventBatcher(batch_size, max_wait_time, processor)
        self.batchers[name] = batcher
        return batcher
    
    async def start_monitoring(self, interval: float = 30.0) -> None:
        """Start performance monitoring."""
        if self.metrics.startup_time is None:
            self.metrics.startup_time = datetime.now(timezone.utc)
        
        self._monitoring_task = asyncio.create_task(self._monitoring_loop(interval))
    
    async def _monitoring_loop(self, interval: float) -> None:
        """Performance monitoring loop."""
        while True:
            try:
                await self._update_metrics()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(interval)
    
    async def _update_metrics(self) -> None:
        """Update all performance metrics."""
        async with self._metrics_lock:
            # Update system metrics
            self.metrics.update_system_metrics()
            
            # Update cache metrics
            for cache in self.caches.values():
                await cache.cleanup_expired()
    
    async def stop_monitoring(self) -> None:
        """Stop performance monitoring."""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        
        # Stop all batchers
        for batcher in self.batchers.values():
            await batcher.stop()
        
        # Clear all caches
        for cache in self.caches.values():
            await cache.clear()
        
        # Shutdown thread pool
        self.thread_pool.shutdown(wait=True)
    
    async def get_comprehensive_metrics(self) -> Dict[str, Any]:
        """Get comprehensive performance metrics."""
        async with self._metrics_lock:
            metrics = self.metrics.to_dict()
            
            # Add rate limiter stats
            metrics["rate_limiters"] = {
                name: limiter.get_status() 
                for name, limiter in self.rate_limiters.items()
            }
            
            # Add cache stats
            metrics["caches"] = {
                name: cache.get_stats() 
                for name, cache in self.caches.items()
            }
            
            return metrics
    
    def record_api_request(self, success: bool, response_time: float) -> None:
        """Record API request metrics."""
        self.metrics.api_requests_total += 1
        if success:
            self.metrics.api_requests_successful += 1
        else:
            self.metrics.api_requests_failed += 1
        
        self.metrics.add_api_response_time(response_time)
    
    def record_event_processing(self, count: int, processing_time: float) -> None:
        """Record event processing metrics."""
        self.metrics.events_processed_total += count
        self.metrics.add_event_processing_time(processing_time)
        
        # Calculate events per second
        if processing_time > 0:
            self.metrics.events_processed_per_second = count / processing_time


def performance_timer(metric_name: str = None):
    """Decorator to time function execution and record metrics."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                success = True
                return result
            except Exception as e:
                success = False
                raise
            finally:
                end_time = time.time()
                duration = end_time - start_time
                
                # Record metrics if monitor available
                if hasattr(func, '__self__') and hasattr(func.__self__, 'performance_monitor'):
                    monitor = func.__self__.performance_monitor
                    if metric_name == 'api_request':
                        monitor.record_api_request(success, duration)
                    elif metric_name == 'event_processing':
                        monitor.record_event_processing(1, duration)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                end_time = time.time()
                duration = end_time - start_time
                logger.debug(f"{func.__name__} took {duration:.3f}s")
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Global performance monitor instance
_global_monitor: Optional[PerformanceMonitor] = None


def get_performance_monitor() -> PerformanceMonitor:
    """Get global performance monitor instance."""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PerformanceMonitor()
    return _global_monitor


def setup_performance_optimization(config: Dict[str, Any]) -> PerformanceMonitor:
    """Setup performance optimization based on configuration."""
    monitor = get_performance_monitor()
    
    # Setup rate limiters
    api_rate_limit = config.get('API_RATE_LIMIT_PER_MINUTE', 60)
    monitor.create_rate_limiter('api', api_rate_limit, 60.0)
    
    websocket_rate_limit = config.get('WEBSOCKET_MAX_SUBSCRIPTIONS', 100)
    monitor.create_rate_limiter('websocket', websocket_rate_limit, 60.0)
    
    # Setup caches
    monitor.create_cache('api_responses', max_size=1000, default_ttl=300.0)
    monitor.create_cache('streamer_data', max_size=5000, default_ttl=600.0)
    
    # Setup batchers
    batch_size = config.get('EVENT_PROCESSING_BATCH_SIZE', 50)
    batch_timeout = config.get('EVENT_PROCESSING_TIMEOUT', 30)
    monitor.create_batcher('events', batch_size=batch_size, max_wait_time=batch_timeout)
    
    return monitor


# Export performance utilities
__all__ = [
    'PerformanceMetrics',
    'RateLimiter', 
    'ConnectionPool',
    'EventBatcher',
    'CacheManager',
    'PerformanceMonitor',
    'performance_timer',
    'get_performance_monitor',
    'setup_performance_optimization'
]