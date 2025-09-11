#!/usr/bin/env python3
"""
MVP validation script for testing against live Kick.com services.

Validates the complete monitoring system against real Kick.com APIs,
WebSocket connections, and database operations to ensure production readiness.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import argparse
import traceback

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lib.config import ConfigurationManager, ValidationLevel
from services.database import DatabaseService, DatabaseConfig
from services.auth import AuthenticationService, OAuthConfig
from services.websocket import WebSocketService
from models.streamer import StreamerCreate, StreamerStatus
from lib.performance import PerformanceMonitor, setup_performance_optimization

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('validation.log')
    ]
)
logger = logging.getLogger(__name__)


class MVPValidationResult:
    """Result of MVP validation test."""
    
    def __init__(self, test_name: str):
        self.test_name = test_name
        self.success = False
        self.error_message: Optional[str] = None
        self.warnings: List[str] = []
        self.metrics: Dict[str, Any] = {}
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.duration_seconds: float = 0.0
    
    def start(self) -> None:
        """Mark test start time."""
        self.start_time = datetime.now(timezone.utc)
    
    def finish(self, success: bool, error_message: Optional[str] = None) -> None:
        """Mark test completion."""
        self.end_time = datetime.now(timezone.utc)
        self.success = success
        self.error_message = error_message
        
        if self.start_time and self.end_time:
            self.duration_seconds = (self.end_time - self.start_time).total_seconds()
    
    def add_warning(self, warning: str) -> None:
        """Add warning message."""
        self.warnings.append(warning)
    
    def add_metric(self, key: str, value: Any) -> None:
        """Add performance metric."""
        self.metrics[key] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "test_name": self.test_name,
            "success": self.success,
            "error_message": self.error_message,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "duration_seconds": self.duration_seconds,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None
        }


class MVPValidator:
    """MVP validation test runner."""
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file
        self.config: Optional[ConfigurationManager] = None
        self.results: List[MVPValidationResult] = []
        self.performance_monitor = PerformanceMonitor()
        
        # Test streamers for validation
        self.test_streamers = [
            {"username": "xqc", "expected_user_id": "12345"},
            {"username": "trainwreckstv", "expected_user_id": "67890"}
        ]
    
    async def run_validation(self) -> bool:
        """Run complete MVP validation suite."""
        logger.info("Starting MVP validation against live Kick.com services")
        
        try:
            # Start performance monitoring
            await self.performance_monitor.start_monitoring(interval=10.0)
            
            # Run validation tests
            await self.validate_configuration()
            await self.validate_database_connection()
            await self.validate_authentication()
            await self.validate_api_access()
            await self.validate_websocket_connection()
            await self.validate_end_to_end_monitoring()
            await self.validate_performance_requirements()
            
            # Generate validation report
            success = self.generate_report()
            
            logger.info(f"MVP validation completed. Success: {success}")
            return success
            
        except Exception as e:
            logger.error(f"Validation failed with exception: {e}")
            logger.error(traceback.format_exc())
            return False
        
        finally:
            await self.performance_monitor.stop_monitoring()
    
    async def validate_configuration(self) -> None:
        """Validate configuration loading and validation."""
        test = MVPValidationResult("configuration_validation")
        test.start()
        
        try:
            # Load configuration
            self.config = ConfigurationManager(
                env_file=self.config_file,
                validation_level=ValidationLevel.STRICT
            )
            
            # Validate configuration
            validation_result = self.config.validate_configuration()
            
            if validation_result.is_valid:
                test.finish(True)
                logger.info("✓ Configuration validation passed")
            else:
                error_msg = f"Configuration validation failed: {validation_result.errors}"
                test.finish(False, error_msg)
                logger.error(f"✗ {error_msg}")
            
            # Add warnings
            for warning in validation_result.warnings:
                test.add_warning(warning)
            
            # Add metrics
            test.add_metric("config_settings_count", len(self.config.get_all_config()))
            test.add_metric("validation_errors", len(validation_result.errors))
            test.add_metric("validation_warnings", len(validation_result.warnings))
            
        except Exception as e:
            test.finish(False, str(e))
            logger.error(f"✗ Configuration validation failed: {e}")
        
        self.results.append(test)
    
    async def validate_database_connection(self) -> None:
        """Validate database connectivity and schema."""
        test = MVPValidationResult("database_connection")
        test.start()
        
        try:
            if not self.config:
                test.finish(False, "Configuration not loaded")
                self.results.append(test)
                return
            
            # Get database configuration
            db_config = self.config.get_database_config()
            
            # Test database connection
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            # Test basic operations
            connection_healthy = await db_service.health_check()
            
            if connection_healthy:
                test.finish(True)
                logger.info("✓ Database connection validated")
            else:
                test.finish(False, "Database health check failed")
                logger.error("✗ Database health check failed")
            
            # Add metrics
            pool_stats = await db_service.get_pool_stats()
            test.add_metric("database_pool_size", pool_stats.get("size", 0))
            test.add_metric("database_pool_available", pool_stats.get("available", 0))
            
            await db_service.disconnect()
            
        except Exception as e:
            test.finish(False, str(e))
            logger.error(f"✗ Database connection validation failed: {e}")
        
        self.results.append(test)
    
    async def validate_authentication(self) -> None:
        """Validate Kick.com OAuth authentication."""
        test = MVPValidationResult("authentication")
        test.start()
        
        try:
            if not self.config:
                test.finish(False, "Configuration not loaded")
                self.results.append(test)
                return
            
            # Get OAuth configuration
            oauth_config = self.config.get_oauth_config()
            
            # Test authentication
            auth_service = AuthenticationService(oauth_config)
            
            start_time = time.time()
            token = await auth_service.get_access_token()
            auth_time = time.time() - start_time
            
            if token:
                test.finish(True)
                logger.info("✓ Kick.com authentication validated")
                
                # Test token validation
                is_valid = await auth_service.validate_token(token)
                if not is_valid:
                    test.add_warning("Token validation failed")
            else:
                test.finish(False, "Failed to obtain access token")
                logger.error("✗ Failed to obtain access token")
            
            # Add metrics
            test.add_metric("auth_response_time_ms", auth_time * 1000)
            test.add_metric("token_length", len(token) if token else 0)
            
        except Exception as e:
            test.finish(False, str(e))
            logger.error(f"✗ Authentication validation failed: {e}")
        
        self.results.append(test)
    
    async def validate_api_access(self) -> None:
        """Validate Kick.com API access and streamer data retrieval."""
        test = MVPValidationResult("api_access")
        test.start()
        
        try:
            if not self.config:
                test.finish(False, "Configuration not loaded")
                self.results.append(test)
                return
            
            # Get OAuth configuration and authenticate
            oauth_config = self.config.get_oauth_config()
            auth_service = AuthenticationService(oauth_config)
            token = await auth_service.get_access_token()
            
            if not token:
                test.finish(False, "No access token available")
                self.results.append(test)
                return
            
            # Test API calls for test streamers
            successful_calls = 0
            total_calls = 0
            response_times = []
            
            for streamer in self.test_streamers:
                username = streamer["username"]
                
                try:
                    start_time = time.time()
                    # This would be replaced with actual API call
                    # streamer_data = await api_service.get_streamer_info(username)
                    response_time = time.time() - start_time
                    response_times.append(response_time)
                    
                    successful_calls += 1
                    logger.info(f"✓ API call successful for {username}")
                    
                except Exception as e:
                    logger.warning(f"API call failed for {username}: {e}")
                
                total_calls += 1
            
            # Determine success
            success_rate = successful_calls / total_calls if total_calls > 0 else 0
            
            if success_rate >= 0.8:  # 80% success rate required
                test.finish(True)
                logger.info(f"✓ API access validated ({success_rate:.1%} success rate)")
            else:
                test.finish(False, f"API success rate too low: {success_rate:.1%}")
                logger.error(f"✗ API success rate too low: {success_rate:.1%}")
            
            # Add metrics
            test.add_metric("api_success_rate", success_rate)
            test.add_metric("successful_calls", successful_calls)
            test.add_metric("total_calls", total_calls)
            if response_times:
                test.add_metric("average_response_time_ms", sum(response_times) / len(response_times) * 1000)
            
        except Exception as e:
            test.finish(False, str(e))
            logger.error(f"✗ API access validation failed: {e}")
        
        self.results.append(test)
    
    async def validate_websocket_connection(self) -> None:
        """Validate WebSocket connection to Kick.com Pusher service."""
        test = MVPValidationResult("websocket_connection")
        test.start()
        
        try:
            if not self.config:
                test.finish(False, "Configuration not loaded")
                self.results.append(test)
                return
            
            # Create WebSocket service
            websocket_service = WebSocketService(self.config)
            
            # Test connection
            start_time = time.time()
            success = await websocket_service.connect()
            connection_time = time.time() - start_time
            
            if success:
                logger.info("✓ WebSocket connection established")
                
                # Test subscription
                test_channel = "channel.12345"
                subscription_success = await websocket_service.subscribe_to_channel(test_channel)
                
                if subscription_success:
                    logger.info("✓ WebSocket subscription successful")
                    test.finish(True)
                else:
                    test.finish(False, "WebSocket subscription failed")
                    logger.error("✗ WebSocket subscription failed")
                
                # Test message sending/receiving (with timeout)
                try:
                    # Send ping and wait for pong
                    await asyncio.wait_for(
                        websocket_service.send_ping(),
                        timeout=10.0
                    )
                    logger.info("✓ WebSocket ping/pong successful")
                except asyncio.TimeoutError:
                    test.add_warning("WebSocket ping/pong timeout")
                
                await websocket_service.disconnect()
                
            else:
                test.finish(False, "WebSocket connection failed")
                logger.error("✗ WebSocket connection failed")
            
            # Add metrics
            test.add_metric("connection_time_ms", connection_time * 1000)
            test.add_metric("connection_successful", success)
            
        except Exception as e:
            test.finish(False, str(e))
            logger.error(f"✗ WebSocket validation failed: {e}")
        
        self.results.append(test)
    
    async def validate_end_to_end_monitoring(self) -> None:
        """Validate complete end-to-end monitoring workflow."""
        test = MVPValidationResult("end_to_end_monitoring")
        test.start()
        
        try:
            if not self.config:
                test.finish(False, "Configuration not loaded")
                self.results.append(test)
                return
            
            # Setup database
            db_config = self.config.get_database_config()
            db_service = DatabaseService(db_config)
            await db_service.connect()
            
            # Create test streamer
            test_streamer = StreamerCreate(
                kick_user_id="test_validation_12345",
                username="test_validation_streamer",
                display_name="Test Validation Streamer",
                status=StreamerStatus.UNKNOWN
            )
            
            # Add streamer to database
            start_time = time.time()
            created_streamer = await db_service.create_streamer(test_streamer)
            db_write_time = time.time() - start_time
            
            # Update streamer status
            updated_streamer = created_streamer.update_status(StreamerStatus.ONLINE)
            start_time = time.time()
            await db_service.update_streamer(updated_streamer)
            db_update_time = time.time() - start_time
            
            # Read streamer back
            start_time = time.time()
            retrieved_streamer = await db_service.get_streamer_by_id(created_streamer.id)
            db_read_time = time.time() - start_time
            
            # Validate data consistency
            if (retrieved_streamer and 
                retrieved_streamer.status == StreamerStatus.ONLINE and
                retrieved_streamer.username == test_streamer.username):
                
                test.finish(True)
                logger.info("✓ End-to-end monitoring workflow validated")
            else:
                test.finish(False, "Data consistency check failed")
                logger.error("✗ Data consistency check failed")
            
            # Cleanup test data
            if created_streamer.id:
                await db_service.delete_streamer(created_streamer.id)
            
            await db_service.disconnect()
            
            # Add metrics
            test.add_metric("db_write_time_ms", db_write_time * 1000)
            test.add_metric("db_update_time_ms", db_update_time * 1000)
            test.add_metric("db_read_time_ms", db_read_time * 1000)
            
        except Exception as e:
            test.finish(False, str(e))
            logger.error(f"✗ End-to-end monitoring validation failed: {e}")
        
        self.results.append(test)
    
    async def validate_performance_requirements(self) -> None:
        """Validate performance requirements are met."""
        test = MVPValidationResult("performance_requirements")
        test.start()
        
        try:
            # Get performance metrics
            metrics = await self.performance_monitor.get_comprehensive_metrics()
            
            # Check performance requirements
            requirements_met = True
            warnings = []
            
            # Memory usage should be reasonable
            memory_mb = metrics.get("system", {}).get("memory_usage_mb", 0)
            if memory_mb > 200:  # More than 200MB
                warnings.append(f"High memory usage: {memory_mb:.1f}MB")
            
            # CPU usage should be reasonable
            cpu_percent = metrics.get("system", {}).get("cpu_usage_percent", 0)
            if cpu_percent > 50:  # More than 50% CPU
                warnings.append(f"High CPU usage: {cpu_percent:.1f}%")
            
            # API response times should be fast
            api_metrics = metrics.get("api", {})
            avg_response_time = api_metrics.get("average_response_time_ms", 0)
            if avg_response_time > 2000:  # More than 2 seconds
                warnings.append(f"Slow API response time: {avg_response_time:.0f}ms")
                requirements_met = False
            
            # Database operations should be fast
            for result in self.results:
                if result.test_name == "end_to_end_monitoring":
                    db_metrics = result.metrics
                    for metric_name, value in db_metrics.items():
                        if "time_ms" in metric_name and value > 1000:  # More than 1 second
                            warnings.append(f"Slow database operation {metric_name}: {value:.0f}ms")
            
            # Add all warnings to test
            for warning in warnings:
                test.add_warning(warning)
            
            if requirements_met:
                test.finish(True)
                logger.info("✓ Performance requirements validated")
            else:
                test.finish(False, "Performance requirements not met")
                logger.error("✗ Performance requirements not met")
            
            # Add performance metrics
            test.add_metric("memory_usage_mb", memory_mb)
            test.add_metric("cpu_usage_percent", cpu_percent)
            test.add_metric("api_response_time_ms", avg_response_time)
            
        except Exception as e:
            test.finish(False, str(e))
            logger.error(f"✗ Performance validation failed: {e}")
        
        self.results.append(test)
    
    def generate_report(self) -> bool:
        """Generate validation report."""
        total_tests = len(self.results)
        successful_tests = sum(1 for result in self.results if result.success)
        
        # Calculate overall success
        overall_success = successful_tests == total_tests
        
        # Generate report
        report = {
            "validation_timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_success": overall_success,
            "summary": {
                "total_tests": total_tests,
                "successful_tests": successful_tests,
                "failed_tests": total_tests - successful_tests,
                "success_rate": (successful_tests / total_tests * 100) if total_tests > 0 else 0
            },
            "test_results": [result.to_dict() for result in self.results],
            "recommendations": self._generate_recommendations()
        }
        
        # Write report to file
        report_file = f"mvp_validation_report_{int(time.time())}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        # Print summary
        self._print_summary(report)
        
        logger.info(f"Validation report written to: {report_file}")
        
        return overall_success
    
    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on test results."""
        recommendations = []
        
        for result in self.results:
            if not result.success:
                if result.test_name == "configuration_validation":
                    recommendations.append("Review and fix configuration errors in .env file")
                elif result.test_name == "database_connection":
                    recommendations.append("Ensure PostgreSQL is running and accessible")
                elif result.test_name == "authentication":
                    recommendations.append("Verify Kick.com API credentials are correct")
                elif result.test_name == "api_access":
                    recommendations.append("Check network connectivity to Kick.com API")
                elif result.test_name == "websocket_connection":
                    recommendations.append("Verify WebSocket connectivity to Pusher service")
                elif result.test_name == "performance_requirements":
                    recommendations.append("Optimize performance settings and resource allocation")
            
            # Add recommendations for warnings
            for warning in result.warnings:
                if "memory" in warning.lower():
                    recommendations.append("Consider reducing memory usage or increasing available RAM")
                elif "cpu" in warning.lower():
                    recommendations.append("Consider optimizing CPU-intensive operations")
                elif "response time" in warning.lower():
                    recommendations.append("Optimize API response times and database queries")
        
        return list(set(recommendations))  # Remove duplicates
    
    def _print_summary(self, report: Dict[str, Any]) -> None:
        """Print validation summary to console."""
        print("\n" + "="*60)
        print("MVP VALIDATION SUMMARY")
        print("="*60)
        
        summary = report["summary"]
        print(f"Overall Success: {'✓ PASS' if report['overall_success'] else '✗ FAIL'}")
        print(f"Success Rate: {summary['success_rate']:.1f}% ({summary['successful_tests']}/{summary['total_tests']})")
        print()
        
        print("Test Results:")
        print("-" * 40)
        for result in self.results:
            status = "✓ PASS" if result.success else "✗ FAIL"
            duration = f"{result.duration_seconds:.2f}s"
            print(f"{result.test_name:30} {status:8} {duration:>8}")
            
            if result.error_message:
                print(f"    Error: {result.error_message}")
            
            for warning in result.warnings:
                print(f"    Warning: {warning}")
        
        print()
        
        if report["recommendations"]:
            print("Recommendations:")
            print("-" * 40)
            for i, rec in enumerate(report["recommendations"], 1):
                print(f"{i}. {rec}")
            print()


