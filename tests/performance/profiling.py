"""
Profiling utilities for detailed performance analysis.

Provides tools for CPU profiling, memory profiling, network analysis,
and performance bottleneck identification.
"""

import cProfile
import pstats
import io
import tracemalloc
import psutil
import threading
import time
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable, Union
from dataclasses import dataclass, field
import json
import gc
from pathlib import Path


@dataclass
class ProfileResult:
    """Result of a profiling session."""
    
    name: str
    start_time: datetime
    end_time: datetime
    duration: float
    
    # CPU profiling data
    cpu_stats: Optional[pstats.Stats] = None
    top_functions: List[Dict[str, Any]] = field(default_factory=list)
    
    # Memory profiling data
    memory_peak: float = 0.0
    memory_growth: float = 0.0
    memory_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    
    # System resource data
    cpu_usage: List[float] = field(default_factory=list)
    memory_usage: List[float] = field(default_factory=list)
    io_stats: Dict[str, Any] = field(default_factory=dict)
    
    def summary(self) -> str:
        """Generate a summary of profiling results."""
        return f"""
Profile: {self.name}
Duration: {self.duration:.2f}s
Start: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}
End: {self.end_time.strftime('%Y-%m-%d %H:%M:%S')}

Memory Analysis:
  Peak Memory: {self.memory_peak:.2f} MB
  Memory Growth: {self.memory_growth:.2f} MB
  Snapshots Taken: {len(self.memory_snapshots)}

CPU Analysis:
  Top Functions: {len(self.top_functions)}
  Average CPU Usage: {sum(self.cpu_usage) / len(self.cpu_usage) if self.cpu_usage else 0:.1f}%

Top CPU Consumers:
{self._format_top_functions()}
        """.strip()
    
    def _format_top_functions(self) -> str:
        """Format top CPU consuming functions."""
        if not self.top_functions:
            return "  No CPU profiling data available"
        
        lines = []
        for func in self.top_functions[:10]:  # Top 10
            lines.append(f"  {func['function']}: {func['cumulative_time']:.4f}s ({func['calls']} calls)")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert profile result to dictionary."""
        return {
            'name': self.name,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'duration': self.duration,
            'memory_peak': self.memory_peak,
            'memory_growth': self.memory_growth,
            'memory_snapshots': self.memory_snapshots,
            'top_functions': self.top_functions,
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'io_stats': self.io_stats,
        }


class CPUProfiler:
    """CPU profiling using cProfile."""
    
    def __init__(self):
        self.profiler: Optional[cProfile.Profile] = None
        self.is_running = False
    
    def start(self):
        """Start CPU profiling."""
        if self.is_running:
            raise RuntimeError("Profiler is already running")
        
        self.profiler = cProfile.Profile()
        self.profiler.enable()
        self.is_running = True
    
    def stop(self) -> pstats.Stats:
        """Stop CPU profiling and return stats."""
        if not self.is_running or not self.profiler:
            raise RuntimeError("Profiler is not running")
        
        self.profiler.disable()
        self.is_running = False
        
        # Create stats object
        stats_stream = io.StringIO()
        stats = pstats.Stats(self.profiler, stream=stats_stream)
        
        return stats
    
    def profile_function(self, func: Callable, *args, **kwargs) -> tuple:
        """Profile a function call and return result and stats."""
        self.start()
        try:
            result = func(*args, **kwargs)
            return result, self.stop()
        except Exception as e:
            if self.is_running:
                self.stop()
            raise e
    
    async def profile_async_function(self, func: Callable, *args, **kwargs) -> tuple:
        """Profile an async function call and return result and stats."""
        self.start()
        try:
            result = await func(*args, **kwargs)
            return result, self.stop()
        except Exception as e:
            if self.is_running:
                self.stop()
            raise e
    
    def extract_top_functions(self, stats: pstats.Stats, limit: int = 20) -> List[Dict[str, Any]]:
        """Extract top functions from profiling stats."""
        # Sort by cumulative time
        stats.sort_stats(pstats.SortKey.CUMULATIVE)
        
        functions = []
        for (filename, line, function), (calls, total_calls, total_time, cumulative_time) in stats.stats.items():
            functions.append({
                'filename': filename,
                'line': line,
                'function': function,
                'calls': calls,
                'total_calls': total_calls,
                'total_time': total_time,
                'cumulative_time': cumulative_time,
                'time_per_call': total_time / calls if calls > 0 else 0,
                'cumulative_per_call': cumulative_time / calls if calls > 0 else 0,
            })
        
        # Sort by cumulative time and return top functions
        functions.sort(key=lambda x: x['cumulative_time'], reverse=True)
        return functions[:limit]


class MemoryProfiler:
    """Memory profiling using tracemalloc."""
    
    def __init__(self):
        self.is_running = False
        self.start_snapshot: Optional[tracemalloc.Snapshot] = None
        self.snapshots: List[Dict[str, Any]] = []
    
    def start(self):
        """Start memory profiling."""
        if self.is_running:
            raise RuntimeError("Memory profiler is already running")
        
        tracemalloc.start()
        self.start_snapshot = tracemalloc.take_snapshot()
        self.snapshots.clear()
        self.is_running = True
    
    def stop(self) -> Dict[str, Any]:
        """Stop memory profiling and return analysis."""
        if not self.is_running:
            raise RuntimeError("Memory profiler is not running")
        
        end_snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()
        self.is_running = False
        
        # Analyze memory usage
        analysis = self._analyze_memory_usage(self.start_snapshot, end_snapshot)
        return analysis
    
    def take_snapshot(self, label: str = ""):
        """Take a memory snapshot during profiling."""
        if not self.is_running:
            return
        
        snapshot = tracemalloc.take_snapshot()
        current_memory, peak_memory = tracemalloc.get_traced_memory()
        
        self.snapshots.append({
            'timestamp': time.time(),
            'label': label,
            'current_memory': current_memory / (1024 * 1024),  # MB
            'peak_memory': peak_memory / (1024 * 1024),  # MB
            'snapshot': snapshot,
        })
    
    def _analyze_memory_usage(
        self,
        start_snapshot: tracemalloc.Snapshot,
        end_snapshot: tracemalloc.Snapshot
    ) -> Dict[str, Any]:
        """Analyze memory usage between snapshots."""
        top_stats = end_snapshot.compare_to(start_snapshot, 'lineno')
        
        total_growth = 0
        top_allocations = []
        
        for stat in top_stats[:20]:  # Top 20 allocations
            total_growth += stat.size_diff
            
            top_allocations.append({
                'filename': stat.traceback.format()[0] if stat.traceback else 'unknown',
                'size_diff': stat.size_diff / (1024 * 1024),  # MB
                'size': stat.size / (1024 * 1024),  # MB
                'count_diff': stat.count_diff,
                'count': stat.count,
            })
        
        return {
            'total_growth_mb': total_growth / (1024 * 1024),
            'top_allocations': top_allocations,
            'snapshots_taken': len(self.snapshots),
        }


class SystemResourceMonitor:
    """Monitor system resources during profiling."""
    
    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.monitoring = False
        self.cpu_readings: List[float] = []
        self.memory_readings: List[float] = []
        self.io_readings: List[Dict[str, Any]] = []
        self._monitor_thread: Optional[threading.Thread] = None
        self._initial_io_counters: Optional[psutil._pslinux.pio] = None
    
    def start(self):
        """Start resource monitoring."""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.cpu_readings.clear()
        self.memory_readings.clear()
        self.io_readings.clear()
        
        # Get initial I/O counters
        try:
            self._initial_io_counters = psutil.Process().io_counters()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._initial_io_counters = None
        
        self._monitor_thread = threading.Thread(target=self._monitor_loop)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()
    
    def stop(self) -> Dict[str, Any]:
        """Stop resource monitoring and return statistics."""
        self.monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        
        return {
            'cpu_usage': self.cpu_readings.copy(),
            'memory_usage': self.memory_readings.copy(),
            'io_stats': self._calculate_io_stats(),
        }
    
    def _monitor_loop(self):
        """Resource monitoring loop."""
        while self.monitoring:
            try:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=None)
                self.cpu_readings.append(cpu_percent)
                
                # Memory usage (current process)
                process = psutil.Process()
                memory_info = process.memory_info()
                memory_mb = memory_info.rss / (1024 * 1024)
                self.memory_readings.append(memory_mb)
                
                # I/O stats (if available)
                try:
                    io_counters = process.io_counters()
                    self.io_readings.append({
                        'timestamp': time.time(),
                        'read_count': io_counters.read_count,
                        'write_count': io_counters.write_count,
                        'read_bytes': io_counters.read_bytes,
                        'write_bytes': io_counters.write_bytes,
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                
                time.sleep(self.interval)
                
            except Exception:
                # Continue monitoring even if individual readings fail
                pass
    
    def _calculate_io_stats(self) -> Dict[str, Any]:
        """Calculate I/O statistics."""
        if not self.io_readings:
            return {}
        
        first_reading = self.io_readings[0]
        last_reading = self.io_readings[-1]
        
        return {
            'total_read_operations': last_reading['read_count'] - first_reading['read_count'],
            'total_write_operations': last_reading['write_count'] - first_reading['write_count'],
            'total_bytes_read': last_reading['read_bytes'] - first_reading['read_bytes'],
            'total_bytes_written': last_reading['write_bytes'] - first_reading['write_bytes'],
            'monitoring_duration': last_reading['timestamp'] - first_reading['timestamp'],
        }


class NetworkProfiler:
    """Network I/O profiling and analysis."""
    
    def __init__(self):
        self.is_monitoring = False
        self.network_stats: List[Dict[str, Any]] = []
        self._initial_stats: Optional[Dict[str, Any]] = None
    
    def start(self):
        """Start network monitoring."""
        if self.is_monitoring:
            return
        
        self.is_monitoring = True
        self.network_stats.clear()
        
        # Get initial network stats
        try:
            net_io = psutil.net_io_counters()
            self._initial_stats = {
                'bytes_sent': net_io.bytes_sent,
                'bytes_recv': net_io.bytes_recv,
                'packets_sent': net_io.packets_sent,
                'packets_recv': net_io.packets_recv,
                'timestamp': time.time(),
            }
        except Exception:
            self._initial_stats = None
    
    def stop(self) -> Dict[str, Any]:
        """Stop network monitoring and return analysis."""
        if not self.is_monitoring:
            return {}
        
        self.is_monitoring = False
        
        # Get final network stats
        try:
            net_io = psutil.net_io_counters()
            final_stats = {
                'bytes_sent': net_io.bytes_sent,
                'bytes_recv': net_io.bytes_recv,
                'packets_sent': net_io.packets_sent,
                'packets_recv': net_io.packets_recv,
                'timestamp': time.time(),
            }
            
            if self._initial_stats:
                return {
                    'bytes_sent': final_stats['bytes_sent'] - self._initial_stats['bytes_sent'],
                    'bytes_received': final_stats['bytes_recv'] - self._initial_stats['bytes_recv'],
                    'packets_sent': final_stats['packets_sent'] - self._initial_stats['packets_sent'],
                    'packets_received': final_stats['packets_recv'] - self._initial_stats['packets_recv'],
                    'duration': final_stats['timestamp'] - self._initial_stats['timestamp'],
                    'bytes_per_second_sent': (final_stats['bytes_sent'] - self._initial_stats['bytes_sent']) / 
                                           max(1, final_stats['timestamp'] - self._initial_stats['timestamp']),
                    'bytes_per_second_received': (final_stats['bytes_recv'] - self._initial_stats['bytes_recv']) / 
                                               max(1, final_stats['timestamp'] - self._initial_stats['timestamp']),
                }
            
        except Exception:
            pass
        
        return {}


class ProfileRunner:
    """Main profiling orchestrator."""
    
    def __init__(self):
        self.results: List[ProfileResult] = []
    
    def profile_function(
        self,
        func: Callable,
        name: str,
        *args,
        enable_cpu: bool = True,
        enable_memory: bool = True,
        enable_system: bool = True,
        enable_network: bool = False,
        **kwargs
    ) -> ProfileResult:
        """Profile a synchronous function comprehensively."""
        start_time = datetime.now(timezone.utc)
        
        # Initialize profilers
        cpu_profiler = CPUProfiler() if enable_cpu else None
        memory_profiler = MemoryProfiler() if enable_memory else None
        system_monitor = SystemResourceMonitor() if enable_system else None
        network_profiler = NetworkProfiler() if enable_network else None
        
        try:
            # Start profiling
            if cpu_profiler:
                cpu_profiler.start()
            if memory_profiler:
                memory_profiler.start()
            if system_monitor:
                system_monitor.start()
            if network_profiler:
                network_profiler.start()
            
            # Execute function
            profile_start = time.time()
            result = func(*args, **kwargs)
            profile_duration = time.time() - profile_start
            
            # Stop profiling and collect results
            end_time = datetime.now(timezone.utc)
            
            cpu_stats = None
            top_functions = []
            if cpu_profiler:
                cpu_stats = cpu_profiler.stop()
                top_functions = cpu_profiler.extract_top_functions(cpu_stats)
            
            memory_analysis = {}
            if memory_profiler:
                memory_analysis = memory_profiler.stop()
            
            system_stats = {}
            if system_monitor:
                system_stats = system_monitor.stop()
            
            network_stats = {}
            if network_profiler:
                network_stats = network_profiler.stop()
            
            # Create profile result
            profile_result = ProfileResult(
                name=name,
                start_time=start_time,
                end_time=end_time,
                duration=profile_duration,
                cpu_stats=cpu_stats,
                top_functions=top_functions,
                memory_peak=memory_analysis.get('total_growth_mb', 0.0),
                memory_growth=memory_analysis.get('total_growth_mb', 0.0),
                memory_snapshots=memory_analysis.get('top_allocations', []),
                cpu_usage=system_stats.get('cpu_usage', []),
                memory_usage=system_stats.get('memory_usage', []),
                io_stats={**system_stats.get('io_stats', {}), **network_stats},
            )
            
            self.results.append(profile_result)
            return profile_result
            
        except Exception as e:
            # Cleanup profilers on error
            if cpu_profiler and cpu_profiler.is_running:
                cpu_profiler.stop()
            if memory_profiler and memory_profiler.is_running:
                memory_profiler.stop()
            if system_monitor and system_monitor.monitoring:
                system_monitor.stop()
            if network_profiler and network_profiler.is_monitoring:
                network_profiler.stop()
            raise e
    
    async def profile_async_function(
        self,
        func: Callable,
        name: str,
        *args,
        enable_cpu: bool = True,
        enable_memory: bool = True,
        enable_system: bool = True,
        enable_network: bool = False,
        **kwargs
    ) -> ProfileResult:
        """Profile an asynchronous function comprehensively."""
        start_time = datetime.now(timezone.utc)
        
        # Initialize profilers
        cpu_profiler = CPUProfiler() if enable_cpu else None
        memory_profiler = MemoryProfiler() if enable_memory else None
        system_monitor = SystemResourceMonitor() if enable_system else None
        network_profiler = NetworkProfiler() if enable_network else None
        
        try:
            # Start profiling
            if cpu_profiler:
                cpu_profiler.start()
            if memory_profiler:
                memory_profiler.start()
            if system_monitor:
                system_monitor.start()
            if network_profiler:
                network_profiler.start()
            
            # Execute async function
            profile_start = time.time()
            result = await func(*args, **kwargs)
            profile_duration = time.time() - profile_start
            
            # Stop profiling and collect results
            end_time = datetime.now(timezone.utc)
            
            cpu_stats = None
            top_functions = []
            if cpu_profiler:
                cpu_stats = cpu_profiler.stop()
                top_functions = cpu_profiler.extract_top_functions(cpu_stats)
            
            memory_analysis = {}
            if memory_profiler:
                memory_analysis = memory_profiler.stop()
            
            system_stats = {}
            if system_monitor:
                system_stats = system_monitor.stop()
            
            network_stats = {}
            if network_profiler:
                network_stats = network_profiler.stop()
            
            # Create profile result
            profile_result = ProfileResult(
                name=name,
                start_time=start_time,
                end_time=end_time,
                duration=profile_duration,
                cpu_stats=cpu_stats,
                top_functions=top_functions,
                memory_peak=memory_analysis.get('total_growth_mb', 0.0),
                memory_growth=memory_analysis.get('total_growth_mb', 0.0),
                memory_snapshots=memory_analysis.get('top_allocations', []),
                cpu_usage=system_stats.get('cpu_usage', []),
                memory_usage=system_stats.get('memory_usage', []),
                io_stats={**system_stats.get('io_stats', {}), **network_stats},
            )
            
            self.results.append(profile_result)
            return profile_result
            
        except Exception as e:
            # Cleanup profilers on error
            if cpu_profiler and cpu_profiler.is_running:
                cpu_profiler.stop()
            if memory_profiler and memory_profiler.is_running:
                memory_profiler.stop()
            if system_monitor and system_monitor.monitoring:
                system_monitor.stop()
            if network_profiler and network_profiler.is_monitoring:
                network_profiler.stop()
            raise e
    
    def export_profile_data(self, filename: str, format: str = 'json'):
        """Export profiling data to file."""
        if format.lower() == 'json':
            data = [result.to_dict() for result in self.results]
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported export format: {format}")
    
    def get_summary_report(self) -> str:
        """Generate summary report of all profiling results."""
        if not self.results:
            return "No profiling results available."
        
        lines = ["Profiling Summary Report", "=" * 50, ""]
        
        for result in self.results:
            lines.extend([
                f"Profile: {result.name}",
                f"  Duration: {result.duration:.3f}s",
                f"  Memory Peak: {result.memory_peak:.2f}MB",
                f"  Top Function: {result.top_functions[0]['function'] if result.top_functions else 'N/A'}",
                f"  Avg CPU: {sum(result.cpu_usage) / len(result.cpu_usage) if result.cpu_usage else 0:.1f}%",
                ""
            ])
        
        return "\n".join(lines)