#!/bin/bash

# VoiceAPI Local Startup Script
# This script starts all modules locally without Docker

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
VENV_PATH="$PROJECT_ROOT/.venv"
LOG_DIR="$PROJECT_ROOT/logs"

# Default ports (can be overridden by environment variables)
API_PORT=${API_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-3000}
VAD_PORT=${VAD_PORT:-8002}
ASR_PORT=${ASR_PORT:-8003}
SID_PORT=${SID_PORT:-8004}
KWS_PORT=${KWS_PORT:-8005}
AGENT_PORT=${AGENT_PORT:-8008}

# PIDs file to track running processes
PIDS_FILE="$PROJECT_ROOT/.local_pids"

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}=== $1 ===${NC}"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check if a port is in use
port_in_use() {
    netstat -tuln 2>/dev/null | grep -q ":$1 " || ss -tuln 2>/dev/null | grep -q ":$1 "
}

# Function to kill all tracked processes
cleanup() {
    print_header "Cleaning up processes"
    if [ -f "$PIDS_FILE" ]; then
        while read -r pid name; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                print_status "Stopping $name (PID: $pid)"
                kill -TERM "$pid" 2>/dev/null || true
                sleep 2
                if kill -0 "$pid" 2>/dev/null; then
                    print_warning "Force killing $name (PID: $pid)"
                    kill -KILL "$pid" 2>/dev/null || true
                fi
            fi
        done < "$PIDS_FILE"
        rm -f "$PIDS_FILE"
    fi

    # Also try to kill any remaining processes by name
    pkill -f "backend/vad.py" 2>/dev/null || true
    pkill -f "backend/asr.py" 2>/dev/null || true
    pkill -f "backend/sid.py" 2>/dev/null || true
    pkill -f "backend/kws.py" 2>/dev/null || true
    pkill -f "backend/agent.py" 2>/dev/null || true
    pkill -f "backend/app.py" 2>/dev/null || true

    print_status "Cleanup complete"
}

# Function to start a module in the background
start_module() {
    local script="$1"
    local name="$2"
    local args="$3"
    local log_file="$LOG_DIR/${name}.log"

    print_status "Starting $name..."

    # Create log file
    mkdir -p "$LOG_DIR"
    touch "$log_file"

    # Start the module
    cd "$BACKEND_DIR"
    if [ -d "$VENV_PATH" ]; then
        # Use virtual environment if it exists
        "$VENV_PATH/bin/python" "$script" $args > "$log_file" 2>&1 &
    else
        # Use system python
        python "$script" $args > "$log_file" 2>&1 &
    fi

    local pid=$!
    echo "$pid $name" >> "$PIDS_FILE"

    # Wait a moment and check if the process is still running
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        print_status "$name started successfully (PID: $pid, Log: $log_file)"
    else
        print_error "$name failed to start. Check log: $log_file"
        return 1
    fi
}

# Function to start the frontend
start_frontend() {
    local name="frontend"
    local log_file="$LOG_DIR/${name}.log"

    print_status "Starting $name..."

    # Check if frontend directory exists
    if [ ! -d "$FRONTEND_DIR" ]; then
        print_warning "Frontend directory not found at $FRONTEND_DIR. Skipping frontend."
        return 0
    fi

    # Check if package.json exists
    if [ ! -f "$FRONTEND_DIR/package.json" ]; then
        print_warning "Frontend package.json not found. Skipping frontend."
        return 0
    fi

    # Check if node_modules exists
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        print_warning "Frontend dependencies not installed. Run 'npm install' in the frontend directory."
        print_warning "Skipping frontend for now."
        return 0
    fi

    # Create log file
    mkdir -p "$LOG_DIR"
    touch "$log_file"

    # Start the frontend
    cd "$FRONTEND_DIR"
    PORT=$FRONTEND_PORT npm run dev > "$log_file" 2>&1 &

    local pid=$!
    echo "$pid $name" >> "$PIDS_FILE"

    # Wait a moment and check if the process is still running
    sleep 3
    if kill -0 "$pid" 2>/dev/null; then
        print_status "$name started successfully (PID: $pid, Log: $log_file)"
        print_status "Frontend available at: http://localhost:$FRONTEND_PORT"
    else
        print_error "$name failed to start. Check log: $log_file"
        print_warning "Make sure you have run 'npm install' in the frontend directory"
        return 1
    fi
}