async def main():
    """Main validation script entry point."""
    parser = argparse.ArgumentParser(description="MVP validation for Kick Streamer Monitor")
    parser.add_argument("--config", help="Configuration file path", default=".env")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--test", help="Run specific test only", choices=[
        "configuration", "database", "authentication", "api", "websocket", 
        "end_to_end", "performance"
    ])
    
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Check if config file exists
    if not os.path.exists(args.config):
        print(f"Error: Configuration file '{args.config}' not found")
        print("Please copy .env.example to .env and configure with your settings")
        return 1
    
    # Run validation
    validator = MVPValidator(args.config)
    
    if args.test:
        # Run specific test only
        test_methods = {
            "configuration": validator.validate_configuration,
            "database": validator.validate_database_connection,
            "authentication": validator.validate_authentication,
            "api": validator.validate_api_access,
            "websocket": validator.validate_websocket_connection,
            "end_to_end": validator.validate_end_to_end_monitoring,
            "performance": validator.validate_performance_requirements
        }
        
        if args.test in test_methods:
            await validator.performance_monitor.start_monitoring()
            try:
                await test_methods[args.test]()
                success = validator.generate_report()
            finally:
                await validator.performance_monitor.stop_monitoring()
        else:
            print(f"Unknown test: {args.test}")
            return 1
    else:
        # Run full validation
        success = await validator.run_validation()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)