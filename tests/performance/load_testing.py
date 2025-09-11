"""
Load testing framework for stress testing and capacity planning.

Provides utilities for simulating user load, testing system limits,
and measuring performance under various stress conditions.
"""

import asyncio
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Callable, Optional, Union, Awaitable
from dataclasses import dataclass, field
from enum import Enum
import statistics
import json
import aiohttp
from concurrent.futures import ThreadPoolExecutor
import threading


class LoadPattern(str, Enum):
    """Different load patterns for testing."""
    CONSTANT = "constant"      # Steady load
    RAMP_UP = "ramp_up"       # Gradually increasing load
    SPIKE = "spike"           # Sudden load spikes
    STEP = "step"             # Step increases in load
    WAVE = "wave"             # Sinusoidal load pattern


@dataclass
class LoadTestConfig:
    """Configuration for load testing."""
    
    # Test duration and users
    duration_seconds: int = 60
    max_users: int = 100
    min_users: int = 1
    
    # Load pattern
    pattern: LoadPattern = LoadPattern.CONSTANT
    ramp_up_seconds: int = 30
    ramp_down_seconds: int = 30
    
    # Request configuration
    requests_per_user: int = 10
    request_timeout: float = 30.0
    think_time_min: float = 1.0
    think_time_max: float = 5.0
    
    # Failure handling
    max_failures_per_user: int = 5
    failure_rate_threshold: float = 0.1  # 10%
    
    # Monitoring
    monitoring_interval: float = 1.0
    collect_response_times: bool = True
    collect_errors: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            'duration_seconds': self.duration_seconds,
            'max_users': self.max_users,
            'min_users': self.min_users,
            'pattern': self.pattern.value,
            'ramp_up_seconds': self.ramp_up_seconds,
            'ramp_down_seconds': self.ramp_down_seconds,
            'requests_per_user': self.requests_per_user,
            'request_timeout': self.request_timeout,
            'think_time_range': [self.think_time_min, self.think_time_max],
            'max_failures_per_user': self.max_failures_per_user,
            'failure_rate_threshold': self.failure_rate_threshold,
        }


@dataclass
class UserSession:
    """Individual user session data."""
    user_id: int
    start_time: float
    end_time: Optional[float] = None
    requests_made: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_response_time: float = 0.0
    errors: List[str] = field(default_factory=list)
    
    def get_avg_response_time(self) -> float:
        """Get average response time for this user."""
        return self.total_response_time / max(1, self.requests_made)
    
    def get_failure_rate(self) -> float:
        """Get failure rate for this user."""
        return self.failed_requests / max(1, self.requests_made)


@dataclass
class LoadTestSnapshot:
    """Snapshot of load test metrics at a point in time."""
    timestamp: float
    active_users: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_response_time: float
    requests_per_second: float
    errors_per_second: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot to dictionary."""
        return {
            'timestamp': self.timestamp,
            'active_users': self.active_users,
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'avg_response_time': self.avg_response_time,
            'requests_per_second': self.requests_per_second,
            'errors_per_second': self.errors_per_second,
        }


@dataclass
class LoadTestResult:
    """Complete load test results."""
    
    config: LoadTestConfig
    start_time: datetime
    end_time: datetime
    duration: float
    
    # Overall metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    
    # Performance metrics
    avg_response_time: float = 0.0
    min_response_time: float = 0.0
    max_response_time: float = 0.0
    p95_response_time: float = 0.0
    p99_response_time: float = 0.0
    
    # Throughput metrics
    requests_per_second: float = 0.0
    peak_requests_per_second: float = 0.0
    
    # User metrics
    total_users: int = 0
    peak_concurrent_users: int = 0
    
    # Error analysis
    error_rate: float = 0.0
    error_types: Dict[str, int] = field(default_factory=dict)
    
    # Time series data
    snapshots: List[LoadTestSnapshot] = field(default_factory=list)
    user_sessions: List[UserSession] = field(default_factory=list)
    
    def summary(self) -> str:
        """Generate summary of load test results."""
        return f"""
Load Test Results
================
Duration: {self.duration:.1f}s
Total Users: {self.total_users}
Peak Concurrent Users: {self.peak_concurrent_users}

