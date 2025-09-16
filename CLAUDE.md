# Claude Code Context: Kick Streamer Monitor

## Project Overview
Complete real-time monitoring platform for Kick.com streamers with web dashboard, multi-user management, and admin controls. Tracks streamer online/offline status, viewer counts, and profile data using Kick.com API with browser fallback for Cloudflare protection. Features both simple polling mode and web-based interfaces for 24/7 operation on production servers.

## Technology Stack
- **Language**: Python 3.11+ (asyncio for concurrent operations)
- **Database**: PostgreSQL with psycopg3 async driver
- **API**: Kick.com OAuth 2.1 with Playwright browser fallback for Cloudflare bypass
- **Web Framework**: aiohttp for REST API and WebSocket real-time updates
- **Frontend**: Server-rendered HTML with vanilla JavaScript and retro terminal styling
- **Authentication**: Session-based with role-based access control (Admin/User/Viewer)
- **Deployment**: Systemd service with full dependency management
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
- `aiohttp` - Web server and HTTP client for API calls
- `playwright` - Browser automation for Cloudflare bypass
- `psycopg3` - PostgreSQL async database driver
- `pydantic` - Configuration validation and data models
- `python-dotenv` - Environment variable management
- `rich` - Terminal UI for manual mode with real-time display
- `pytest-asyncio` - Async testing framework
- `hashlib` - Password hashing for user authentication

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
- **Streamer**: username, kick_user_id, status, viewer counts, profile pictures
- **StatusEvent**: event_type, timestamps, streamer reference, viewer data
- **User**: multi-tenant accounts with role-based permissions and streamer assignments
- **UserSession**: authentication sessions with login tracking
- **Configuration**: key-value pairs with encryption support

## API Contracts
- **REST**: OAuth token management, channel information, user management
- **Web Dashboard**: Real-time WebSocket updates, AJAX API endpoints
- **CLI**: status, config, streamers, db commands with standardized exit codes
- **Authentication**: Session-based login with role-based access control

## Performance Requirements
- <1 second status update latency
- Handle 1000+ concurrent streamers
- Minimal memory footprint (<100MB)
- Auto-recovery from connection failures

## Major Features Implemented

### **Web Dashboard & Multi-User System**
- **Web Interface**: Complete aiohttp-based web server with real-time WebSocket updates
- **Multi-User Authentication**: Session-based login with Admin/User/Viewer roles
- **User Management**: Admin portal for creating users, assigning streamers, password resets
- **Account Settings**: User profile management with password change functionality
- **Retro Terminal Theme**: Consistent orange/green terminal aesthetic across all interfaces

### **Enhanced Monitoring Capabilities**
- **Simple Polling Mode**: Cloudflare-resistant monitoring using Playwright browser fallback
- **Viewer Analytics**: Real-time viewer count tracking with historical data storage
- **Profile Pictures**: Automatic collection and display of streamer profile images
- **Status Tracking**: Comprehensive online/offline detection with timestamp tracking
- **Real-time Updates**: WebSocket-powered live dashboard updates without page refresh

### **Production Deployment**
- **Systemd Integration**: Full systemd service with proper user isolation and dependencies
- **Playwright Setup**: Automated browser installation for service users
- **Database Schema**: Extended with user management, viewer tracking, and profile data
- **Error Handling**: Comprehensive error messages and graceful failure recovery

### **API & Integration**
- **OAuth 2.1 Flow**: Robust Kick.com API integration with token management
- **Browser Fallback**: Playwright-based scraping when API access is blocked
- **RESTful Endpoints**: JSON API for dashboard data and user management
- **CLI Interface**: Extended command-line tools for service management and debugging