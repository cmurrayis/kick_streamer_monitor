"""
System performance tests for Kick Streamer Monitor.

Tests system performance under various conditions including high load,
stress testing, and performance regression detection.
"""

import pytest
import asyncio
import time
from datetime import datetime, timezone
from typing import List, Dict, Any
import json

from tests.performance.benchmark import BenchmarkRunner, benchmark_async_function
from tests.performance.load_testing import LoadTestRunner, LoadTestConfig, LoadPattern, ScenarioRunner
from tests.performance.profiling import ProfileRunner

from src.lib.config import ConfigurationManager
from src.lib.processor import EventProcessor, Event, EventType
from src.lib.errors import ErrorHandler, MonitoringError
from src.lib.health import HealthMonitor
from src.models.streamer import Streamer, StreamerStatus


class TestCoreComponentPerformance:
    """Performance tests for core system components."""
    
    @pytest.fixture
    def benchmark_runner(self):
        """Fixture providing benchmark runner."""
        return BenchmarkRunner(warmup_iterations=5, min_iterations=100)
    
    @pytest.fixture
    def profile_runner(self):
        """Fixture providing profile runner."""
        return ProfileRunner()
    
    def test_streamer_model_creation_performance(self, benchmark_runner):
        """Test performance of streamer model creation and validation."""
        
        def create_streamer():
            return Streamer(
                kick_user_id="12345",
                username="test_streamer",
                display_name="Test Streamer",
                status=StreamerStatus.ONLINE
            )
        
        result = benchmark_runner.benchmark_function(
            func=create_streamer,
            name="streamer_creation",
            description="Creating and validating streamer models",
            iterations=1000
        )
        
        # Performance assertions
        assert result.metrics.avg_time < 0.001  # Less than 1ms average
        assert result.metrics.operations_per_second > 500  # At least 500 ops/sec
        assert result.metrics.success_rate == 100.0  # No failures
        
        print(result.summary())
    
    def test_streamer_status_update_performance(self, benchmark_runner):
        """Test performance of streamer status updates."""
        
        streamer = Streamer(
            kick_user_id="12345",
            username="test_streamer",
            status=StreamerStatus.OFFLINE
        )
        
        def update_status():
            # Alternate between online and offline
            new_status = StreamerStatus.ONLINE if streamer.status == StreamerStatus.OFFLINE else StreamerStatus.OFFLINE
            return streamer.update_status(new_status)
        
        result = benchmark_runner.benchmark_function(
            func=update_status,
            name="status_update",
            description="Updating streamer status with validation",
            iterations=1000
        )
        
        # Performance assertions
        assert result.metrics.avg_time < 0.002  # Less than 2ms average
        assert result.metrics.operations_per_second > 300  # At least 300 ops/sec
        
        print(result.summary())
    
    @pytest.mark.asyncio
    async def test_event_processor_performance(self, benchmark_runner):
        """Test event processor performance under load."""
        
        processor = EventProcessor()
        processed_events = []
        
        async def batch_callback(events):
            processed_events.extend(events)
        
        processor.set_batch_callback(batch_callback)
        
        async def process_events():
            # Process a batch of events
            events = [
                Event(f"event_{i}", EventType.STREAM_START, {"data": f"test_{i}"}, 123 + i)
                for i in range(10)
            ]
            
            for event in events:
                await processor.process_event(event)
            
            # Flush any remaining events
            await processor.flush_batches()
        
        result = await benchmark_runner.benchmark_async_function(
            func=process_events,
            name="event_processing",
            description="Processing batches of events through pipeline",
            iterations=100
        )
        
        # Performance assertions
        assert result.metrics.avg_time < 0.1  # Less than 100ms for 10 events
        assert result.metrics.operations_per_second > 10  # At least 10 batches/sec
        
        print(result.summary())
    
    @pytest.mark.asyncio
    async def test_error_handler_performance(self, benchmark_runner):
        """Test error handling performance."""
        
        error_handler = ErrorHandler()
        handled_errors = []
        
        def error_callback(error):
            handled_errors.append(error)
        
        error_handler.register_handler(MonitoringError, error_callback)
        
        async def handle_errors():
            # Handle a batch of errors
            for i in range(5):
                error = MonitoringError(f"Test error {i}")
                await error_handler.handle_error(error)
        
        result = await benchmark_runner.benchmark_async_function(
            func=handle_errors,
            name="error_handling",
            description="Handling and processing errors",
            iterations=100
        )
        
        # Performance assertions
        assert result.metrics.avg_time < 0.05  # Less than 50ms for 5 errors
        assert result.metrics.operations_per_second > 20  # At least 20 batches/sec
        
        print(result.summary())
    
    def test_configuration_loading_performance(self, benchmark_runner):
        """Test configuration loading performance."""
        
        def load_config():
            config_manager = ConfigurationManager(auto_load=False)
            config_data = {
                "database": {
                    "host": "localhost",
                    "port": "5432",
                    "name": "kick_monitor"
                },
                "websocket": {
                    "url": "wss://ws-us2.pusher.com",
                    "timeout": "30"
                },
                "kick": {
                    "client_id": "test_client_id",
                    "client_secret": "test_secret"
                }
            }
            config_manager.load_from_dict(config_data)
            
            # Validate configuration
            config_manager.validate([
                "database.host",
                "database.port",
                "websocket.url",
                "kick.client_id"
            ])
            
            return config_manager
        
        result = benchmark_runner.benchmark_function(
            func=load_config,
            name="config_loading",
            description="Loading and validating configuration",
            iterations=200
        )
        
        # Performance assertions
        assert result.metrics.avg_time < 0.01  # Less than 10ms average
        assert result.metrics.operations_per_second > 100  # At least 100 ops/sec
        
        print(result.summary())


