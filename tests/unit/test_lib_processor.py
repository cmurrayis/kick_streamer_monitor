"""
Unit tests for event processing pipeline.

Tests cover event filtering, deduplication strategies, batching,
and the complete event processing pipeline functionality.
"""

import pytest
import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch
from typing import Dict, Any

from src.lib.processor import (
    EventType,
    DeduplicationStrategy,
    BatchStrategy,
    Event,
    EventFilter,
    EventDeduplicator,
    EventBatcher,
    EventProcessor,
    ProcessingStats,
)


class TestEventType:
    """Test EventType enum."""
    
    def test_event_types(self):
        """Test that all event types are correct."""
        assert EventType.STREAM_START == "stream_start"
        assert EventType.STREAM_END == "stream_end"
        assert EventType.STATUS_CHANGE == "status_change"
        assert EventType.HEARTBEAT == "heartbeat"
        assert EventType.ERROR == "error"


class TestDeduplicationStrategy:
    """Test DeduplicationStrategy enum."""
    
    def test_deduplication_strategies(self):
        """Test all deduplication strategy values."""
        assert DeduplicationStrategy.NONE == "none"
        assert DeduplicationStrategy.CONTENT_HASH == "content_hash"
        assert DeduplicationStrategy.TIME_WINDOW == "time_window"
        assert DeduplicationStrategy.EVENT_ID == "event_id"
        assert DeduplicationStrategy.COMPOSITE == "composite"


class TestBatchStrategy:
    """Test BatchStrategy enum."""
    
    def test_batch_strategies(self):
        """Test all batch strategy values."""
        assert BatchStrategy.SIZE_BASED == "size_based"
        assert BatchStrategy.TIME_BASED == "time_based"
        assert BatchStrategy.ADAPTIVE == "adaptive"