Requests:
  Total: {self.total_requests}
  Successful: {self.successful_requests}
  Failed: {self.failed_requests}
  Error Rate: {self.error_rate:.2%}

Performance:
  Average Response Time: {self.avg_response_time * 1000:.2f}ms
  95th Percentile: {self.p95_response_time * 1000:.2f}ms
  99th Percentile: {self.p99_response_time * 1000:.2f}ms

Throughput:
  Average RPS: {self.requests_per_second:.2f}
  Peak RPS: {self.peak_requests_per_second:.2f}

Top Errors:
{self._format_top_errors()}
        """.strip()
    
    def _format_top_errors(self) -> str:
        """Format top errors for summary."""
        if not self.error_types:
            return "  None"
        
        sorted_errors = sorted(
            self.error_types.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        lines = []
        for error, count in sorted_errors:
            lines.append(f"  {error}: {count}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert results to dictionary."""
        return {
            'config': self.config.to_dict(),
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'duration': self.duration,
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'avg_response_time': self.avg_response_time,
            'min_response_time': self.min_response_time,
            'max_response_time': self.max_response_time,
            'p95_response_time': self.p95_response_time,
            'p99_response_time': self.p99_response_time,
            'requests_per_second': self.requests_per_second,
            'peak_requests_per_second': self.peak_requests_per_second,
            'total_users': self.total_users,
            'peak_concurrent_users': self.peak_concurrent_users,
            'error_rate': self.error_rate,
            'error_types': self.error_types,
            'snapshots': [snapshot.to_dict() for snapshot in self.snapshots],
        }


class UserSimulator:
    """Simulates individual user behavior."""
    
    def __init__(
        self,
        user_id: int,
        scenario: Callable[['UserSimulator'], Awaitable[None]],
        config: LoadTestConfig
    ):
        self.user_id = user_id
        self.scenario = scenario
        self.config = config
        self.session = UserSession(user_id=user_id, start_time=time.time())
        self.should_stop = False
        self._http_session: Optional[aiohttp.ClientSession] = None
    
    async def run(self) -> UserSession:
        """Run the user simulation."""
        try:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.request_timeout)
            )
            
            # Run user scenario
            await self.scenario(self)
            
        except Exception as e:
            self.session.errors.append(f"User {self.user_id} failed: {str(e)}")
        finally:
            if self._http_session:
                await self._http_session.close()
            
            self.session.end_time = time.time()
        
        return self.session
    
    async def make_request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Optional[aiohttp.ClientResponse]:
        """Make an HTTP request and record metrics."""
        if self.should_stop:
            return None
        
        start_time = time.time()
        
        try:
            if not self._http_session:
                raise RuntimeError("HTTP session not initialized")
            
            async with self._http_session.request(method, url, **kwargs) as response:
                response_time = time.time() - start_time
                
                self.session.requests_made += 1
                self.session.total_response_time += response_time
                
                if response.status < 400:
                    self.session.successful_requests += 1
                else:
                    self.session.failed_requests += 1
                    self.session.errors.append(f"HTTP {response.status}: {url}")
                
                return response
                
        except Exception as e:
            response_time = time.time() - start_time
            self.session.requests_made += 1
            self.session.total_response_time += response_time
            self.session.failed_requests += 1
            self.session.errors.append(f"Request failed: {str(e)}")
            return None
    
    async def think_time(self):
        """Simulate user think time between requests."""
        if self.should_stop:
            return
        
        think_time = random.uniform(
            self.config.think_time_min,
            self.config.think_time_max
        )
        await asyncio.sleep(think_time)
    
    def stop(self):
        """Signal the user to stop."""
        self.should_stop = True


