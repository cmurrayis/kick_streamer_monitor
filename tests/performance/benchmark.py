"""
Benchmarking framework for measuring function and system performance.

Provides utilities for timing operations, measuring throughput, memory usage,
and generating performance reports with statistical analysis.
"""

import asyncio
import time
import statistics
import gc
import tracemalloc
from datetime import datetime, timezone
from typing import Dict, List, Any, Callable, Optional, Union, TypeVar, Generic
from dataclasses import dataclass, field
from functools import wraps
import psutil
import threading
from concurrent.futures import ThreadPoolExecutor
import json


T = TypeVar('T')


@dataclass
class PerformanceMetrics:
    """Performance metrics for a benchmark run."""
    
    # Timing metrics
    total_time: float
    avg_time: float
    min_time: float
    max_time: float
    median_time: float
    p95_time: float
    p99_time: float
    
    # Throughput metrics
    operations_per_second: float
    total_operations: int
    
    # Memory metrics
    peak_memory_mb: float
    avg_memory_mb: float
    memory_growth_mb: float
    
    # CPU metrics
    avg_cpu_percent: float
    peak_cpu_percent: float
    
    # Statistical metrics
    std_deviation: float
    coefficient_of_variation: float
    
    # Additional metrics
    success_rate: float
    error_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            'timing': {
                'total_time': self.total_time,
                'avg_time': self.avg_time,
                'min_time': self.min_time,
                'max_time': self.max_time,
                'median_time': self.median_time,
                'p95_time': self.p95_time,
                'p99_time': self.p99_time,
                'std_deviation': self.std_deviation,
                'coefficient_of_variation': self.coefficient_of_variation,
            },
            'throughput': {
                'operations_per_second': self.operations_per_second,
                'total_operations': self.total_operations,
            },
            'memory': {
                'peak_memory_mb': self.peak_memory_mb,
                'avg_memory_mb': self.avg_memory_mb,
                'memory_growth_mb': self.memory_growth_mb,
            },
            'cpu': {
                'avg_cpu_percent': self.avg_cpu_percent,
                'peak_cpu_percent': self.peak_cpu_percent,
            },
            'reliability': {
                'success_rate': self.success_rate,
                'error_count': self.error_count,
            },
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""
    
    name: str
    description: str
    metrics: PerformanceMetrics
    iterations: int
    duration: float
    configuration: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    raw_timings: List[float] = field(default_factory=list)
    
    def summary(self) -> str:
        """Generate a summary string of the benchmark results."""
        return f"""
Benchmark: {self.name}
Description: {self.description}
Iterations: {self.iterations}
Duration: {self.duration:.2f}s

Performance Metrics:
  Average Time: {self.metrics.avg_time * 1000:.2f}ms
  Median Time: {self.metrics.median_time * 1000:.2f}ms
  95th Percentile: {self.metrics.p95_time * 1000:.2f}ms
  Operations/Second: {self.metrics.operations_per_second:.2f}
  
Memory Usage:
  Peak Memory: {self.metrics.peak_memory_mb:.2f}MB
  Average Memory: {self.metrics.avg_memory_mb:.2f}MB
  Memory Growth: {self.metrics.memory_growth_mb:.2f}MB
  
CPU Usage:
  Average CPU: {self.metrics.avg_cpu_percent:.1f}%
  Peak CPU: {self.metrics.peak_cpu_percent:.1f}%
  
Reliability:
  Success Rate: {self.metrics.success_rate:.1f}%
  Error Count: {self.metrics.error_count}
        """.strip()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            'name': self.name,
            'description': self.description,
            'metrics': self.metrics.to_dict(),
            'iterations': self.iterations,
            'duration': self.duration,
            'configuration': self.configuration,
            'errors': self.errors,
            'timestamp': self.metrics.timestamp.isoformat(),
        }


