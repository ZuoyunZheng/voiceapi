#!/usr/bin/env python3
"""
Test script to verify local VoiceAPI setup without Docker.
This script tests the database connection and basic API functionality.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import requests

# Add backend to path
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from config import config
from db import DatabaseManager


def print_status(message, status="INFO"):
    colors = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "ERROR": "\033[91m",
        "WARNING": "\033[93m",
    }
    print(f"{colors.get(status, '')}{status}: {message}\033[0m")


async def test_database():
    """Test database connection and operations."""
    print_status("Testing database connection...", "INFO")

    try:
        db = DatabaseManager(config.database.get_url())
        await db.initialize()
        print_status("Database connection successful", "SUCCESS")

        # Test creating a session
        session_id = await db.create_session("Local Test Session")
        print_status(f"Created test session with ID: {session_id}", "SUCCESS")

        # Test creating a speaker
        speaker_id = await db.create_speaker("Test Speaker")
        print_status(f"Created test speaker with ID: {speaker_id}", "SUCCESS")

        # Test getting sessions
        sessions = await db.get_all_sessions()
        print_status(f"Retrieved {len(sessions)} sessions", "SUCCESS")

        # Clean up
        await db.close()
        print_status("Database tests completed successfully", "SUCCESS")
        return True

    except Exception as e:
        print_status(f"Database test failed: {e}", "ERROR")
        return False


def test_api_endpoints():
    """Test API endpoints."""
    print_status("Testing API endpoints...", "INFO")

    base_url = f"http://localhost:{config.port}"

    try:
        # Test health endpoint
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            health_data = response.json()
            print_status(
                f"Health check: {health_data['status']}, DB: {health_data['database']}",
                "SUCCESS",
            )
        else:
            print_status(f"Health check failed: {response.status_code}", "ERROR")
            return False

        # Test sessions endpoint
        response = requests.get(f"{base_url}/sessions", timeout=5)
        if response.status_code == 200:
            sessions = response.json()
            print_status(
                f"Sessions endpoint: retrieved {len(sessions)} sessions", "SUCCESS"
            )
        else:
            print_status(f"Sessions endpoint failed: {response.status_code}", "ERROR")
            return False

        # Test speakers endpoint
        response = requests.get(f"{base_url}/speakers", timeout=5)
        if response.status_code == 200:
            speakers = response.json()
            print_status(
                f"Speakers endpoint: retrieved {len(speakers)} speakers", "SUCCESS"
            )
        else:
            print_status(f"Speakers endpoint failed: {response.status_code}", "ERROR")
            return False

        # Test API docs
        response = requests.get(f"{base_url}/docs", timeout=5)
        if response.status_code == 200:
            print_status("API documentation accessible", "SUCCESS")
        else:
            print_status(f"API docs failed: {response.status_code}", "ERROR")
            return False

        print_status("All API endpoints working correctly", "SUCCESS")
        return True

    except requests.exceptions.ConnectionError:
        print_status("Could not connect to API server. Is it running?", "ERROR")
        return False
    except Exception as e:
        print_status(f"API test failed: {e}", "ERROR")
        return False


def start_server():
    """Start the FastAPI server in background."""
    print_status("Starting FastAPI server...", "INFO")

    try:
        # Start server in background
        process = subprocess.Popen(
            [sys.executable, "app.py", "--port", str(config.port)],
            cwd=backend_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to start
        time.sleep(3)

        # Check if process is still running
        if process.poll() is None:
            print_status(
                f"Server started successfully on port {config.port}", "SUCCESS"
            )
            return process
        else:
            stdout, stderr = process.communicate()
            print_status(f"Server failed to start: {stderr.decode()}", "ERROR")
            return None

    except Exception as e:
        print_status(f"Failed to start server: {e}", "ERROR")
        return None


async def main():
    """Main test function."""
    print_status("Starting VoiceAPI Local Setup Test", "INFO")
    print_status(f"Configuration: {config.database}", "INFO")
    print_status(f"Docker mode: {config.is_docker}", "INFO")

    # Test database first
    db_success = await test_database()
    if not db_success:
        print_status("Database tests failed. Exiting.", "ERROR")
        return False

    # Start server
    server_process = start_server()
    if not server_process:
        print_status("Failed to start server. Exiting.", "ERROR")
        return False

    try:
        # Test API endpoints
        api_success = test_api_endpoints()

        if api_success:
            print_status(
                "🎉 All tests passed! Local setup is working correctly.", "SUCCESS"
            )
            print_status(f"API server: http://localhost:{config.port}", "INFO")
            print_status(f"API docs: http://localhost:{config.port}/docs", "INFO")
            return True
        else:
            print_status("Some API tests failed.", "ERROR")
            return False

    finally:
        # Clean up: stop server
        if server_process:
            server_process.terminate()
            server_process.wait()
            print_status("Server stopped", "INFO")


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
