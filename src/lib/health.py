"""
Health monitoring with connection status tracking.

Provides comprehensive health monitoring for all system components including
database connections, WebSocket connections, OAuth services, and overall
system health with alerting and recovery mechanisms.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Callable, Union, Set
from enum import Enum
from dataclasses import dataclass, field
from collections import deque, defaultdict
import json

from .errors import MonitoringError, ErrorCategory, ErrorSeverity

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Health status levels."""
    HEALTHY = "healthy"       # All systems operational
    DEGRADED = "degraded"     # Some issues but service functional
    UNHEALTHY = "unhealthy"   # Major issues affecting functionality
    CRITICAL = "critical"     # Service not functional
    UNKNOWN = "unknown"       # Status cannot be determined


class ComponentType(str, Enum):
    """Types of monitored components."""
    DATABASE = "database"
    WEBSOCKET = "websocket"
    OAUTH = "oauth"
    API = "api"
    PROCESSOR = "processor"
    MONITOR = "monitor"
    SYSTEM = "system"


@dataclass
class HealthMetric:
    """Individual health metric."""
    name: str
    value: Union[float, int, str, bool]
    unit: Optional[str] = None
    threshold_warning: Optional[float] = None
    threshold_critical: Optional[float] = None
    description: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def get_status(self) -> HealthStatus:
        """Get health status based on thresholds."""
        if not isinstance(self.value, (int, float)):
            return HealthStatus.UNKNOWN
        
        if self.threshold_critical is not None and self.value >= self.threshold_critical:
            return HealthStatus.CRITICAL
        
        if self.threshold_warning is not None and self.value >= self.threshold_warning:
            return HealthStatus.DEGRADED
        
        return HealthStatus.HEALTHY
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'name': self.name,
            'value': self.value,
            'unit': self.unit,
            'threshold_warning': self.threshold_warning,
            'threshold_critical': self.threshold_critical,
            'description': self.description,
            'timestamp': self.timestamp.isoformat(),
            'status': self.get_status().value
        }


@dataclass
class ComponentHealth:
    """Health status of a system component."""
    component_type: ComponentType
    component_name: str
    status: HealthStatus
    message: str
    metrics: Dict[str, HealthMetric] = field(default_factory=dict)
    last_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    check_duration_ms: float = 0.0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_metric(self, metric: HealthMetric) -> None:
        """Add a health metric."""
        self.metrics[metric.name] = metric
    
    def get_overall_status(self) -> HealthStatus:
        """Get overall status considering all metrics."""
        if not self.metrics:
            return self.status
        
        metric_statuses = [metric.get_status() for metric in self.metrics.values()]
        
        # Return worst status
        if HealthStatus.CRITICAL in metric_statuses:
            return HealthStatus.CRITICAL
        elif HealthStatus.UNHEALTHY in metric_statuses:
            return HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in metric_statuses:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'component_type': self.component_type.value,
            'component_name': self.component_name,
            'status': self.status.value,
            'overall_status': self.get_overall_status().value,
            'message': self.message,
            'metrics': {name: metric.to_dict() for name, metric in self.metrics.items()},
            'last_check': self.last_check.isoformat(),
            'check_duration_ms': self.check_duration_ms,
            'consecutive_failures': self.consecutive_failures,
            'last_error': self.last_error,
            'metadata': self.metadata
        }