class ScenarioRunner:
    """Predefined scenarios for load testing."""
    
    @staticmethod
    async def simple_get_scenario(user: UserSimulator, base_url: str = "http://localhost:8080"):
        """Simple GET request scenario."""
        for _ in range(user.config.requests_per_user):
            if user.should_stop:
                break
            
            await user.make_request("GET", f"{base_url}/health")
            await user.think_time()
    
    @staticmethod
    async def api_browsing_scenario(user: UserSimulator, base_url: str = "http://localhost:8080"):
        """API browsing scenario with multiple endpoints."""
        endpoints = [
            "/api/streamers",
            "/api/streamers/123",
            "/api/health",
            "/api/status",
        ]
        
        for _ in range(user.config.requests_per_user):
            if user.should_stop:
                break
            
            endpoint = random.choice(endpoints)
            await user.make_request("GET", f"{base_url}{endpoint}")
            await user.think_time()
    
    @staticmethod
    async def websocket_simulation_scenario(user: UserSimulator, ws_url: str = "ws://localhost:8080/ws"):
        """WebSocket connection simulation."""
        try:
            import websockets
            
            async with websockets.connect(ws_url) as websocket:
                # Send some messages
                for i in range(user.config.requests_per_user):
                    if user.should_stop:
                        break
                    
                    message = json.dumps({
                        "type": "subscribe",
                        "channel": f"user_{user.user_id}",
                        "message_id": i
                    })
                    
                    start_time = time.time()
                    try:
                        await websocket.send(message)
                        response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        
                        response_time = time.time() - start_time
                        user.session.requests_made += 1
                        user.session.total_response_time += response_time
                        user.session.successful_requests += 1
                        
                    except Exception as e:
                        response_time = time.time() - start_time
                        user.session.requests_made += 1
                        user.session.total_response_time += response_time
                        user.session.failed_requests += 1
                        user.session.errors.append(f"WebSocket error: {str(e)}")
                    
                    await user.think_time()
        
        except Exception as e:
            user.session.errors.append(f"WebSocket connection failed: {str(e)}")


