# Claude Code Context: Kick Streamer Monitor

## Project Overview
Real-time monitoring service that tracks Kick.com streamer online/offline status using WebSocket connections and updates PostgreSQL database. Designed as a Python asyncio service for 24/7 operation on Debian 12+ servers.

## Technology Stack
- **Language**: Python 3.11+ (asyncio, websockets for real-time events)
- **Database**: PostgreSQL with psycopg3 async driver
- **API**: Kick.com OAuth 2.1 + unofficial WebSocket (Pusher protocol)
- **Deployment**: Systemd service with CLI management interface
- **Testing**: pytest with real external dependencies (no mocks)

## Architecture Principles
- Single project structure: monitor-lib with CLI interface
- Direct framework usage (no wrapper classes/abstractions)
- Library-first design with CLI exposure
- TDD with contract→integration→unit test order
- Structured JSON logging for observability

## Project Structure
```
src/
├── models/          # Data entities (Streamer, StatusEvent, Configuration)
├── services/        # Core services (WebSocket, Database, Auth)
├── cli/            # Command-line interface
└── lib/            # Core monitoring library

tests/
├── contract/       # API contract tests (Kick.com endpoints)
├── integration/    # Database + WebSocket integration tests  
└── unit/           # Unit tests for business logic
```

## Key Dependencies
- `asyncio` - Core async/await event handling
- `websockets`/`pusher-py` - WebSocket connections to Kick.com
- `psycopg3` - PostgreSQL async database driver
- `pydantic` - Configuration validation and data models
- `python-dotenv` - Environment variable management
- `pytest-asyncio` - Async testing framework

## Development Workflow
1. **Tests First**: Write failing contract/integration tests
2. **Implementation**: Make tests pass with minimal code
3. **CLI Interface**: Expose functionality via command-line
4. **Documentation**: Update llms.txt with new patterns

## Configuration
Environment-based (.env file) with categories:
- `KICK_CLIENT_ID`/`KICK_CLIENT_SECRET` - API authentication
- `DATABASE_*` - PostgreSQL connection settings
- `LOG_LEVEL`, `BATCH_SIZE` - Monitoring parameters

## Core Entities
- **Streamer**: username, kick_user_id, status (online/offline/unknown)
- **StatusEvent**: event_type, timestamps, streamer reference
- **Configuration**: key-value pairs with encryption support

## API Contracts
- **REST**: OAuth token management, channel information
- **WebSocket**: Real-time StreamerIsLive/StreamerOffline events
- **CLI**: status, config, streamers, db commands with standardized exit codes

## Performance Requirements
- <1 second status update latency
- Handle 1000+ concurrent streamers
- Minimal memory footprint (<100MB)
- Auto-recovery from connection failures

## Recent Changes
- Initial project setup with spec-driven development
- Established OAuth 2.1 authentication flow
- Designed WebSocket event handling architecture
- Created database schema with proper indexing