class HealthChecker:
    """Base class for component health checkers."""
    
    def __init__(self, component_type: ComponentType, component_name: str):
        self.component_type = component_type
        self.component_name = component_name
    
    async def check_health(self) -> ComponentHealth:
        """Check component health. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement check_health")


class DatabaseHealthChecker(HealthChecker):
    """Health checker for database connections."""
    
    def __init__(self, database_service, component_name: str = "primary_db"):
        super().__init__(ComponentType.DATABASE, component_name)
        self.database_service = database_service
    
    async def check_health(self) -> ComponentHealth:
        """Check database health."""
        start_time = time.time()
        
        try:
            # Basic connectivity test
            health_data = await self.database_service.health_check()
            check_duration = (time.time() - start_time) * 1000
            
            status = HealthStatus.HEALTHY if health_data['status'] == 'healthy' else HealthStatus.UNHEALTHY
            
            component_health = ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=status,
                message=health_data.get('error', 'Database connection healthy'),
                check_duration_ms=check_duration
            )
            
            # Add metrics
            pool_stats = health_data.get('pool_stats', {})
            if pool_stats:
                component_health.add_metric(HealthMetric(
                    name="connection_pool_size",
                    value=pool_stats.get('size', 0),
                    unit="connections",
                    threshold_warning=pool_stats.get('max_size', 20) * 0.8,
                    threshold_critical=pool_stats.get('max_size', 20) * 0.95,
                    description="Active database connections"
                ))
                
                component_health.add_metric(HealthMetric(
                    name="connection_pool_idle",
                    value=pool_stats.get('idle_size', 0),
                    unit="connections",
                    description="Idle database connections"
                ))
            
            component_health.add_metric(HealthMetric(
                name="response_time",
                value=check_duration,
                unit="ms",
                threshold_warning=1000,
                threshold_critical=5000,
                description="Database response time"
            ))
            
            return component_health
        
        except Exception as e:
            check_duration = (time.time() - start_time) * 1000
            
            return ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=HealthStatus.CRITICAL,
                message=f"Database health check failed: {e}",
                check_duration_ms=check_duration,
                last_error=str(e)
            )


class WebSocketHealthChecker(HealthChecker):
    """Health checker for WebSocket connections."""
    
    def __init__(self, websocket_service, component_name: str = "kick_websocket"):
        super().__init__(ComponentType.WEBSOCKET, component_name)
        self.websocket_service = websocket_service
    
    async def check_health(self) -> ComponentHealth:
        """Check WebSocket health."""
        start_time = time.time()
        
        try:
            connection_status = self.websocket_service.get_connection_status()
            check_duration = (time.time() - start_time) * 1000
            
            is_connected = connection_status.get('is_connected', False)
            status = HealthStatus.HEALTHY if is_connected else HealthStatus.UNHEALTHY
            
            component_health = ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=status,
                message="WebSocket connected" if is_connected else "WebSocket disconnected",
                check_duration_ms=check_duration,
                metadata=connection_status
            )
            
            # Add metrics
            component_health.add_metric(HealthMetric(
                name="uptime",
                value=connection_status.get('uptime_seconds', 0),
                unit="seconds",
                description="WebSocket connection uptime"
            ))
            
            component_health.add_metric(HealthMetric(
                name="subscribed_channels",
                value=connection_status.get('subscribed_channels', 0),
                unit="channels",
                description="Number of subscribed channels"
            ))
            
            component_health.add_metric(HealthMetric(
                name="total_messages",
                value=connection_status.get('total_messages', 0),
                unit="messages",
                description="Total messages received"
            ))
            
            component_health.add_metric(HealthMetric(
                name="reconnect_attempts",
                value=connection_status.get('reconnect_attempts', 0),
                unit="attempts",
                threshold_warning=3,
                threshold_critical=10,
                description="Number of reconnection attempts"
            ))
            
            return component_health
        
        except Exception as e:
            check_duration = (time.time() - start_time) * 1000
            
            return ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=HealthStatus.CRITICAL,
                message=f"WebSocket health check failed: {e}",
                check_duration_ms=check_duration,
                last_error=str(e)
            )


class OAuthHealthChecker(HealthChecker):
    """Health checker for OAuth authentication."""
    
    def __init__(self, oauth_service, component_name: str = "kick_oauth"):
        super().__init__(ComponentType.OAUTH, component_name)
        self.oauth_service = oauth_service
    
    async def check_health(self) -> ComponentHealth:
        """Check OAuth health."""
        start_time = time.time()
        
        try:
            # Check token status
            token_info = self.oauth_service.get_token_info()
            stats = self.oauth_service.get_stats()
            check_duration = (time.time() - start_time) * 1000
            
            # Determine status based on token state
            if token_info and not token_info.get('is_expired', True):
                status = HealthStatus.HEALTHY
                message = "OAuth token valid"
            elif token_info and token_info.get('is_expired', True):
                status = HealthStatus.DEGRADED
                message = "OAuth token expired but can be refreshed"
            else:
                status = HealthStatus.UNHEALTHY
                message = "No valid OAuth token"
            
            component_health = ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=status,
                message=message,
                check_duration_ms=check_duration,
                metadata=stats
            )
            
            # Add metrics
            if token_info:
                expires_in = token_info.get('expires_in_seconds', 0)
                component_health.add_metric(HealthMetric(
                    name="token_expires_in",
                    value=max(0, expires_in),
                    unit="seconds",
                    threshold_warning=300,  # 5 minutes
                    threshold_critical=60,  # 1 minute
                    description="Time until token expiration"
                ))
            
            component_health.add_metric(HealthMetric(
                name="total_requests",
                value=stats.get('total_requests', 0),
                unit="requests",
                description="Total API requests made"
            ))
            
            component_health.add_metric(HealthMetric(
                name="recent_requests",
                value=stats.get('recent_requests', 0),
                unit="requests",
                description="Recent API requests"
            ))
            
            return component_health
        
        except Exception as e:
            check_duration = (time.time() - start_time) * 1000
            
            return ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=HealthStatus.CRITICAL,
                message=f"OAuth health check failed: {e}",
                check_duration_ms=check_duration,
                last_error=str(e)
            )


class SystemHealthChecker(HealthChecker):
    """Health checker for system resources."""
    
    def __init__(self, component_name: str = "system"):
        super().__init__(ComponentType.SYSTEM, component_name)
    
    async def check_health(self) -> ComponentHealth:
        """Check system health."""
        start_time = time.time()
        
        try:
            import psutil
            psutil_available = True
        except ImportError:
            psutil_available = False
        
        try:
            check_duration = (time.time() - start_time) * 1000
            
            component_health = ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=HealthStatus.HEALTHY,
                message="System resources available",
                check_duration_ms=check_duration
            )
            
            if psutil_available:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=0.1)
                component_health.add_metric(HealthMetric(
                    name="cpu_usage",
                    value=cpu_percent,
                    unit="percent",
                    threshold_warning=80,
                    threshold_critical=95,
                    description="CPU usage percentage"
                ))
                
                # Memory usage
                memory = psutil.virtual_memory()
                component_health.add_metric(HealthMetric(
                    name="memory_usage",
                    value=memory.percent,
                    unit="percent",
                    threshold_warning=85,
                    threshold_critical=95,
                    description="Memory usage percentage"
                ))
                
                component_health.add_metric(HealthMetric(
                    name="memory_available",
                    value=memory.available // (1024 * 1024),  # MB
                    unit="MB",
                    description="Available memory"
                ))
                
                # Disk usage
                disk = psutil.disk_usage('/')
                disk_percent = (disk.used / disk.total) * 100
                component_health.add_metric(HealthMetric(
                    name="disk_usage",
                    value=disk_percent,
                    unit="percent",
                    threshold_warning=85,
                    threshold_critical=95,
                    description="Disk usage percentage"
                ))
            else:
                component_health.message = "System monitoring limited (psutil not available)"
                component_health.status = HealthStatus.DEGRADED
            
            return component_health
        
        except Exception as e:
            check_duration = (time.time() - start_time) * 1000
            
            return ComponentHealth(
                component_type=self.component_type,
                component_name=self.component_name,
                status=HealthStatus.CRITICAL,
                message=f"System health check failed: {e}",
                check_duration_ms=check_duration,
                last_error=str(e)
            )


@dataclass
class HealthAlert:
    """Health alert for status changes."""
    component_name: str
    component_type: ComponentType
    previous_status: HealthStatus
    current_status: HealthStatus
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False
    
    @property
    def severity(self) -> str:
        """Get alert severity based on status change."""
        if self.current_status == HealthStatus.CRITICAL:
            return "critical"
        elif self.current_status == HealthStatus.UNHEALTHY:
            return "high"
        elif self.current_status == HealthStatus.DEGRADED:
            return "medium"
        else:
            return "low"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'component_name': self.component_name,
            'component_type': self.component_type.value,
            'previous_status': self.previous_status.value,
            'current_status': self.current_status.value,
            'message': self.message,
            'timestamp': self.timestamp.isoformat(),
            'severity': self.severity,
            'acknowledged': self.acknowledged
        }


class HealthMonitor:
    """Main health monitoring system."""
    
    def __init__(self, check_interval: int = 30, alert_retention_hours: int = 24):
        self.check_interval = check_interval
        self.alert_retention_hours = alert_retention_hours
        
        # Health checkers
        self._checkers: Dict[str, HealthChecker] = {}
        self._component_health: Dict[str, ComponentHealth] = {}
        self._health_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        
        # Alerting
        self._alerts: List[HealthAlert] = []
        self._alert_handlers: List[Callable[[HealthAlert], None]] = []
        
        # Monitoring state
        self._monitoring_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._last_check: Optional[datetime] = None
        
        # Statistics
        self._check_count = 0
        self._total_check_time = 0.0
    
    def register_checker(self, name: str, checker: HealthChecker) -> None:
        """Register a health checker."""
        self._checkers[name] = checker
        logger.info(f"Registered health checker: {name}")
    
    def add_alert_handler(self, handler: Callable[[HealthAlert], None]) -> None:
        """Add alert handler."""
        self._alert_handlers.append(handler)
    
    async def start(self) -> None:
        """Start health monitoring."""
        if self._is_running:
            logger.warning("Health monitor already running")
            return
        
        self._is_running = True
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info(f"Health monitor started (check interval: {self.check_interval}s)")
    
    async def stop(self) -> None:
        """Stop health monitoring."""
        if not self._is_running:
            return
        
        self._is_running = False
        
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Health monitor stopped")
    
    async def check_all_components(self) -> Dict[str, ComponentHealth]:
        """Check health of all registered components."""
        start_time = time.time()
        results = {}
        
        # Run all health checks concurrently
        tasks = []
        checker_names = []
        
        for name, checker in self._checkers.items():
            tasks.append(checker.check_health())
            checker_names.append(name)
        
        if tasks:
            health_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for name, result in zip(checker_names, health_results):
                if isinstance(result, Exception):
                    # Health check failed
                    logger.error(f"Health check failed for {name}: {result}")
                    results[name] = ComponentHealth(
                        component_type=ComponentType.UNKNOWN,
                        component_name=name,
                        status=HealthStatus.CRITICAL,
                        message=f"Health check failed: {result}",
                        last_error=str(result)
                    )
                else:
                    results[name] = result
        
        # Update stored health status and check for alerts
        for name, health in results.items():
            previous_health = self._component_health.get(name)
            self._component_health[name] = health
            
            # Add to history
            self._health_history[name].append({
                'status': health.status.value,
                'timestamp': health.last_check.isoformat(),
                'metrics': {k: v.value for k, v in health.metrics.items()}
            })
            
            # Check for status changes (alerts)
            if previous_health and previous_health.status != health.status:
                alert = HealthAlert(
                    component_name=name,
                    component_type=health.component_type,
                    previous_status=previous_health.status,
                    current_status=health.status,
                    message=health.message
                )
                await self._handle_alert(alert)
        
        # Update statistics
        check_duration = time.time() - start_time
        self._check_count += 1
        self._total_check_time += check_duration
        self._last_check = datetime.now(timezone.utc)
        
        logger.debug(f"Health check completed in {check_duration:.2f}s")
        return results
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        while self._is_running:
            try:
                await self.check_all_components()
                
                # Clean up old alerts
                self._cleanup_old_alerts()
                
                # Wait for next check
                await asyncio.sleep(self.check_interval)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitoring error: {e}")
                await asyncio.sleep(min(self.check_interval, 60))  # Don't wait too long on errors
    
    async def _handle_alert(self, alert: HealthAlert) -> None:
        """Handle health alert."""
        self._alerts.append(alert)
        
        logger.warning(
            f"Health alert: {alert.component_name} changed from "
            f"{alert.previous_status.value} to {alert.current_status.value}"
        )
        
        # Call alert handlers
        for handler in self._alert_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(alert)
                else:
                    handler(alert)
            except Exception as e:
                logger.error(f"Alert handler error: {e}")
    
    def _cleanup_old_alerts(self) -> None:
        """Clean up old alerts."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.alert_retention_hours)
        self._alerts = [alert for alert in self._alerts if alert.timestamp >= cutoff]
    
    def get_overall_status(self) -> HealthStatus:
        """Get overall system health status."""
        if not self._component_health:
            return HealthStatus.UNKNOWN
        
        statuses = [health.get_overall_status() for health in self._component_health.values()]
        
        # Return worst status
        if HealthStatus.CRITICAL in statuses:
            return HealthStatus.CRITICAL
        elif HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
    
    def get_health_summary(self) -> Dict[str, Any]:
        """Get comprehensive health summary."""
        overall_status = self.get_overall_status()
        
        # Component summary
        components_by_status = defaultdict(list)
        for name, health in self._component_health.items():
            components_by_status[health.get_overall_status().value].append(name)
        
        # Recent alerts
        recent_alerts = [alert for alert in self._alerts[-10:]]  # Last 10 alerts
        
        return {
            'overall_status': overall_status.value,
            'last_check': self._last_check.isoformat() if self._last_check else None,
            'components': {
                'total': len(self._component_health),
                'by_status': dict(components_by_status),
                'details': {name: health.to_dict() for name, health in self._component_health.items()}
            },
            'alerts': {
                'total': len(self._alerts),
                'recent': [alert.to_dict() for alert in recent_alerts]
            },
            'monitoring': {
                'is_running': self._is_running,
                'check_interval': self.check_interval,
                'check_count': self._check_count,
                'avg_check_time': self._total_check_time / max(1, self._check_count)
            }
        }
    
    def get_component_health(self, name: str) -> Optional[ComponentHealth]:
        """Get health status of specific component."""
        return self._component_health.get(name)
    
    def get_component_history(self, name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get health history for component."""
        history = self._health_history.get(name, deque())
        return list(history)[-limit:]
    
    def get_alerts(self, component_name: Optional[str] = None, 
                   severity: Optional[str] = None,
                   acknowledged: Optional[bool] = None) -> List[HealthAlert]:
        """Get alerts with optional filtering."""
        alerts = self._alerts
        
        if component_name:
            alerts = [a for a in alerts if a.component_name == component_name]
        
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        
        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]
        
        return alerts
    
    def acknowledge_alert(self, alert_index: int) -> bool:
        """Acknowledge an alert."""
        if 0 <= alert_index < len(self._alerts):
            self._alerts[alert_index].acknowledged = True
            return True
        return False
    
    @property
    def is_running(self) -> bool:
        """Check if health monitor is running."""
        return self._is_running


# Global health monitor instance
_global_health_monitor: Optional[HealthMonitor] = None


def get_health_monitor() -> HealthMonitor:
    """Get global health monitor instance."""
    global _global_health_monitor
    if _global_health_monitor is None:
        _global_health_monitor = HealthMonitor()
    return _global_health_monitor


def setup_health_monitoring(
    database_service=None,
    websocket_service=None,
    oauth_service=None,
    check_interval: int = 30
) -> HealthMonitor:
    """Setup health monitoring with standard checkers."""
    monitor = HealthMonitor(check_interval=check_interval)
    
    # Register standard health checkers
    if database_service:
        monitor.register_checker("database", DatabaseHealthChecker(database_service))
    
    if websocket_service:
        monitor.register_checker("websocket", WebSocketHealthChecker(websocket_service))
    
    if oauth_service:
        monitor.register_checker("oauth", OAuthHealthChecker(oauth_service))
    
    # Always register system checker
    monitor.register_checker("system", SystemHealthChecker())
    
    return monitor