class TestEventProcessingPerformance:
    """Performance tests focused on event processing pipeline."""
    
    @pytest.fixture
    def event_processor(self):
        """Fixture providing configured event processor."""
        processor = EventProcessor()
        return processor
    
    @pytest.mark.asyncio
    async def test_event_deduplication_performance(self, event_processor):
        """Test event deduplication performance."""
        
        benchmark_runner = BenchmarkRunner()
        
        async def process_duplicate_events():
            # Create many similar events that should be deduplicated
            base_event_data = {"streamer_id": 123, "status": "online"}
            
            for i in range(50):
                # Half will be duplicates
                event_id = f"event_{i // 2}"
                event = Event(event_id, EventType.STATUS_CHANGE, base_event_data, 123)
                await event_processor.process_event(event)
        
        result = await benchmark_runner.benchmark_async_function(
            func=process_duplicate_events,
            name="event_deduplication",
            description="Processing events with deduplication",
            iterations=20
        )
        
        # Check deduplication effectiveness
        stats = event_processor.get_stats()
        assert stats['events_duplicate'] > 0  # Should have detected duplicates
        
        # Performance assertions
        assert result.metrics.avg_time < 0.2  # Less than 200ms for 50 events
        
        print(result.summary())
        print(f"Deduplication stats: {stats}")
    
    @pytest.mark.asyncio
    async def test_event_batching_performance(self, event_processor):
        """Test event batching performance."""
        
        # Configure for immediate batching
        event_processor.batcher.max_batch_size = 10
        
        processed_batches = []
        
        async def batch_callback(events):
            processed_batches.append(len(events))
        
        event_processor.set_batch_callback(batch_callback)
        
        benchmark_runner = BenchmarkRunner()
        
        async def process_events_for_batching():
            # Generate events that should trigger batching
            for i in range(25):  # Should create 2-3 batches
                event = Event(f"batch_event_{i}", EventType.HEARTBEAT, {}, 123)
                await event_processor.process_event(event)
            
            # Flush remaining
            await event_processor.flush_batches()
        
        result = await benchmark_runner.benchmark_async_function(
            func=process_events_for_batching,
            name="event_batching",
            description="Processing events with batching",
            iterations=50
        )
        
        # Check batching effectiveness
        assert len(processed_batches) > 0  # Should have created batches
        
        # Performance assertions
        assert result.metrics.avg_time < 0.1  # Less than 100ms for 25 events
        
        print(result.summary())
        print(f"Batches created: {len(processed_batches)}, sizes: {processed_batches[:5]}")
    
    @pytest.mark.asyncio
    async def test_concurrent_event_processing(self):
        """Test concurrent event processing performance."""
        
        processor = EventProcessor()
        benchmark_runner = BenchmarkRunner()
        
        async def concurrent_event_processing():
            # Create multiple tasks processing events concurrently
            async def process_event_batch(batch_id: int):
                for i in range(10):
                    event = Event(f"concurrent_{batch_id}_{i}", EventType.STREAM_UPDATE, {}, batch_id)
                    await processor.process_event(event)
            
            # Run 5 concurrent batches
            tasks = [process_event_batch(i) for i in range(5)]
            await asyncio.gather(*tasks)
        
        result = await benchmark_runner.benchmark_async_function(
            func=concurrent_event_processing,
            name="concurrent_event_processing",
            description="Concurrent event processing",
            iterations=20,
            concurrent=1  # Test sequential vs concurrent separately
        )
        
        # Performance assertions
        assert result.metrics.avg_time < 0.3  # Less than 300ms for 50 events across 5 batches
        
        print(result.summary())