class LoadTestRunner:
    """Main load test runner."""
    
    def __init__(self):
        self.results: List[LoadTestResult] = []
    
    async def run_load_test(
        self,
        scenario: Callable[[UserSimulator], Awaitable[None]],
        config: LoadTestConfig,
        name: str = "Load Test"
    ) -> LoadTestResult:
        """
        Execute a load test with the given scenario and configuration.
        
        Args:
            scenario: User scenario function
            config: Load test configuration
            name: Test name for identification
            
        Returns:
            LoadTestResult with comprehensive metrics
        """
        print(f"Starting load test: {name}")
        print(f"Configuration: {config.max_users} users, {config.duration_seconds}s duration")
        
        start_time = datetime.now(timezone.utc)
        test_start = time.time()
        
        # Initialize result
        result = LoadTestResult(
            config=config,
            start_time=start_time,
            end_time=start_time,  # Will be updated at the end
            duration=0.0
        )
        
        # Monitoring setup
        active_users: List[UserSimulator] = []
        completed_sessions: List[UserSession] = []
        monitoring_task = None
        
        try:
            # Start monitoring
            monitoring_task = asyncio.create_task(
                self._monitor_test_progress(result, active_users, config)
            )
            
            # Generate user load based on pattern
            user_schedule = self._generate_user_schedule(config)
            
            # Execute test
            for scheduled_time, action, user_count in user_schedule:
                # Wait until scheduled time
                elapsed = time.time() - test_start
                if scheduled_time > elapsed:
                    await asyncio.sleep(scheduled_time - elapsed)
                
                if action == "start_users":
                    # Start new users
                    new_users = []
                    for i in range(user_count):
                        user_id = len(active_users) + len(completed_sessions) + i + 1
                        user = UserSimulator(user_id, scenario, config)
                        new_users.append(user)
                    
                    # Start user tasks
                    for user in new_users:
                        active_users.append(user)
                        asyncio.create_task(self._run_user_until_completion(user, completed_sessions))
                
                elif action == "stop_users":
                    # Stop some users
                    users_to_stop = active_users[:user_count]
                    for user in users_to_stop:
                        user.stop()
                
                # Check if test should end
                if time.time() - test_start >= config.duration_seconds:
                    break
            
            # Wait for remaining users to complete or timeout
            timeout_time = test_start + config.duration_seconds + 30  # 30s grace period
            while active_users and time.time() < timeout_time:
                await asyncio.sleep(0.1)
            
            # Force stop any remaining users
            for user in active_users:
                user.stop()
            
            # Wait a bit more for cleanup
            await asyncio.sleep(1)
            
        finally:
            # Stop monitoring
            if monitoring_task:
                monitoring_task.cancel()
                try:
                    await monitoring_task
                except asyncio.CancelledError:
                    pass
        
        # Calculate final results
        end_time = datetime.now(timezone.utc)
        total_duration = time.time() - test_start
        
        result.end_time = end_time
        result.duration = total_duration
        result.user_sessions = completed_sessions
        
        self._calculate_final_metrics(result, completed_sessions)
        
        print(f"Load test completed in {total_duration:.1f}s")
        print(f"Total users: {len(completed_sessions)}")
        print(f"Total requests: {result.total_requests}")
        print(f"Error rate: {result.error_rate:.2%}")
        
        self.results.append(result)
        return result
    
    async def _run_user_until_completion(
        self,
        user: UserSimulator,
        completed_sessions: List[UserSession]
    ):
        """Run a user simulation until completion."""
        try:
            session = await user.run()
            completed_sessions.append(session)
        except Exception as e:
            # Create error session
            error_session = UserSession(
                user_id=user.user_id,
                start_time=user.session.start_time,
                end_time=time.time()
            )
            error_session.errors.append(f"User simulation failed: {str(e)}")
            completed_sessions.append(error_session)
    
    async def _monitor_test_progress(
        self,
        result: LoadTestResult,
        active_users: List[UserSimulator],
        config: LoadTestConfig
    ):
        """Monitor test progress and collect metrics."""
        last_snapshot_time = time.time()
        last_requests = 0
        last_errors = 0
        
        while True:
            try:
                current_time = time.time()
                
                # Calculate current metrics
                total_requests = sum(user.session.requests_made for user in active_users)
                successful_requests = sum(user.session.successful_requests for user in active_users)
                failed_requests = sum(user.session.failed_requests for user in active_users)
                
                # Calculate rates
                time_delta = current_time - last_snapshot_time
                if time_delta > 0:
                    requests_per_second = (total_requests - last_requests) / time_delta
                    errors_per_second = (failed_requests - last_errors) / time_delta
                else:
                    requests_per_second = 0.0
                    errors_per_second = 0.0
                
                # Calculate average response time
                total_response_time = sum(user.session.total_response_time for user in active_users)
                avg_response_time = total_response_time / max(1, total_requests)
                
                # Create snapshot
                snapshot = LoadTestSnapshot(
                    timestamp=current_time,
                    active_users=len(active_users),
                    total_requests=total_requests,
                    successful_requests=successful_requests,
                    failed_requests=failed_requests,
                    avg_response_time=avg_response_time,
                    requests_per_second=requests_per_second,
                    errors_per_second=errors_per_second
                )
                
                result.snapshots.append(snapshot)
                
                # Update peak metrics
                result.peak_concurrent_users = max(result.peak_concurrent_users, len(active_users))
                result.peak_requests_per_second = max(result.peak_requests_per_second, requests_per_second)
                
                # Update for next iteration
                last_snapshot_time = current_time
                last_requests = total_requests
                last_errors = failed_requests
                
                await asyncio.sleep(config.monitoring_interval)
                
            except asyncio.CancelledError:
                break
            except Exception:
                # Continue monitoring even if there are errors
                await asyncio.sleep(config.monitoring_interval)
    
    def _generate_user_schedule(self, config: LoadTestConfig) -> List[tuple]:
        """Generate schedule of when to start/stop users based on load pattern."""
        schedule = []
        
        if config.pattern == LoadPattern.CONSTANT:
            # Start all users at the beginning
            schedule.append((0, "start_users", config.max_users))
        
        elif config.pattern == LoadPattern.RAMP_UP:
            # Gradually increase users
            users_per_step = max(1, config.max_users // 10)
            time_per_step = config.ramp_up_seconds / 10
            
            for step in range(10):
                start_time = step * time_per_step
                users_to_start = min(users_per_step, config.max_users - step * users_per_step)
                if users_to_start > 0:
                    schedule.append((start_time, "start_users", users_to_start))
        
        elif config.pattern == LoadPattern.SPIKE:
            # Start with base load, then spike
            base_users = config.max_users // 4
            spike_users = config.max_users - base_users
            spike_time = config.duration_seconds // 3
            
            schedule.append((0, "start_users", base_users))
            schedule.append((spike_time, "start_users", spike_users))
        
        elif config.pattern == LoadPattern.STEP:
            # Step increases
            steps = 5
            users_per_step = config.max_users // steps
            time_per_step = config.duration_seconds // steps
            
            for step in range(steps):
                start_time = step * time_per_step
                schedule.append((start_time, "start_users", users_per_step))
        
        elif config.pattern == LoadPattern.WAVE:
            # Sinusoidal pattern
            import math
            steps = 20
            for step in range(steps):
                time_ratio = step / steps
                sine_value = (math.sin(time_ratio * 4 * math.pi) + 1) / 2  # 0 to 1
                users = int(config.min_users + (config.max_users - config.min_users) * sine_value)
                start_time = time_ratio * config.duration_seconds
                
                if step == 0:
                    schedule.append((start_time, "start_users", users))
                else:
                    # Calculate difference from previous step
                    prev_sine = (math.sin((step - 1) / steps * 4 * math.pi) + 1) / 2
                    prev_users = int(config.min_users + (config.max_users - config.min_users) * prev_sine)
                    
                    if users > prev_users:
                        schedule.append((start_time, "start_users", users - prev_users))
                    elif users < prev_users:
                        schedule.append((start_time, "stop_users", prev_users - users))
        
        return sorted(schedule, key=lambda x: x[0])
    
    def _calculate_final_metrics(self, result: LoadTestResult, sessions: List[UserSession]):
        """Calculate final metrics from completed user sessions."""
        if not sessions:
            return
        
        # Basic counts
        result.total_users = len(sessions)
        result.total_requests = sum(s.requests_made for s in sessions)
        result.successful_requests = sum(s.successful_requests for s in sessions)
        result.failed_requests = sum(s.failed_requests for s in sessions)
        
        # Error rate
        result.error_rate = result.failed_requests / max(1, result.total_requests)
        
        # Response time statistics
        all_response_times = []
        for session in sessions:
            if session.requests_made > 0:
                avg_time = session.total_response_time / session.requests_made
                all_response_times.extend([avg_time] * session.requests_made)
        
        if all_response_times:
            result.avg_response_time = statistics.mean(all_response_times)
            result.min_response_time = min(all_response_times)
            result.max_response_time = max(all_response_times)
            
            sorted_times = sorted(all_response_times)
            p95_index = int(0.95 * len(sorted_times))
            p99_index = int(0.99 * len(sorted_times))
            result.p95_response_time = sorted_times[min(p95_index, len(sorted_times) - 1)]
            result.p99_response_time = sorted_times[min(p99_index, len(sorted_times) - 1)]
        
        # Throughput
        if result.duration > 0:
            result.requests_per_second = result.total_requests / result.duration
        
        # Error analysis
        error_types = {}
        for session in sessions:
            for error in session.errors:
                error_type = error.split(':')[0] if ':' in error else error
                error_types[error_type] = error_types.get(error_type, 0) + 1
        
        result.error_types = error_types
    
    def export_results(self, filename: str, format: str = 'json'):
        """Export load test results to file."""
        if format.lower() == 'json':
            data = [result.to_dict() for result in self.results]
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported export format: {format}")
    
    def get_summary_report(self) -> str:
        """Generate summary report of all load test results."""
        if not self.results:
            return "No load test results available."
        
        lines = ["Load Test Summary Report", "=" * 50, ""]
        
        for i, result in enumerate(self.results, 1):
            lines.extend([
                f"Test {i}:",
                f"  Duration: {result.duration:.1f}s",
                f"  Users: {result.total_users}",
                f"  Requests: {result.total_requests}",
                f"  RPS: {result.requests_per_second:.1f}",
                f"  Error Rate: {result.error_rate:.2%}",
                f"  Avg Response Time: {result.avg_response_time * 1000:.2f}ms",
                ""
            ])
        
        return "\n".join(lines)