# Function to check dependencies
check_dependencies() {
    print_header "Checking dependencies"

    # Check Python
    if ! command_exists python && ! command_exists python3; then
        print_error "Python is not installed"
        exit 1
    fi

    # Check if virtual environment exists
    if [ ! -d "$VENV_PATH" ]; then
        print_warning "Virtual environment not found at $VENV_PATH"
        print_warning "Consider creating one with: python -m venv .venv && source .venv/bin/activate && pip install -e .[fastapi,sherpa,agent]"
    fi

    # Check if PostgreSQL is available
    if ! command_exists psql; then
        print_warning "PostgreSQL client (psql) not found. Database operations may not work."
    fi

    # Check required files
    local required_files=("backend/app.py" "backend/vad.py" "backend/asr.py" "backend/sid.py" "backend/kws.py" "backend/agent.py")
    for file in "${required_files[@]}"; do
        if [ ! -f "$PROJECT_ROOT/$file" ]; then
            print_error "Required file not found: $file"
            exit 1
        fi
    done

    print_status "Dependencies check passed"
}

# Function to check ports
check_ports() {
    print_header "Checking ports"

    local ports=("$API_PORT" "$VAD_PORT" "$ASR_PORT" "$SID_PORT" "$KWS_PORT" "$AGENT_PORT")
    local port_names=("API" "VAD" "ASR" "SID" "KWS" "AGENT")

    for i in "${!ports[@]}"; do
        if port_in_use "${ports[$i]}"; then
            print_error "Port ${ports[$i]} (${port_names[$i]}) is already in use"
            print_error "Stop the process using this port or change the port configuration"
            exit 1
        fi
    done

    print_status "All ports are available"
}

# Function to wait for database
wait_for_database() {
    print_header "Waiting for database"

    local db_host=${POSTGRES_HOST:-localhost}
    local db_port=${POSTGRES_PORT:-5432}
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if command_exists nc; then
            if nc -z "$db_host" "$db_port" 2>/dev/null; then
                print_status "Database is available"
                return 0
            fi
        elif command_exists telnet; then
            if echo "quit" | telnet "$db_host" "$db_port" 2>/dev/null | grep -q "Connected"; then
                print_status "Database is available"
                return 0
            fi
        else
            print_warning "Cannot check database connectivity (nc or telnet not available)"
            print_warning "Assuming database is available..."
            return 0
        fi

        print_status "Waiting for database... (attempt $attempt/$max_attempts)"
        sleep 2
        ((attempt++))
    done

    print_error "Database not available after $max_attempts attempts"
    print_error "Make sure PostgreSQL is running on $db_host:$db_port"
    exit 1
}

# Function to setup environment
setup_environment() {
    print_header "Setting up environment"

    # Set local development environment variables
    export DOCKER_MODE=false
    export ENVIRONMENT=development
    export DEBUG=false
    export API_HOST=0.0.0.0
    export API_PORT=$API_PORT
    export LOG_LEVEL=INFO

    # Database configuration for local development
    export POSTGRES_HOST=${POSTGRES_HOST:-localhost}
    export POSTGRES_PORT=${POSTGRES_PORT:-5432}
    export POSTGRES_USER=${POSTGRES_USER:-voiceapi}
    export POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-password}
    export POSTGRES_DB=${POSTGRES_DB:-voiceapi}
    export DATABASE_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"

    print_status "Environment configured for local development"
    print_status "Database URL: postgresql://$POSTGRES_USER:***@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"
}

# Function to show module status
show_status() {
    print_header "Module Status"

    if [ ! -f "$PIDS_FILE" ]; then
        print_warning "No running modules found"
        return
    fi

    printf "%-10s %-8s %-8s %s\n" "MODULE" "PID" "STATUS" "LOG"
    printf "%-10s %-8s %-8s %s\n" "------" "---" "------" "---"

    while read -r pid name; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            status="${GREEN}RUNNING${NC}"
        else
            status="${RED}STOPPED${NC}"
        fi
        log_file="$LOG_DIR/${name}.log"
        printf "%-10s %-8s %-15s %s\n" "$name" "$pid" "$(echo -e "$status")" "$log_file"
    done < "$PIDS_FILE"
}

# Function to show help
show_help() {
    echo "VoiceAPI Local Startup Script"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  start     Start all modules and exit (for scripting)"
    echo "  run       Start all modules and keep running for testing"
    echo "  stop      Stop all modules"
    echo "  restart   Restart all modules"
    echo "  status    Show status of all modules"
    echo "  logs      Show logs from all modules"
    echo "  help      Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  API_PORT       API server port (default: 8000)"
    echo "  POSTGRES_HOST  Database host (default: localhost)"
    echo "  POSTGRES_PORT  Database port (default: 5432)"
    echo "  POSTGRES_USER  Database user (default: voiceapi)"
    echo "  POSTGRES_PASSWORD Database password (default: password)"
    echo "  POSTGRES_DB    Database name (default: voiceapi)"
    echo ""
    echo "Examples:"
    echo "  $0 start              # Start and exit (for automation)"
    echo "  $0 run                # Start and keep running (for testing)"
    echo "  API_PORT=8080 $0 run  # Start with custom port"
    echo "  $0 status             # Check module status"
    echo "  $0 logs               # View module logs"
}