class TestMemoryPerformance:
    """Memory usage and leak detection tests."""
    
    @pytest.fixture
    def profile_runner(self):
        """Fixture providing profile runner."""
        return ProfileRunner()
    
    def test_streamer_model_memory_usage(self, profile_runner):
        """Test memory usage of streamer models."""
        
        def create_many_streamers():
            streamers = []
            for i in range(1000):
                streamer = Streamer(
                    kick_user_id=str(12345 + i),
                    username=f"streamer_{i}",
                    display_name=f"Streamer {i}",
                    status=StreamerStatus.ONLINE
                )
                streamers.append(streamer)
            return streamers
        
        result = profile_runner.profile_function(
            create_many_streamers,
            "streamer_memory_usage",
            enable_cpu=False,
            enable_network=False
        )
        
        # Memory assertions
        assert result.memory_peak < 50  # Less than 50MB for 1000 streamers
        assert result.memory_growth < 50  # Memory growth should be reasonable
        
        print(result.summary())
    
    @pytest.mark.asyncio
    async def test_event_processor_memory_leak(self, profile_runner):
        """Test for memory leaks in event processor."""
        
        async def process_many_events():
            processor = EventProcessor()
            
            # Process many events to check for memory leaks
            for i in range(5000):
                event = Event(f"leak_test_{i}", EventType.HEARTBEAT, {"data": f"test_{i}"}, 123)
                await processor.process_event(event)
                
                # Periodically flush to prevent normal batching from affecting results
                if i % 100 == 0:
                    await processor.flush_batches()
        
        result = await profile_runner.profile_async_function(
            process_many_events,
            "event_processor_memory_leak",
            enable_cpu=False,
            enable_network=False
        )
        
        # Memory leak detection
        assert result.memory_growth < 100  # Should not grow more than 100MB
        
        print(result.summary())
    
    def test_configuration_memory_usage(self, profile_runner):
        """Test memory usage of configuration management."""
        
        def load_large_config():
            config_manager = ConfigurationManager(auto_load=False)
            
            # Create large configuration
            large_config = {}
            for i in range(1000):
                large_config[f"section_{i}"] = {
                    f"key_{j}": f"value_{i}_{j}" for j in range(50)
                }
            
            config_manager.load_from_dict(large_config)
            return config_manager
        
        result = profile_runner.profile_function(
            load_large_config,
            "large_config_memory",
            enable_cpu=False,
            enable_network=False
        )
        
        # Memory usage should be reasonable for large config
        assert result.memory_peak < 20  # Less than 20MB for large config
        
        print(result.summary())


