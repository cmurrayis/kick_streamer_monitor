"""
Event processing pipeline with deduplication and batching.

Provides efficient event processing with deduplication, batching, filtering,
and transformation capabilities for the Kick streamer monitoring system.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import (
    Any, Dict, List, Optional, Set, Callable, Union, TypeVar, Generic,
    Awaitable, Tuple, Iterator
)
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict, deque
import json

from ..models import StatusEvent, StatusEventCreate, EventType, StreamerStatus
from .errors import MonitoringError, ErrorCategory, ErrorSeverity

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ProcessingStage(str, Enum):
    """Event processing stages."""
    RAW = "raw"                    # Raw event received
    FILTERED = "filtered"          # After filtering rules applied
    DEDUPLICATED = "deduplicated"  # After deduplication
    TRANSFORMED = "transformed"    # After transformation
    VALIDATED = "validated"        # After validation
    BATCHED = "batched"           # Ready for batch processing
    PROCESSED = "processed"        # Successfully processed


class DeduplicationStrategy(str, Enum):
    """Deduplication strategies."""
    CONTENT_HASH = "content_hash"      # Based on event content hash
    TIME_WINDOW = "time_window"        # Based on time window
    EVENT_ID = "event_id"             # Based on external event ID
    COMPOSITE = "composite"            # Combination of strategies


class BatchStrategy(str, Enum):
    """Batching strategies."""
    SIZE_BASED = "size_based"          # Batch when size reached
    TIME_BASED = "time_based"          # Batch when time elapsed
    ADAPTIVE = "adaptive"              # Adaptive based on load
    IMMEDIATE = "immediate"            # Process immediately (no batching)


@dataclass
class EventMetadata:
    """Metadata for event processing."""
    event_id: str
    received_at: datetime
    stage: ProcessingStage = ProcessingStage.RAW
    source: str = "websocket"
    processing_attempts: int = 0
    last_error: Optional[str] = None
    processing_time_ms: float = 0.0
    dedup_key: Optional[str] = None
    batch_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'event_id': self.event_id,
            'received_at': self.received_at.isoformat(),
            'stage': self.stage,
            'source': self.source,
            'processing_attempts': self.processing_attempts,
            'last_error': self.last_error,
            'processing_time_ms': self.processing_time_ms,
            'dedup_key': self.dedup_key,
            'batch_id': self.batch_id
        }


@dataclass
class ProcessedEvent:
    """Wrapper for events with processing metadata."""
    event: StatusEventCreate
    metadata: EventMetadata
    
    @property
    def event_id(self) -> str:
        return self.metadata.event_id
    
    @property
    def stage(self) -> ProcessingStage:
        return self.metadata.stage
    
    def advance_stage(self, stage: ProcessingStage) -> None:
        """Advance to next processing stage."""
        self.metadata.stage = stage


@dataclass
class ProcessingStats:
    """Statistics for event processing."""
    total_received: int = 0
    total_processed: int = 0
    total_dropped: int = 0
    total_errors: int = 0
    duplicate_events: int = 0
    batches_processed: int = 0
    avg_processing_time_ms: float = 0.0
    events_by_stage: Dict[ProcessingStage, int] = field(default_factory=lambda: defaultdict(int))
    errors_by_type: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    throughput_per_minute: float = 0.0
    
    def record_event(self, stage: ProcessingStage) -> None:
        """Record an event at specific stage."""
        self.events_by_stage[stage] += 1
    
    def record_processing_time(self, time_ms: float) -> None:
        """Record processing time and update average."""
        if self.total_processed == 0:
            self.avg_processing_time_ms = time_ms
        else:
            # Running average
            self.avg_processing_time_ms = (
                (self.avg_processing_time_ms * self.total_processed + time_ms) /
                (self.total_processed + 1)
            )


class EventFilter:
    """Filter for events based on various criteria."""
    
    def __init__(self):
        self._filters: List[Callable[[StatusEventCreate], bool]] = []
    
    def add_filter(self, filter_func: Callable[[StatusEventCreate], bool]) -> None:
        """Add a filter function."""
        self._filters.append(filter_func)
    
    def add_streamer_filter(self, allowed_streamers: Set[int]) -> None:
        """Add filter for specific streamers."""
        def streamer_filter(event: StatusEventCreate) -> bool:
            return event.streamer_id in allowed_streamers
        self.add_filter(streamer_filter)
    
    def add_event_type_filter(self, allowed_types: Set[EventType]) -> None:
        """Add filter for specific event types."""
        def type_filter(event: StatusEventCreate) -> bool:
            return event.event_type in allowed_types
        self.add_filter(type_filter)
    
    def add_time_window_filter(self, max_age_seconds: int) -> None:
        """Add filter for events within time window."""
        def time_filter(event: StatusEventCreate) -> bool:
            age = datetime.now(timezone.utc) - event.event_timestamp
            return age.total_seconds() <= max_age_seconds
        self.add_filter(time_filter)
    
    def should_process(self, event: StatusEventCreate) -> bool:
        """Check if event should be processed."""
        return all(filter_func(event) for filter_func in self._filters)


class EventDeduplicator:
    """Deduplicates events based on various strategies."""
    
    def __init__(
        self,
        strategy: DeduplicationStrategy = DeduplicationStrategy.COMPOSITE,
        time_window_seconds: int = 300,
        max_cache_size: int = 10000
    ):
        self.strategy = strategy
        self.time_window_seconds = time_window_seconds
        self.max_cache_size = max_cache_size
        
        # Deduplication caches
        self._content_hashes: Set[str] = set()
        self._event_ids: Set[str] = set()
        self._time_window_cache: deque = deque()
        self._composite_keys: Set[str] = set()
        
        # Cleanup tracking
        self._last_cleanup = datetime.now(timezone.utc)
    
    def generate_dedup_key(self, event: StatusEventCreate) -> str:
        """Generate deduplication key for event."""
        if self.strategy == DeduplicationStrategy.CONTENT_HASH:
            return self._generate_content_hash(event)
        elif self.strategy == DeduplicationStrategy.EVENT_ID:
            return event.kick_event_id or "no_id"
        elif self.strategy == DeduplicationStrategy.TIME_WINDOW:
            return f"{event.streamer_id}_{event.event_type}_{int(event.event_timestamp.timestamp())}"
        else:  # COMPOSITE
            return self._generate_composite_key(event)
    
    def _generate_content_hash(self, event: StatusEventCreate) -> str:
        """Generate content-based hash."""
        content = {
            'streamer_id': event.streamer_id,
            'event_type': event.event_type.value,
            'previous_status': event.previous_status.value,
            'new_status': event.new_status.value,
            'timestamp': int(event.event_timestamp.timestamp() / 10)  # 10-second buckets
        }
        
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()[:16]
    
    def _generate_composite_key(self, event: StatusEventCreate) -> str:
        """Generate composite deduplication key."""
        # Combine multiple approaches
        base_key = f"{event.streamer_id}_{event.event_type.value}"
        
        # Add event ID if available
        if event.kick_event_id:
            base_key += f"_{event.kick_event_id}"
        
        # Add time bucket (5-second windows)
        time_bucket = int(event.event_timestamp.timestamp() / 5)
        base_key += f"_{time_bucket}"
        
        return base_key
    
    def is_duplicate(self, event: StatusEventCreate, dedup_key: str) -> bool:
        """Check if event is a duplicate."""
        self._cleanup_old_entries()
        
        if self.strategy == DeduplicationStrategy.CONTENT_HASH:
            return dedup_key in self._content_hashes
        elif self.strategy == DeduplicationStrategy.EVENT_ID:
            return dedup_key in self._event_ids
        elif self.strategy == DeduplicationStrategy.TIME_WINDOW:
            return self._check_time_window_duplicate(event, dedup_key)
        else:  # COMPOSITE
            return dedup_key in self._composite_keys
    
    def _check_time_window_duplicate(self, event: StatusEventCreate, dedup_key: str) -> bool:
        """Check for duplicates in time window."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.time_window_seconds)
        
        # Check recent events
        for cached_time, cached_key in self._time_window_cache:
            if cached_time < cutoff:
                continue
            if cached_key == dedup_key:
                return True
        
        return False
    
    def record_event(self, event: StatusEventCreate, dedup_key: str) -> None:
        """Record event to prevent future duplicates."""
        now = datetime.now(timezone.utc)
        
        if self.strategy == DeduplicationStrategy.CONTENT_HASH:
            self._content_hashes.add(dedup_key)
        elif self.strategy == DeduplicationStrategy.EVENT_ID:
            self._event_ids.add(dedup_key)
        elif self.strategy == DeduplicationStrategy.TIME_WINDOW:
            self._time_window_cache.append((now, dedup_key))
        else:  # COMPOSITE
            self._composite_keys.add(dedup_key)
    
    def _cleanup_old_entries(self) -> None:
        """Clean up old deduplication entries."""
        now = datetime.now(timezone.utc)
        
        # Only cleanup every 5 minutes
        if (now - self._last_cleanup).total_seconds() < 300:
            return
        
        cutoff = now - timedelta(seconds=self.time_window_seconds)
        
        # Clean time window cache
        while self._time_window_cache and self._time_window_cache[0][0] < cutoff:
            self._time_window_cache.popleft()
        
        # Limit cache sizes
        if len(self._content_hashes) > self.max_cache_size:
            # Remove oldest 20%
            remove_count = len(self._content_hashes) // 5
            for _ in range(remove_count):
                self._content_hashes.pop()
        
        if len(self._composite_keys) > self.max_cache_size:
            remove_count = len(self._composite_keys) // 5
            for _ in range(remove_count):
                self._composite_keys.pop()
        
        self._last_cleanup = now
    
    def get_stats(self) -> Dict[str, Any]:
        """Get deduplication statistics."""
        return {
            'strategy': self.strategy,
            'cache_sizes': {
                'content_hashes': len(self._content_hashes),
                'event_ids': len(self._event_ids),
                'time_window': len(self._time_window_cache),
                'composite_keys': len(self._composite_keys)
            },
            'time_window_seconds': self.time_window_seconds,
            'last_cleanup': self._last_cleanup.isoformat()
        }