class ResourceMonitor:
    """Monitor system resources during benchmark execution."""
    
    def __init__(self, interval: float = 0.1):
        self.interval = interval
        self.monitoring = False
        self.cpu_readings: List[float] = []
        self.memory_readings: List[float] = []
        self._monitor_thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start resource monitoring."""
        self.monitoring = True
        self.cpu_readings.clear()
        self.memory_readings.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()
    
    def stop(self):
        """Stop resource monitoring."""
        self.monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
    
    def _monitor_loop(self):
        """Monitor loop running in separate thread."""
        while self.monitoring:
            try:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=None)
                self.cpu_readings.append(cpu_percent)
                
                # Memory usage
                memory_info = psutil.virtual_memory()
                memory_mb = (memory_info.total - memory_info.available) / (1024 * 1024)
                self.memory_readings.append(memory_mb)
                
                time.sleep(self.interval)
            except Exception:
                # Ignore monitoring errors
                pass
    
    def get_cpu_stats(self) -> Dict[str, float]:
        """Get CPU usage statistics."""
        if not self.cpu_readings:
            return {'avg': 0.0, 'peak': 0.0}
        
        return {
            'avg': statistics.mean(self.cpu_readings),
            'peak': max(self.cpu_readings),
        }
    
    def get_memory_stats(self) -> Dict[str, float]:
        """Get memory usage statistics."""
        if not self.memory_readings:
            return {'avg': 0.0, 'peak': 0.0, 'growth': 0.0}
        
        avg_memory = statistics.mean(self.memory_readings)
        peak_memory = max(self.memory_readings)
        growth = peak_memory - self.memory_readings[0] if len(self.memory_readings) > 1 else 0.0
        
        return {
            'avg': avg_memory,
            'peak': peak_memory,
            'growth': growth,
        }


class BenchmarkRunner:
    """Main benchmark runner for executing and analyzing performance tests."""
    
    def __init__(self, warmup_iterations: int = 3, min_iterations: int = 10):
        self.warmup_iterations = warmup_iterations
        self.min_iterations = min_iterations
        self.results: List[BenchmarkResult] = []
    
    def benchmark_function(
        self,
        func: Callable[..., T],
        name: str,
        description: str = "",
        iterations: Optional[int] = None,
        max_time: float = 60.0,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        warmup: bool = True,
    ) -> BenchmarkResult:
        """
        Benchmark a synchronous function.
        
        Args:
            func: Function to benchmark
            name: Benchmark name
            description: Benchmark description
            iterations: Number of iterations (auto-determined if None)
            max_time: Maximum time to run benchmark
            args: Function arguments
            kwargs: Function keyword arguments
            warmup: Whether to perform warmup runs
            
        Returns:
            BenchmarkResult with performance metrics
        """
        kwargs = kwargs or {}
        
        # Warmup
        if warmup:
            for _ in range(self.warmup_iterations):
                try:
                    func(*args, **kwargs)
                except Exception:
                    pass  # Ignore warmup errors
        
        # Force garbage collection before benchmark
        gc.collect()
        
        # Start memory tracking
        tracemalloc.start()
        initial_memory = tracemalloc.get_traced_memory()[0]
        
        # Start resource monitoring
        monitor = ResourceMonitor()
        monitor.start()
        
        # Determine iterations if not specified
        if iterations is None:
            iterations = self._determine_iterations(func, args, kwargs, max_time)
        
        # Execute benchmark
        timings = []
        errors = []
        successful_operations = 0
        
        start_time = time.perf_counter()
        
        for i in range(iterations):
            iter_start = time.perf_counter()
            try:
                func(*args, **kwargs)
                iter_end = time.perf_counter()
                timings.append(iter_end - iter_start)
                successful_operations += 1
            except Exception as e:
                iter_end = time.perf_counter()
                timings.append(iter_end - iter_start)
                errors.append(f"Iteration {i}: {str(e)}")
            
            # Check if we've exceeded max time
            if time.perf_counter() - start_time > max_time:
                break
        
        total_time = time.perf_counter() - start_time
        
        # Stop monitoring
        monitor.stop()
        
        # Get memory usage
        peak_memory = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        memory_growth = (peak_memory - initial_memory) / (1024 * 1024)  # MB
        
        # Calculate metrics
        metrics = self._calculate_metrics(
            timings=timings,
            total_time=total_time,
            successful_operations=successful_operations,
            total_operations=len(timings),
            memory_growth=memory_growth,
            cpu_stats=monitor.get_cpu_stats(),
            memory_stats=monitor.get_memory_stats(),
        )
        
        # Create result
        result = BenchmarkResult(
            name=name,
            description=description,
            metrics=metrics,
            iterations=len(timings),
            duration=total_time,
            configuration={
                'warmup_iterations': self.warmup_iterations if warmup else 0,
                'target_iterations': iterations,
                'max_time': max_time,
            },
            errors=errors,
            raw_timings=timings,
        )
        
        self.results.append(result)
        return result
    
    async def benchmark_async_function(
        self,
        func: Callable[..., T],
        name: str,
        description: str = "",
        iterations: Optional[int] = None,
        max_time: float = 60.0,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        warmup: bool = True,
        concurrent: int = 1,
    ) -> BenchmarkResult:
        """
        Benchmark an asynchronous function.
        
        Args:
            func: Async function to benchmark
            name: Benchmark name
            description: Benchmark description
            iterations: Number of iterations (auto-determined if None)
            max_time: Maximum time to run benchmark
            args: Function arguments
            kwargs: Function keyword arguments
            warmup: Whether to perform warmup runs
            concurrent: Number of concurrent executions
            
        Returns:
            BenchmarkResult with performance metrics
        """
        kwargs = kwargs or {}
        
        # Warmup
        if warmup:
            for _ in range(self.warmup_iterations):
                try:
                    await func(*args, **kwargs)
                except Exception:
                    pass  # Ignore warmup errors
        
        # Force garbage collection before benchmark
        gc.collect()
        
        # Start memory tracking
        tracemalloc.start()
        initial_memory = tracemalloc.get_traced_memory()[0]
        
        # Start resource monitoring
        monitor = ResourceMonitor()
        monitor.start()
        
        # Determine iterations if not specified
        if iterations is None:
            # Quick test to estimate timing
            test_start = time.perf_counter()
            try:
                await func(*args, **kwargs)
                test_time = time.perf_counter() - test_start
                iterations = min(max(int(max_time / (test_time * 10)), self.min_iterations), 1000)
            except Exception:
                iterations = self.min_iterations
        
        # Execute benchmark
        timings = []
        errors = []
        successful_operations = 0
        
        start_time = time.perf_counter()
        
        if concurrent == 1:
            # Sequential execution
            for i in range(iterations):
                iter_start = time.perf_counter()
                try:
                    await func(*args, **kwargs)
                    iter_end = time.perf_counter()
                    timings.append(iter_end - iter_start)
                    successful_operations += 1
                except Exception as e:
                    iter_end = time.perf_counter()
                    timings.append(iter_end - iter_start)
                    errors.append(f"Iteration {i}: {str(e)}")
                
                if time.perf_counter() - start_time > max_time:
                    break
        else:
            # Concurrent execution
            semaphore = asyncio.Semaphore(concurrent)
            
            async def execute_iteration(iteration_id: int):
                async with semaphore:
                    iter_start = time.perf_counter()
                    try:
                        await func(*args, **kwargs)
                        iter_end = time.perf_counter()
                        return iter_end - iter_start, None
                    except Exception as e:
                        iter_end = time.perf_counter()
                        return iter_end - iter_start, f"Iteration {iteration_id}: {str(e)}"
            
            # Execute all iterations concurrently
            tasks = [execute_iteration(i) for i in range(iterations)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    errors.append(f"Task failed: {str(result)}")
                    continue
                
                timing, error = result
                timings.append(timing)
                if error:
                    errors.append(error)
                else:
                    successful_operations += 1
        
        total_time = time.perf_counter() - start_time
        
        # Stop monitoring
        monitor.stop()
        
        # Get memory usage
        peak_memory = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        memory_growth = (peak_memory - initial_memory) / (1024 * 1024)  # MB
        
        # Calculate metrics
        metrics = self._calculate_metrics(
            timings=timings,
            total_time=total_time,
            successful_operations=successful_operations,
            total_operations=len(timings),
            memory_growth=memory_growth,
            cpu_stats=monitor.get_cpu_stats(),
            memory_stats=monitor.get_memory_stats(),
        )
        
        # Create result
        result = BenchmarkResult(
            name=name,
            description=description,
            metrics=metrics,
            iterations=len(timings),
            duration=total_time,
            configuration={
                'warmup_iterations': self.warmup_iterations if warmup else 0,
                'target_iterations': iterations,
                'max_time': max_time,
                'concurrent': concurrent,
            },
            errors=errors,
            raw_timings=timings,
        )
        
        self.results.append(result)
        return result
    
    def _determine_iterations(
        self,
        func: Callable,
        args: tuple,
        kwargs: Dict[str, Any],
        max_time: float
    ) -> int:
        """Determine optimal number of iterations based on function timing."""
        # Quick test to estimate timing
        test_start = time.perf_counter()
        try:
            func(*args, **kwargs)
            test_time = time.perf_counter() - test_start
        except Exception:
            return self.min_iterations
        
        if test_time == 0:
            return 1000  # Very fast function
        
        # Target spending about 10% of max_time on measurement
        target_time = max_time * 0.1
        estimated_iterations = int(target_time / test_time)
        
        return max(min(estimated_iterations, 1000), self.min_iterations)
    
    def _calculate_metrics(
        self,
        timings: List[float],
        total_time: float,
        successful_operations: int,
        total_operations: int,
        memory_growth: float,
        cpu_stats: Dict[str, float],
        memory_stats: Dict[str, float],
    ) -> PerformanceMetrics:
        """Calculate performance metrics from timing data."""
        if not timings:
            # Handle case with no successful timings
            return PerformanceMetrics(
                total_time=total_time,
                avg_time=0.0,
                min_time=0.0,
                max_time=0.0,
                median_time=0.0,
                p95_time=0.0,
                p99_time=0.0,
                operations_per_second=0.0,
                total_operations=total_operations,
                peak_memory_mb=memory_stats.get('peak', 0.0),
                avg_memory_mb=memory_stats.get('avg', 0.0),
                memory_growth_mb=memory_growth,
                avg_cpu_percent=cpu_stats.get('avg', 0.0),
                peak_cpu_percent=cpu_stats.get('peak', 0.0),
                std_deviation=0.0,
                coefficient_of_variation=0.0,
                success_rate=0.0,
                error_count=total_operations,
            )
        
        # Sort timings for percentile calculations
        sorted_timings = sorted(timings)
        
        # Calculate statistics
        avg_time = statistics.mean(timings)
        min_time = min(timings)
        max_time = max(timings)
        median_time = statistics.median(timings)
        
        # Percentiles
        p95_index = int(0.95 * len(sorted_timings))
        p99_index = int(0.99 * len(sorted_timings))
        p95_time = sorted_timings[min(p95_index, len(sorted_timings) - 1)]
        p99_time = sorted_timings[min(p99_index, len(sorted_timings) - 1)]
        
        # Standard deviation and coefficient of variation
        std_dev = statistics.stdev(timings) if len(timings) > 1 else 0.0
        cv = (std_dev / avg_time) if avg_time > 0 else 0.0
        
        # Throughput
        ops_per_second = successful_operations / total_time if total_time > 0 else 0.0
        
        # Success rate
        success_rate = (successful_operations / total_operations * 100) if total_operations > 0 else 0.0
        
        return PerformanceMetrics(
            total_time=total_time,
            avg_time=avg_time,
            min_time=min_time,
            max_time=max_time,
            median_time=median_time,
            p95_time=p95_time,
            p99_time=p99_time,
            operations_per_second=ops_per_second,
            total_operations=total_operations,
            peak_memory_mb=memory_stats.get('peak', 0.0),
            avg_memory_mb=memory_stats.get('avg', 0.0),
            memory_growth_mb=memory_growth,
            avg_cpu_percent=cpu_stats.get('avg', 0.0),
            peak_cpu_percent=cpu_stats.get('peak', 0.0),
            std_deviation=std_dev,
            coefficient_of_variation=cv,
            success_rate=success_rate,
            error_count=total_operations - successful_operations,
        )
    
    def compare_results(self, baseline: str, comparison: str) -> Dict[str, Any]:
        """Compare two benchmark results."""
        baseline_result = None
        comparison_result = None
        
        for result in self.results:
            if result.name == baseline:
                baseline_result = result
            elif result.name == comparison:
                comparison_result = result
        
        if not baseline_result or not comparison_result:
            raise ValueError("Baseline or comparison benchmark not found")
        
        baseline_metrics = baseline_result.metrics
        comparison_metrics = comparison_result.metrics
        
        return {
            'baseline': baseline,
            'comparison': comparison,
            'improvements': {
                'avg_time': self._calculate_improvement(
                    baseline_metrics.avg_time,
                    comparison_metrics.avg_time,
                    lower_is_better=True
                ),
                'operations_per_second': self._calculate_improvement(
                    baseline_metrics.operations_per_second,
                    comparison_metrics.operations_per_second,
                    lower_is_better=False
                ),
                'memory_usage': self._calculate_improvement(
                    baseline_metrics.peak_memory_mb,
                    comparison_metrics.peak_memory_mb,
                    lower_is_better=True
                ),
                'cpu_usage': self._calculate_improvement(
                    baseline_metrics.avg_cpu_percent,
                    comparison_metrics.avg_cpu_percent,
                    lower_is_better=True
                ),
            }
        }
    
    def _calculate_improvement(
        self,
        baseline: float,
        comparison: float,
        lower_is_better: bool = True
    ) -> Dict[str, Any]:
        """Calculate improvement percentage between two values."""
        if baseline == 0:
            return {'percentage': 0.0, 'direction': 'unchanged'}
        
        percentage = ((comparison - baseline) / baseline) * 100
        
        if lower_is_better:
            # For metrics where lower is better (time, memory usage)
            if percentage < 0:
                direction = 'improved'
                percentage = abs(percentage)
            elif percentage > 0:
                direction = 'regressed'
            else:
                direction = 'unchanged'
        else:
            # For metrics where higher is better (throughput)
            if percentage > 0:
                direction = 'improved'
            elif percentage < 0:
                direction = 'regressed'
                percentage = abs(percentage)
            else:
                direction = 'unchanged'
        
        return {
            'percentage': percentage,
            'direction': direction,
            'baseline_value': baseline,
            'comparison_value': comparison,
        }
    
    def export_results(self, filename: str, format: str = 'json'):
        """Export benchmark results to file."""
        if format.lower() == 'json':
            data = [result.to_dict() for result in self.results]
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported export format: {format}")
    
    def get_summary_report(self) -> str:
        """Generate a summary report of all benchmark results."""
        if not self.results:
            return "No benchmark results available."
        
        report_lines = ["Benchmark Summary Report", "=" * 50, ""]
        
        for result in self.results:
            report_lines.extend([
                f"Benchmark: {result.name}",
                f"  Average Time: {result.metrics.avg_time * 1000:.2f}ms",
                f"  Operations/Second: {result.metrics.operations_per_second:.2f}",
                f"  Success Rate: {result.metrics.success_rate:.1f}%",
                f"  Peak Memory: {result.metrics.peak_memory_mb:.2f}MB",
                ""
            ])
        
        return "\n".join(report_lines)


# Decorator functions for easy benchmarking
def benchmark_function(
    name: str,
    description: str = "",
    iterations: Optional[int] = None,
    runner: Optional[BenchmarkRunner] = None
):
    """Decorator to benchmark a function."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            benchmark_runner = runner or BenchmarkRunner()
            result = benchmark_runner.benchmark_function(
                func=func,
                name=name,
                description=description,
                iterations=iterations,
                args=args,
                kwargs=kwargs
            )
            print(result.summary())
            return func(*args, **kwargs)
        return wrapper
    return decorator


def benchmark_async_function(
    name: str,
    description: str = "",
    iterations: Optional[int] = None,
    concurrent: int = 1,
    runner: Optional[BenchmarkRunner] = None
):
    """Decorator to benchmark an async function."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            benchmark_runner = runner or BenchmarkRunner()
            result = await benchmark_runner.benchmark_async_function(
                func=func,
                name=name,
                description=description,
                iterations=iterations,
                concurrent=concurrent,
                args=args,
                kwargs=kwargs
            )
            print(result.summary())
            return await func(*args, **kwargs)
        return wrapper
    return decorator