class TestLoadTesting:
    """Load testing scenarios for the system."""
    
    @pytest.fixture
    def load_test_runner(self):
        """Fixture providing load test runner."""
        return LoadTestRunner()
    
    @pytest.mark.asyncio
    @pytest.mark.slow  # Mark as slow test
    async def test_constant_load_scenario(self, load_test_runner):
        """Test system under constant load."""
        
        async def simple_processing_scenario(user):
            """Simple processing scenario for load testing."""
            processor = EventProcessor()
            
            for i in range(user.config.requests_per_user):
                if user.should_stop:
                    break
                
                # Simulate processing an event
                event = Event(f"load_test_{user.user_id}_{i}", EventType.HEARTBEAT, {}, user.user_id)
                await processor.process_event(event)
                await user.think_time()
        
        config = LoadTestConfig(
            duration_seconds=30,
            max_users=50,
            pattern=LoadPattern.CONSTANT,
            requests_per_user=10,
            think_time_min=0.1,
            think_time_max=0.3
        )
        
        result = await load_test_runner.run_load_test(
            simple_processing_scenario,
            config,
            "constant_load_test"
        )
        
        # Performance assertions
        assert result.error_rate < 0.05  # Less than 5% error rate
        assert result.avg_response_time < 1.0  # Less than 1 second average response
        assert result.requests_per_second > 10  # At least 10 requests per second
        
        print(result.summary())
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_ramp_up_load_scenario(self, load_test_runner):
        """Test system under gradually increasing load."""
        
        async def event_processing_scenario(user):
            """Event processing scenario with realistic operations."""
            # Simulate realistic event processing
            processor = EventProcessor()
            
            event_types = [EventType.STREAM_START, EventType.STREAM_END, EventType.STATUS_CHANGE]
            
            for i in range(user.config.requests_per_user):
                if user.should_stop:
                    break
                
                event_type = event_types[i % len(event_types)]
                event = Event(
                    f"ramp_test_{user.user_id}_{i}",
                    event_type,
                    {"timestamp": time.time(), "user": user.user_id},
                    user.user_id
                )
                
                await processor.process_event(event)
                await user.think_time()
        
        config = LoadTestConfig(
            duration_seconds=45,
            max_users=30,
            pattern=LoadPattern.RAMP_UP,
            ramp_up_seconds=20,
            requests_per_user=15,
            think_time_min=0.2,
            think_time_max=0.5
        )
        
        result = await load_test_runner.run_load_test(
            event_processing_scenario,
            config,
            "ramp_up_load_test"
        )
        
        # Performance assertions
        assert result.error_rate < 0.1  # Less than 10% error rate under ramp-up
        assert result.peak_concurrent_users >= 20  # Should reach significant concurrent users
        
        print(result.summary())
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_spike_load_scenario(self, load_test_runner):
        """Test system under sudden load spikes."""
        
        async def intensive_processing_scenario(user):
            """More intensive processing scenario."""
            processor = EventProcessor()
            error_handler = ErrorHandler()
            
            for i in range(user.config.requests_per_user):
                if user.should_stop:
                    break
                
                try:
                    # More complex processing
                    event = Event(
                        f"spike_test_{user.user_id}_{i}",
                        EventType.STATUS_CHANGE,
                        {
                            "previous_status": "offline",
                            "new_status": "online",
                            "metadata": {"user_id": user.user_id, "timestamp": time.time()}
                        },
                        user.user_id
                    )
                    
                    await processor.process_event(event)
                    
                    # Simulate some error handling
                    if i % 10 == 0:
                        test_error = MonitoringError("Test error for spike testing")
                        await error_handler.handle_error(test_error)
                    
                except Exception as e:
                    # Record error in user session
                    pass
                
                await user.think_time()
        
        config = LoadTestConfig(
            duration_seconds=40,
            max_users=40,
            pattern=LoadPattern.SPIKE,
            requests_per_user=20,
            think_time_min=0.05,
            think_time_max=0.2
        )
        
        result = await load_test_runner.run_load_test(
            intensive_processing_scenario,
            config,
            "spike_load_test"
        )
        
        # Performance assertions for spike test (more lenient)
        assert result.error_rate < 0.2  # Less than 20% error rate under spike
        assert result.peak_requests_per_second > 20  # Should handle significant peak load
        
        print(result.summary())


@pytest.mark.performance
class TestPerformanceRegression:
    """Performance regression detection tests."""
    
    def test_baseline_performance_metrics(self):
        """Establish baseline performance metrics."""
        benchmark_runner = BenchmarkRunner()
        
        # Test core operations that should maintain consistent performance
        baseline_tests = [
            {
                'name': 'streamer_creation_baseline',
                'func': lambda: Streamer(kick_user_id="12345", username="test", status=StreamerStatus.ONLINE),
                'max_avg_time': 0.001,
                'min_ops_per_sec': 500
            },
            {
                'name': 'config_validation_baseline',
                'func': lambda: ConfigurationManager(auto_load=False).validate(['test.key']),
                'max_avg_time': 0.005,
                'min_ops_per_sec': 200
            }
        ]
        
        results = {}
        for test in baseline_tests:
            result = benchmark_runner.benchmark_function(
                func=test['func'],
                name=test['name'],
                description=f"Baseline performance test for {test['name']}",
                iterations=200
            )
            
            # Assert performance requirements
            assert result.metrics.avg_time <= test['max_avg_time'], \
                f"{test['name']} exceeded max average time: {result.metrics.avg_time:.6f}s > {test['max_avg_time']}s"
            
            assert result.metrics.operations_per_second >= test['min_ops_per_sec'], \
                f"{test['name']} below min ops/sec: {result.metrics.operations_per_second:.1f} < {test['min_ops_per_sec']}"
            
            results[test['name']] = result.metrics.to_dict()
        
        # Save baseline results for comparison
        with open('baseline_performance.json', 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print("Baseline performance metrics established and saved.")


if __name__ == "__main__":
    # Run performance tests directly
    pytest.main([__file__, "-v", "-m", "not slow"])