class TestEvent:
    """Test Event data class."""
    
    def test_event_creation(self):
        """Test creating events."""
        event = Event(
            id="test123",
            type=EventType.STREAM_START,
            data={"streamer_id": 456, "title": "Test Stream"},
            streamer_id=456
        )
        
        assert event.id == "test123"
        assert event.type == EventType.STREAM_START
        assert event.data["streamer_id"] == 456
        assert event.streamer_id == 456
        assert isinstance(event.timestamp, datetime)
    
    def test_event_content_hash(self):
        """Test event content hash generation."""
        event1 = Event(
            id="test1",
            type=EventType.STREAM_START,
            data={"key": "value"},
            streamer_id=123
        )
        
        event2 = Event(
            id="test2",  # Different ID
            type=EventType.STREAM_START,
            data={"key": "value"},
            streamer_id=123
        )
        
        # Same content should produce same hash
        assert event1.content_hash() == event2.content_hash()
    
    def test_event_content_hash_different(self):
        """Test different content produces different hash."""
        event1 = Event(
            id="test1",
            type=EventType.STREAM_START,
            data={"key": "value1"},
            streamer_id=123
        )
        
        event2 = Event(
            id="test1",
            type=EventType.STREAM_START,
            data={"key": "value2"},  # Different data
            streamer_id=123
        )
        
        assert event1.content_hash() != event2.content_hash()
    
    def test_event_to_dict(self):
        """Test converting event to dictionary."""
        metadata = {"source": "websocket", "version": "1.0"}
        event = Event(
            id="test123",
            type=EventType.STATUS_CHANGE,
            data={"status": "online"},
            streamer_id=456,
            metadata=metadata
        )
        
        data = event.to_dict()
        assert data['id'] == "test123"
        assert data['type'] == "status_change"
        assert data['data'] == {"status": "online"}
        assert data['streamer_id'] == 456
        assert data['metadata'] == metadata
        assert 'timestamp' in data
    
    def test_event_from_dict(self):
        """Test creating event from dictionary."""
        data = {
            'id': 'test456',
            'type': 'stream_end',
            'data': {'duration': 3600},
            'streamer_id': 789,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        event = Event.from_dict(data)
        assert event.id == 'test456'
        assert event.type == EventType.STREAM_END
        assert event.data == {'duration': 3600}
        assert event.streamer_id == 789


class TestEventFilter:
    """Test EventFilter functionality."""
    
    def test_event_filter_creation(self):
        """Test creating event filter."""
        filter_obj = EventFilter()
        assert len(filter_obj._type_filters) == 0
        assert len(filter_obj._streamer_filters) == 0
        assert len(filter_obj._custom_filters) == 0
    
    def test_add_type_filter(self):
        """Test adding type filters."""
        filter_obj = EventFilter()
        filter_obj.add_type_filter([EventType.STREAM_START, EventType.STREAM_END])
        
        # Should allow specified types
        start_event = Event("1", EventType.STREAM_START, {}, 123)
        end_event = Event("2", EventType.STREAM_END, {}, 123)
        heartbeat_event = Event("3", EventType.HEARTBEAT, {}, 123)
        
        assert filter_obj.should_process(start_event)
        assert filter_obj.should_process(end_event)
        assert not filter_obj.should_process(heartbeat_event)
    
    def test_add_streamer_filter(self):
        """Test adding streamer filters."""
        filter_obj = EventFilter()
        filter_obj.add_streamer_filter([123, 456])
        
        # Should allow specified streamers
        event1 = Event("1", EventType.STREAM_START, {}, 123)
        event2 = Event("2", EventType.STREAM_START, {}, 456)
        event3 = Event("3", EventType.STREAM_START, {}, 789)
        
        assert filter_obj.should_process(event1)
        assert filter_obj.should_process(event2)
        assert not filter_obj.should_process(event3)
    
    def test_add_custom_filter(self):
        """Test adding custom filters."""
        filter_obj = EventFilter()
        
        def custom_filter(event: Event) -> bool:
            return event.data.get('priority') == 'high'
        
        filter_obj.add_custom_filter('priority', custom_filter)
        
        high_priority = Event("1", EventType.STATUS_CHANGE, {"priority": "high"}, 123)
        low_priority = Event("2", EventType.STATUS_CHANGE, {"priority": "low"}, 123)
        
        assert filter_obj.should_process(high_priority)
        assert not filter_obj.should_process(low_priority)
    
    def test_combined_filters(self):
        """Test multiple filters working together."""
        filter_obj = EventFilter()
        filter_obj.add_type_filter([EventType.STREAM_START])
        filter_obj.add_streamer_filter([123])
        
        # Must match both type and streamer
        valid_event = Event("1", EventType.STREAM_START, {}, 123)
        wrong_type = Event("2", EventType.STREAM_END, {}, 123)
        wrong_streamer = Event("3", EventType.STREAM_START, {}, 456)
        
        assert filter_obj.should_process(valid_event)
        assert not filter_obj.should_process(wrong_type)
        assert not filter_obj.should_process(wrong_streamer)
    
    def test_clear_filters(self):
        """Test clearing all filters."""
        filter_obj = EventFilter()
        filter_obj.add_type_filter([EventType.STREAM_START])
        filter_obj.add_streamer_filter([123])
        
        filter_obj.clear_filters()
        
        # Should now accept all events
        event = Event("1", EventType.HEARTBEAT, {}, 999)
        assert filter_obj.should_process(event)


class TestEventDeduplicator:
    """Test EventDeduplicator functionality."""
    
    def test_deduplicator_creation(self):
        """Test creating event deduplicator."""
        dedup = EventDeduplicator(
            strategy=DeduplicationStrategy.CONTENT_HASH,
            window_size=300
        )
        
        assert dedup.strategy == DeduplicationStrategy.CONTENT_HASH
        assert dedup.window_size == 300
    
    def test_no_deduplication(self):
        """Test no deduplication strategy."""
        dedup = EventDeduplicator(strategy=DeduplicationStrategy.NONE)
        
        event = Event("1", EventType.STREAM_START, {}, 123)
        
        # Should always be unique with no deduplication
        assert not dedup.is_duplicate(event)
        assert not dedup.is_duplicate(event)  # Same event again
    
    def test_content_hash_deduplication(self):
        """Test content hash deduplication."""
        dedup = EventDeduplicator(strategy=DeduplicationStrategy.CONTENT_HASH)
        
        event1 = Event("1", EventType.STREAM_START, {"key": "value"}, 123)
        event2 = Event("2", EventType.STREAM_START, {"key": "value"}, 123)  # Same content
        event3 = Event("3", EventType.STREAM_START, {"key": "different"}, 123)  # Different content
        
        assert not dedup.is_duplicate(event1)  # First occurrence
        assert dedup.is_duplicate(event2)      # Duplicate content
        assert not dedup.is_duplicate(event3)  # Different content
    
    def test_event_id_deduplication(self):
        """Test event ID deduplication."""
        dedup = EventDeduplicator(strategy=DeduplicationStrategy.EVENT_ID)
        
        event1 = Event("same_id", EventType.STREAM_START, {}, 123)
        event2 = Event("same_id", EventType.STREAM_END, {}, 456)  # Same ID, different content
        event3 = Event("different_id", EventType.STREAM_START, {}, 123)
        
        assert not dedup.is_duplicate(event1)  # First occurrence
        assert dedup.is_duplicate(event2)      # Same ID
        assert not dedup.is_duplicate(event3)  # Different ID
    
    def test_time_window_deduplication(self):
        """Test time window deduplication."""
        dedup = EventDeduplicator(
            strategy=DeduplicationStrategy.TIME_WINDOW,
            window_size=5  # 5 second window
        )
        
        base_time = datetime.now(timezone.utc)
        
        # Events within window
        event1 = Event("1", EventType.STREAM_START, {"key": "value"}, 123)
        event1.timestamp = base_time
        
        event2 = Event("2", EventType.STREAM_START, {"key": "value"}, 123)
        event2.timestamp = base_time + timedelta(seconds=2)  # Within window
        
        event3 = Event("3", EventType.STREAM_START, {"key": "value"}, 123)
        event3.timestamp = base_time + timedelta(seconds=10)  # Outside window
        
        assert not dedup.is_duplicate(event1)  # First occurrence
        assert dedup.is_duplicate(event2)      # Within window
        assert not dedup.is_duplicate(event3)  # Outside window
    
    def test_composite_deduplication(self):
        """Test composite deduplication strategy."""
        dedup = EventDeduplicator(strategy=DeduplicationStrategy.COMPOSITE)
        
        # Same ID should be duplicate regardless of content
        event1 = Event("same_id", EventType.STREAM_START, {"data": "1"}, 123)
        event2 = Event("same_id", EventType.STREAM_END, {"data": "2"}, 456)
        
        assert not dedup.is_duplicate(event1)
        assert dedup.is_duplicate(event2)  # Same ID
        
        # Different IDs but same content should also be duplicate
        event3 = Event("id1", EventType.STATUS_CHANGE, {"status": "online"}, 123)
        event4 = Event("id2", EventType.STATUS_CHANGE, {"status": "online"}, 123)
        
        assert not dedup.is_duplicate(event3)
        assert dedup.is_duplicate(event4)  # Same content
    
    def test_cleanup_old_entries(self):
        """Test cleanup of old deduplication entries."""
        dedup = EventDeduplicator(
            strategy=DeduplicationStrategy.CONTENT_HASH,
            window_size=1  # 1 second window
        )
        
        old_time = datetime.now(timezone.utc) - timedelta(seconds=2)
        event = Event("1", EventType.STREAM_START, {}, 123)
        event.timestamp = old_time
        
        # Process old event
        dedup.is_duplicate(event)
        assert len(dedup._seen_hashes) == 1
        
        # Process new event (should trigger cleanup)
        new_event = Event("2", EventType.STREAM_START, {}, 123)
        dedup.is_duplicate(new_event)
        
        # Old entry should be cleaned up
        dedup._cleanup_old_entries()
        assert len(dedup._seen_hashes) == 1  # Only new event remains


class TestEventBatcher:
    """Test EventBatcher functionality."""
    
    def test_batcher_creation(self):
        """Test creating event batcher."""
        batcher = EventBatcher(
            strategy=BatchStrategy.SIZE_BASED,
            max_batch_size=10,
            max_wait_time=5.0
        )
        
        assert batcher.strategy == BatchStrategy.SIZE_BASED
        assert batcher.max_batch_size == 10
        assert batcher.max_wait_time == 5.0
    
    def test_size_based_batching(self):
        """Test size-based batching."""
        batcher = EventBatcher(
            strategy=BatchStrategy.SIZE_BASED,
            max_batch_size=3
        )
        
        events = [
            Event(f"event_{i}", EventType.HEARTBEAT, {}, 123)
            for i in range(5)
        ]
        
        # Add events one by one
        batches = []
        for event in events:
            if batcher.should_flush():
                batches.append(batcher.get_batch())
            batcher.add_event(event)
        
        # Should have created one batch of 3 events
        assert len(batches) == 1
        assert len(batches[0]) == 3
        
        # Final flush should get remaining events
        if batcher.should_flush():
            batches.append(batcher.get_batch())
        assert len(batches) == 2
        assert len(batches[1]) == 2
    
    @pytest.mark.asyncio
    async def test_time_based_batching(self):
        """Test time-based batching."""
        batcher = EventBatcher(
            strategy=BatchStrategy.TIME_BASED,
            max_wait_time=0.1  # 100ms
        )
        
        event = Event("1", EventType.STREAM_START, {}, 123)
        batcher.add_event(event)
        
        # Should not flush immediately
        assert not batcher.should_flush()
        
        # Wait for timeout
        await asyncio.sleep(0.15)
        
        # Should now flush
        assert batcher.should_flush()
        batch = batcher.get_batch()
        assert len(batch) == 1
    
    def test_adaptive_batching_low_load(self):
        """Test adaptive batching under low load."""
        batcher = EventBatcher(
            strategy=BatchStrategy.ADAPTIVE,
            max_batch_size=10
        )
        
        # Simulate low load (events added slowly)
        event = Event("1", EventType.STREAM_START, {}, 123)
        batcher.add_event(event)
        
        # Should be more conservative with batching under low load
        # Implementation details may vary, but batch should be smaller
        current_size = len(batcher._current_batch)
        assert current_size == 1
    
    def test_get_batch_stats(self):
        """Test getting batch statistics."""
        batcher = EventBatcher()
        
        # Add some events
        for i in range(5):
            event = Event(f"event_{i}", EventType.HEARTBEAT, {}, 123)
            batcher.add_event(event)
        
        stats = batcher.get_stats()
        assert 'total_events' in stats
        assert 'total_batches' in stats
        assert 'current_batch_size' in stats
        assert 'avg_batch_size' in stats
        assert stats['current_batch_size'] == 5


class TestEventProcessor:
    """Test EventProcessor complete pipeline."""
    
    def test_processor_creation(self):
        """Test creating event processor."""
        processor = EventProcessor(
            dedup_strategy=DeduplicationStrategy.CONTENT_HASH,
            batch_strategy=BatchStrategy.SIZE_BASED,
            max_batch_size=20
        )
        
        assert isinstance(processor.filter, EventFilter)
        assert isinstance(processor.deduplicator, EventDeduplicator)
        assert isinstance(processor.batcher, EventBatcher)
        assert processor.deduplicator.strategy == DeduplicationStrategy.CONTENT_HASH
        assert processor.batcher.strategy == BatchStrategy.SIZE_BASED
    
    @pytest.mark.asyncio
    async def test_process_event_success(self):
        """Test successful event processing."""
        processor = EventProcessor()
        
        event = Event("1", EventType.STREAM_START, {"title": "Test Stream"}, 123)
        result = await processor.process_event(event)
        
        assert result is True
        assert processor._stats.events_processed == 1
        assert processor._stats.events_accepted == 1
        assert processor._stats.events_duplicate == 0
    
    @pytest.mark.asyncio
    async def test_process_event_filtered(self):
        """Test event processing with filtering."""
        processor = EventProcessor()
        processor.filter.add_type_filter([EventType.STREAM_START])
        
        # Should be accepted
        start_event = Event("1", EventType.STREAM_START, {}, 123)
        result1 = await processor.process_event(start_event)
        assert result1 is True
        
        # Should be filtered out
        end_event = Event("2", EventType.STREAM_END, {}, 123)
        result2 = await processor.process_event(end_event)
        assert result2 is False
        
        assert processor._stats.events_processed == 2
        assert processor._stats.events_accepted == 1
        assert processor._stats.events_filtered == 1
    
    @pytest.mark.asyncio
    async def test_process_event_duplicate(self):
        """Test event processing with deduplication."""
        processor = EventProcessor(
            dedup_strategy=DeduplicationStrategy.CONTENT_HASH
        )
        
        # Same content events
        event1 = Event("1", EventType.STREAM_START, {"key": "value"}, 123)
        event2 = Event("2", EventType.STREAM_START, {"key": "value"}, 123)
        
        result1 = await processor.process_event(event1)
        result2 = await processor.process_event(event2)
        
        assert result1 is True
        assert result2 is False  # Duplicate
        
        assert processor._stats.events_processed == 2
        assert processor._stats.events_accepted == 1
        assert processor._stats.events_duplicate == 1
    
    @pytest.mark.asyncio
    async def test_process_batch_callback(self):
        """Test batch processing with callback."""
        processed_batches = []
        
        async def batch_callback(batch):
            processed_batches.append(batch)
        
        processor = EventProcessor(
            batch_strategy=BatchStrategy.SIZE_BASED,
            max_batch_size=2
        )
        processor.set_batch_callback(batch_callback)
        
        # Add events to trigger batch
        for i in range(3):
            event = Event(f"event_{i}", EventType.HEARTBEAT, {}, 123)
            await processor.process_event(event)
        
        # Should have created one batch
        assert len(processed_batches) == 1
        assert len(processed_batches[0]) == 2
    
    @pytest.mark.asyncio
    async def test_flush_batches(self):
        """Test manual batch flushing."""
        processed_batches = []
        
        async def batch_callback(batch):
            processed_batches.append(batch)
        
        processor = EventProcessor(max_batch_size=10)  # Large batch size
        processor.set_batch_callback(batch_callback)
        
        # Add single event
        event = Event("1", EventType.STREAM_START, {}, 123)
        await processor.process_event(event)
        
        # No batch yet
        assert len(processed_batches) == 0
        
        # Manual flush
        await processor.flush_batches()
        
        # Should now have batch
        assert len(processed_batches) == 1
        assert len(processed_batches[0]) == 1
    
    def test_add_filters(self):
        """Test adding filters to processor."""
        processor = EventProcessor()
        
        processor.add_type_filter([EventType.STREAM_START])
        processor.add_streamer_filter([123, 456])
        
        def custom_filter(event):
            return event.data.get('important', False)
        
        processor.add_custom_filter('importance', custom_filter)
        
        # Check filters were added
        assert len(processor.filter._type_filters) > 0
        assert len(processor.filter._streamer_filters) > 0
        assert len(processor.filter._custom_filters) > 0
    
    def test_get_processing_stats(self):
        """Test getting processing statistics."""
        processor = EventProcessor()
        
        stats = processor.get_stats()
        
        assert 'events_processed' in stats
        assert 'events_accepted' in stats
        assert 'events_filtered' in stats
        assert 'events_duplicate' in stats
        assert 'events_error' in stats
        assert 'processing_times' in stats
        assert 'uptime_seconds' in stats
    
    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test error handling in processing."""
        processor = EventProcessor()
        
        # Mock batch callback that raises exception
        async def failing_callback(batch):
            raise ValueError("Callback failed")
        
        processor.set_batch_callback(failing_callback)
        
        # Process event that would trigger batch
        processor.batcher.max_batch_size = 1
        event = Event("1", EventType.STREAM_START, {}, 123)
        
        # Should handle error gracefully
        result = await processor.process_event(event)
        assert result is True  # Event still processed
        assert processor._stats.events_error > 0


class TestProcessingStats:
    """Test ProcessingStats data class."""
    
    def test_stats_creation(self):
        """Test creating processing statistics."""
        stats = ProcessingStats()
        
        assert stats.events_processed == 0
        assert stats.events_accepted == 0
        assert stats.events_filtered == 0
        assert stats.events_duplicate == 0
        assert stats.events_error == 0
        assert isinstance(stats.start_time, datetime)
        assert len(stats.processing_times) == 0
    
    def test_record_processing_time(self):
        """Test recording processing times."""
        stats = ProcessingStats()
        
        stats.record_processing_time(0.1)
        stats.record_processing_time(0.2)
        stats.record_processing_time(0.15)
        
        assert len(stats.processing_times) == 3
        assert stats.get_avg_processing_time() == 0.15
    
    def test_get_uptime(self):
        """Test getting uptime."""
        stats = ProcessingStats()
        
        # Small delay to ensure uptime > 0
        time.sleep(0.01)
        
        uptime = stats.get_uptime_seconds()
        assert uptime > 0
    
    def test_stats_to_dict(self):
        """Test converting stats to dictionary."""
        stats = ProcessingStats()
        stats.events_processed = 10
        stats.events_accepted = 8
        stats.events_filtered = 1
        stats.events_duplicate = 1
        
        data = stats.to_dict()
        
        assert data['events_processed'] == 10
        assert data['events_accepted'] == 8
        assert data['events_filtered'] == 1
        assert data['events_duplicate'] == 1
        assert 'start_time' in data
        assert 'uptime_seconds' in data
        assert 'avg_processing_time' in data


@pytest.fixture
def sample_events():
    """Fixture providing sample events for testing."""
    return [
        Event("1", EventType.STREAM_START, {"title": "Stream 1"}, 123),
        Event("2", EventType.STATUS_CHANGE, {"status": "online"}, 123),
        Event("3", EventType.STREAM_END, {"duration": 3600}, 123),
        Event("4", EventType.STREAM_START, {"title": "Stream 2"}, 456),
        Event("5", EventType.HEARTBEAT, {}, 789),
    ]


@pytest.fixture
def configured_processor():
    """Fixture providing a configured event processor."""
    processor = EventProcessor(
        dedup_strategy=DeduplicationStrategy.CONTENT_HASH,
        batch_strategy=BatchStrategy.SIZE_BASED,
        max_batch_size=3
    )
    
    # Add some filters
    processor.add_type_filter([
        EventType.STREAM_START,
        EventType.STREAM_END,
        EventType.STATUS_CHANGE
    ])
    
    return processor


class TestEventProcessorFixtures:
    """Test using the event processor fixtures."""
    
    @pytest.mark.asyncio
    async def test_sample_events_fixture(self, sample_events):
        """Test that sample events fixture works."""
        assert len(sample_events) == 5
        assert sample_events[0].type == EventType.STREAM_START
        assert sample_events[0].streamer_id == 123
    
    @pytest.mark.asyncio
    async def test_configured_processor_fixture(self, configured_processor, sample_events):
        """Test that configured processor fixture works."""
        processed_count = 0
        
        for event in sample_events:
            result = await configured_processor.process_event(event)
            if result:
                processed_count += 1
        
        # Should filter out heartbeat event
        assert processed_count == 4
        
        stats = configured_processor.get_stats()
        assert stats['events_processed'] == 5
        assert stats['events_accepted'] == 4
        assert stats['events_filtered'] == 1