class EventBatcher:
    """Batches events for efficient processing."""
    
    def __init__(
        self,
        strategy: BatchStrategy = BatchStrategy.ADAPTIVE,
        max_batch_size: int = 50,
        max_wait_time: float = 2.0,
        min_batch_size: int = 1
    ):
        self.strategy = strategy
        self.max_batch_size = max_batch_size
        self.max_wait_time = max_wait_time
        self.min_batch_size = min_batch_size
        
        # Batching state
        self._current_batch: List[ProcessedEvent] = []
        self._batch_start_time: Optional[datetime] = None
        self._batch_id_counter = 0
        
        # Adaptive parameters
        self._recent_batch_times: deque = deque(maxlen=10)
        self._load_factor = 1.0
    
    def add_event(self, event: ProcessedEvent) -> Optional[List[ProcessedEvent]]:
        """Add event to batch. Returns completed batch if ready."""
        if not self._current_batch:
            self._batch_start_time = datetime.now(timezone.utc)
        
        self._current_batch.append(event)
        
        # Check if batch is ready
        if self._is_batch_ready():
            return self._complete_batch()
        
        return None
    
    def _is_batch_ready(self) -> bool:
        """Check if current batch is ready for processing."""
        if self.strategy == BatchStrategy.IMMEDIATE:
            return len(self._current_batch) >= 1
        
        elif self.strategy == BatchStrategy.SIZE_BASED:
            return len(self._current_batch) >= self.max_batch_size
        
        elif self.strategy == BatchStrategy.TIME_BASED:
            if not self._batch_start_time:
                return False
            
            elapsed = (datetime.now(timezone.utc) - self._batch_start_time).total_seconds()
            return elapsed >= self.max_wait_time
        
        else:  # ADAPTIVE
            return self._is_adaptive_batch_ready()
    
    def _is_adaptive_batch_ready(self) -> bool:
        """Check if batch is ready using adaptive strategy."""
        batch_size = len(self._current_batch)
        
        # Size threshold
        adaptive_max_size = max(self.min_batch_size, int(self.max_batch_size * self._load_factor))
        if batch_size >= adaptive_max_size:
            return True
        
        # Time threshold
        if self._batch_start_time:
            elapsed = (datetime.now(timezone.utc) - self._batch_start_time).total_seconds()
            adaptive_wait_time = self.max_wait_time / self._load_factor
            
            if elapsed >= adaptive_wait_time and batch_size >= self.min_batch_size:
                return True
        
        return False
    
    def _complete_batch(self) -> List[ProcessedEvent]:
        """Complete current batch and return it."""
        if not self._current_batch:
            return []
        
        # Assign batch ID
        batch_id = f"batch_{self._batch_id_counter}"
        self._batch_id_counter += 1
        
        for event in self._current_batch:
            event.metadata.batch_id = batch_id
            event.advance_stage(ProcessingStage.BATCHED)
        
        # Record batch timing for adaptive strategy
        if self._batch_start_time:
            batch_time = (datetime.now(timezone.utc) - self._batch_start_time).total_seconds()
            self._recent_batch_times.append(batch_time)
            self._update_load_factor()
        
        # Return batch and reset
        completed_batch = self._current_batch.copy()
        self._current_batch.clear()
        self._batch_start_time = None
        
        return completed_batch
    
    def _update_load_factor(self) -> None:
        """Update load factor for adaptive batching."""
        if len(self._recent_batch_times) < 3:
            return
        
        avg_batch_time = sum(self._recent_batch_times) / len(self._recent_batch_times)
        
        # Adjust load factor based on processing time
        if avg_batch_time < 0.5:
            self._load_factor = min(2.0, self._load_factor * 1.1)
        elif avg_batch_time > 2.0:
            self._load_factor = max(0.5, self._load_factor * 0.9)
    
    def flush_batch(self) -> Optional[List[ProcessedEvent]]:
        """Force completion of current batch."""
        if self._current_batch:
            return self._complete_batch()
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get batching statistics."""
        return {
            'strategy': self.strategy,
            'current_batch_size': len(self._current_batch),
            'max_batch_size': self.max_batch_size,
            'load_factor': self._load_factor,
            'recent_batch_times': list(self._recent_batch_times),
            'batch_id_counter': self._batch_id_counter
        }


class EventProcessor:
    """Main event processing pipeline."""
    
    def __init__(
        self,
        dedup_strategy: DeduplicationStrategy = DeduplicationStrategy.COMPOSITE,
        batch_strategy: BatchStrategy = BatchStrategy.ADAPTIVE,
        max_queue_size: int = 1000,
        max_batch_size: int = 50
    ):
        # Components
        self.filter = EventFilter()
        self.deduplicator = EventDeduplicator(strategy=dedup_strategy)
        self.batcher = EventBatcher(strategy=batch_strategy, max_batch_size=max_batch_size)
        
        # Processing queue
        self.max_queue_size = max_queue_size
        self._input_queue: asyncio.Queue[StatusEventCreate] = asyncio.Queue(maxsize=max_queue_size)
        self._output_queue: asyncio.Queue[List[ProcessedEvent]] = asyncio.Queue(maxsize=100)
        
        # Processing state
        self._processing_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._stats = ProcessingStats()
        
        # Event handlers
        self._batch_handlers: List[Callable[[List[ProcessedEvent]], Awaitable[None]]] = []
        self._error_handlers: List[Callable[[Exception, ProcessedEvent], Awaitable[None]]] = []
    
    async def start(self) -> None:
        """Start the event processor."""
        if self._is_running:
            logger.warning("Event processor already running")
            return
        
        self._is_running = True
        self._processing_task = asyncio.create_task(self._process_events())
        logger.info("Event processor started")
    
    async def stop(self) -> None:
        """Stop the event processor."""
        if not self._is_running:
            return
        
        self._is_running = False
        
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        
        # Flush remaining batch
        remaining_batch = self.batcher.flush_batch()
        if remaining_batch:
            await self._output_queue.put(remaining_batch)
        
        logger.info("Event processor stopped")
    
    async def process_event(self, event: StatusEventCreate) -> bool:
        """Add event to processing queue."""
        try:
            await self._input_queue.put(event)
            self._stats.total_received += 1
            self._stats.record_event(ProcessingStage.RAW)
            return True
        except asyncio.QueueFull:
            logger.warning("Event processor queue full, dropping event")
            self._stats.total_dropped += 1
            return False
    
    async def get_processed_batch(self) -> Optional[List[ProcessedEvent]]:
        """Get next processed batch."""
        try:
            return await asyncio.wait_for(self._output_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None
    
    def add_batch_handler(self, handler: Callable[[List[ProcessedEvent]], Awaitable[None]]) -> None:
        """Add handler for processed batches."""
        self._batch_handlers.append(handler)
    
    def add_error_handler(self, handler: Callable[[Exception, ProcessedEvent], Awaitable[None]]) -> None:
        """Add error handler."""
        self._error_handlers.append(handler)
    
    async def _process_events(self) -> None:
        """Main event processing loop."""
        while self._is_running:
            try:
                # Get event from input queue
                try:
                    event = await asyncio.wait_for(self._input_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Check for batch timeout
                    batch = self.batcher.flush_batch()
                    if batch:
                        await self._handle_batch(batch)
                    continue
                
                # Process single event
                processed_event = await self._process_single_event(event)
                if processed_event:
                    # Add to batcher
                    batch = self.batcher.add_event(processed_event)
                    if batch:
                        await self._handle_batch(batch)
            
            except Exception as e:
                logger.error(f"Event processing error: {e}")
                self._stats.total_errors += 1
                self._stats.errors_by_type[type(e).__name__] += 1
    
    async def _process_single_event(self, event: StatusEventCreate) -> Optional[ProcessedEvent]:
        """Process a single event through the pipeline."""
        start_time = datetime.now(timezone.utc)
        
        # Create processed event wrapper
        metadata = EventMetadata(
            event_id=f"evt_{hash(event)}_{int(start_time.timestamp())}",
            received_at=start_time
        )
        processed_event = ProcessedEvent(event=event, metadata=metadata)
        
        try:
            # Stage 1: Filtering
            if not self.filter.should_process(event):
                logger.debug(f"Event filtered out: {processed_event.event_id}")
                self._stats.total_dropped += 1
                return None
            
            processed_event.advance_stage(ProcessingStage.FILTERED)
            self._stats.record_event(ProcessingStage.FILTERED)
            
            # Stage 2: Deduplication
            dedup_key = self.deduplicator.generate_dedup_key(event)
            processed_event.metadata.dedup_key = dedup_key
            
            if self.deduplicator.is_duplicate(event, dedup_key):
                logger.debug(f"Duplicate event detected: {processed_event.event_id}")
                self._stats.duplicate_events += 1
                self._stats.total_dropped += 1
                return None
            
            self.deduplicator.record_event(event, dedup_key)
            processed_event.advance_stage(ProcessingStage.DEDUPLICATED)
            self._stats.record_event(ProcessingStage.DEDUPLICATED)
            
            # Stage 3: Validation
            if not self._validate_event(event):
                logger.warning(f"Event validation failed: {processed_event.event_id}")
                self._stats.total_dropped += 1
                return None
            
            processed_event.advance_stage(ProcessingStage.VALIDATED)
            self._stats.record_event(ProcessingStage.VALIDATED)
            
            # Record processing time
            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            processed_event.metadata.processing_time_ms = processing_time
            self._stats.record_processing_time(processing_time)
            
            return processed_event
        
        except Exception as e:
            logger.error(f"Error processing event {processed_event.event_id}: {e}")
            processed_event.metadata.last_error = str(e)
            processed_event.metadata.processing_attempts += 1
            
            # Call error handlers
            for handler in self._error_handlers:
                try:
                    await handler(e, processed_event)
                except Exception as handler_error:
                    logger.error(f"Error handler failed: {handler_error}")
            
            return None
    
    def _validate_event(self, event: StatusEventCreate) -> bool:
        """Validate event data."""
        try:
            # Basic validation
            if not event.streamer_id or event.streamer_id <= 0:
                return False
            
            if not event.event_type or event.event_type not in EventType:
                return False
            
            if not event.previous_status or event.previous_status not in StreamerStatus:
                return False
            
            if not event.new_status or event.new_status not in StreamerStatus:
                return False
            
            # Timestamp validation
            if not event.event_timestamp:
                return False
            
            # Check for reasonable timestamp (not too far in past or future)
            now = datetime.now(timezone.utc)
            age = now - event.event_timestamp
            if age.total_seconds() > 3600 or age.total_seconds() < -60:  # 1 hour old or 1 minute future
                return False
            
            return True
        
        except Exception as e:
            logger.error(f"Event validation error: {e}")
            return False
    
    async def _handle_batch(self, batch: List[ProcessedEvent]) -> None:
        """Handle completed batch."""
        try:
            await self._output_queue.put(batch)
            self._stats.batches_processed += 1
            self._stats.total_processed += len(batch)
            
            # Call batch handlers
            for handler in self._batch_handlers:
                try:
                    await handler(batch)
                except Exception as e:
                    logger.error(f"Batch handler error: {e}")
        
        except Exception as e:
            logger.error(f"Error handling batch: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive processing statistics."""
        return {
            'processing': self._stats.__dict__.copy(),
            'queue_sizes': {
                'input': self._input_queue.qsize(),
                'output': self._output_queue.qsize()
            },
            'deduplicator': self.deduplicator.get_stats(),
            'batcher': self.batcher.get_stats(),
            'is_running': self._is_running
        }
    
    @property
    def is_running(self) -> bool:
        """Check if processor is running."""
        return self._is_running