# Function to show logs
show_logs() {
    print_header "Module Logs"

    if [ ! -d "$LOG_DIR" ]; then
        print_warning "No logs directory found"
        return
    fi

    for log_file in "$LOG_DIR"/*.log; do
        if [ -f "$log_file" ]; then
            local module_name=$(basename "$log_file" .log)
            print_header "$module_name logs (last 10 lines)"
            tail -n 10 "$log_file" || true
            echo ""
        fi
    done
}

# Function to start all modules
start_all() {
    print_header "Starting VoiceAPI in local mode"

    # Setup environment
    setup_environment

    # Check dependencies and ports
    check_dependencies
    check_ports

    # Wait for database
    wait_for_database

    # Clean up any existing processes
    cleanup

    # Create logs directory
    mkdir -p "$LOG_DIR"

    # Start modules in dependency order
    print_header "Starting modules"

    # Start VAD (Voice Activity Detection)
    start_module "vad.py" "vad" "--provider cpu --model_dir ../models --push_port tcp://127.0.0.1:8002 --pull_port tcp://127.0.0.1:8001"

    # Start ASR (Automatic Speech Recognition)
    start_module "asr.py" "asr" "--provider cpu --model_dir ../models --push_port tcp://127.0.0.1:8003 --pull_port tcp://127.0.0.1:8002"

    # Start SID (Speaker Identification)
    start_module "sid.py" "sid" "--provider cpu --model_dir ../models --push_port tcp://127.0.0.1:8004 --pull_port tcp://127.0.0.1:8002"

    # Start KWS (Keyword Spotting)
    start_module "kws.py" "kws" "--provider cpu --model_dir ../models --push_port tcp://127.0.0.1:8005 --pull_port tcp://127.0.0.1:8002"

    # Start Agent
    start_module "agent.py" "agent" ""

    # Start main API server
    start_module "app.py" "app" "--addr 0.0.0.0 --port $API_PORT"

    # Start frontend
    start_frontend

    print_header "All modules started successfully!"
    print_status "Frontend: http://localhost:$FRONTEND_PORT"
    print_status "API server: http://localhost:$API_PORT"
    print_status "Health check: http://localhost:$API_PORT/health"
    print_status "API docs: http://localhost:$API_PORT/docs"
    print_status ""
    print_status "Use '$0 status' to check module status"
    print_status "Use '$0 logs' to view logs"
    print_status "Use '$0 stop' to stop all modules"
}

# Function to run interactively
run_interactive() {
    # Don't cleanup on exit for interactive mode
    trap '' EXIT

    start_all

    print_header "Running in interactive mode"
    print_status "All services are running. Press Ctrl+C to stop all services and exit."
    print_status "Open your browser and test the frontend: http://localhost:$FRONTEND_PORT"
    print_status ""
    print_status "Available endpoints:"
    print_status "  • Frontend: http://localhost:$FRONTEND_PORT"
    print_status "  • API server: http://localhost:$API_PORT"
    print_status "  • API docs: http://localhost:$API_PORT/docs"
    print_status "  • Health check: http://localhost:$API_PORT/health"
    print_status "  • Sessions: http://localhost:$API_PORT/sessions"
    print_status ""
    print_status "In another terminal, you can run:"
    print_status "  • $0 status   # Check module status"
    print_status "  • $0 logs     # View logs"
    print_status ""

    # Setup trap for interactive cleanup
    trap 'print_header "Shutting down..."; cleanup; exit 0' INT TERM

    # Wait indefinitely
    while true; do
        sleep 10
        # Check if all processes are still running
        if [ -f "$PIDS_FILE" ]; then
            local all_running=true
            while read -r pid name; do
                if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
                    print_error "$name (PID: $pid) has stopped unexpectedly"
                    all_running=false
                fi
            done < "$PIDS_FILE"

            if [ "$all_running" = false ]; then
                print_error "Some services have stopped. Exiting..."
                cleanup
                exit 1
            fi
        else
            print_error "No running processes found. Exiting..."
            exit 1
        fi
    done
}

# Main script logic
case "${1:-start}" in
    start)
        # Trap to cleanup on script exit for start command
        trap cleanup EXIT
        start_all
        ;;
    run)
        run_interactive
        ;;
    stop)
        cleanup
        ;;
    restart)
        cleanup
        sleep 2
        # Trap to cleanup on script exit for restart command
        trap cleanup EXIT
        start_all
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
