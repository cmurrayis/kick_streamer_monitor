# Quickstart: Kick Streamer Status Monitor

## Prerequisites
- Debian 12+ Linux server
- PostgreSQL 13+ with existing streamer database
- Kick.com API credentials (ClientID and Client Secret)
- Python 3.11+ with pip

## Quick Setup (5 minutes)

### 1. Install the Service
```bash
# Install from package (when available)
pip install kick-monitor

# Or clone and install from source
git clone <repository-url>
cd kick-streamer-monitor
pip install -e .
```

### 2. Configure Environment
```bash
# Generate configuration template
kick-monitor config generate-template > .env

# Edit configuration file
nano .env
```

Required configuration:
```env
# Kick.com API Credentials
KICK_CLIENT_ID=your_client_id_here
KICK_CLIENT_SECRET=your_client_secret_here

# PostgreSQL Database
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=streamer_db
DATABASE_USER=kick_monitor
DATABASE_PASSWORD=secure_password

# Monitoring Settings
LOG_LEVEL=INFO
BATCH_SIZE=10
BATCH_TIMEOUT_MS=100
```

### 3. Setup Database
```bash
# Test database connection
kick-monitor db health

# Run database migrations
kick-monitor db migrate
```

### 4. Validate Configuration
```bash
# Test all connections
kick-monitor config validate

# Test specific streamers
kick-monitor streamers test "popular_streamer"
```

### 5. Start Monitoring
```bash
# Run in manual mode with real-time console display
kick-monitor start --manual

# Run in foreground (for testing)
kick-monitor start

# Or run as background daemon
kick-monitor start --daemon

# Check status
kick-monitor status
```

## Verification Steps

### Test User Story 0: Manual Mode Display
```bash
# 1. Start monitoring in manual mode
kick-monitor start --manual

# 2. Verify real-time console display shows:
#    - List of all streamers with current status
#    - Color-coded status indicators (Green=online, Red=offline, Yellow=unknown)
#    - Connection status and timestamp in header
#    - Auto-refresh every 1-2 seconds

# 3. Test keyboard shortcuts:
#    - Press 'r' to force refresh
#    - Press 's' to sort by status/username
#    - Press 'q' to quit gracefully
```

### Test User Story 1: Online Status Detection
```bash
# 1. Ensure test streamer is in database and currently offline
kick-monitor streamers list --status=offline

# 2. Wait for streamer to go online (or simulate)
# 3. Check status update occurred within seconds
kick-monitor streamers list --status=online

# 4. Verify database contains recent status event
kick-monitor logs --lines=10 | grep "status_updated"
```

### Test User Story 2: Service Recovery
```bash
# 1. Start monitoring service
kick-monitor start --daemon

# 2. Simulate network interruption
sudo iptables -A OUTPUT -d kick.com -j DROP

# 3. Wait 30 seconds, then restore connection
sudo iptables -D OUTPUT -d kick.com -j DROP

# 4. Verify service automatically reconnected
kick-monitor status | grep websocket_connected
```

### Test User Story 3: System Service Integration
```bash
# 1. Install as system service (requires sudo)
sudo kick-monitor service install --user=kick-monitor

# 2. Enable automatic startup
sudo systemctl enable kick-monitor

# 3. Restart server
sudo reboot

# 4. Verify service started automatically
systemctl status kick-monitor
kick-monitor status
```

## Expected Results

### Successful Installation
- ✅ Configuration validates without errors
- ✅ Database connection established
- ✅ Kick.com API authentication successful
- ✅ WebSocket connection to Pusher established

### Monitoring Active
- ✅ Service status shows all systems connected
- ✅ Streamer status updates appear in logs within 1 second
- ✅ Database contains recent status events
- ✅ No error messages in service logs

### Service Health
```bash
kick-monitor status
```
Expected output:
```json
{
  "service_running": true,
  "database_connected": true,
  "kick_api_authenticated": true,
  "websocket_connected": true,
  "monitored_streamers": 150,
  "events_processed_last_hour": 47,
  "last_event_timestamp": "2025-09-10T14:32:15Z",
  "uptime_seconds": 3600
}
```

## Troubleshooting

### Common Issues

**Database Connection Failed**
```bash
# Check database is running
sudo systemctl status postgresql

# Test connection manually
psql -h localhost -U kick_monitor -d streamer_db

# Verify credentials in .env file
kick-monitor config show --mask-secrets=false
```

**Kick.com Authentication Failed**
```bash
# Test API credentials
kick-monitor debug api-token

# Verify credentials at https://dev.kick.com/
# Check client_id and client_secret in .env
```

**WebSocket Connection Issues**
```bash
# Test WebSocket connection
kick-monitor debug websocket test_username

# Check firewall settings
sudo ufw status

# Verify network connectivity
curl -I https://kick.com
```

**Service Won't Start**
```bash
# Check detailed logs
kick-monitor logs --level=DEBUG

# Validate configuration
kick-monitor config validate

# Check permissions
ls -la .env
```

## Performance Verification

### Load Testing
```bash
# Monitor resource usage
htop

# Check memory usage (should be < 100MB)
kick-monitor status | grep memory_usage

# Verify event processing rate
kick-monitor logs --follow | grep "events_per_second"
```

### Database Performance
```bash
# Check database response time
kick-monitor db health

# Monitor connection pool
kick-monitor status | grep database
```

## Production Checklist

- [ ] Service runs as non-root user
- [ ] Configuration file secured (600 permissions)
- [ ] Log rotation configured
- [ ] Monitoring/alerting setup for service health
- [ ] Database backup strategy in place
- [ ] Network security rules configured
- [ ] Service auto-restart on failure enabled
- [ ] Documentation updated with production settings

## Next Steps

1. **Monitor Service Health**: Set up alerting for service downtime
2. **Scale Monitoring**: Add more streamers to database
3. **Enhance Logging**: Configure log aggregation (ELK stack, etc.)
4. **API Integration**: Connect downstream applications to database
5. **Performance Tuning**: Optimize for higher streamer counts

## Support

- **Logs**: `/var/log/kick-monitor/` or `kick-monitor logs`
- **Configuration**: `kick-monitor config show`
- **Health Check**: `kick-monitor status`
- **Debug Mode**: `kick-monitor start --log-level=DEBUG`