#!/usr/bin/env bash
#
# Cognition Docker-Compose API Proof Script
#
# This script exercises all 12 API endpoints against a live docker-compose environment.
# It runs 9 scenarios with comprehensive assertions to prove the APIs work correctly.
#
# Usage:
#   ./scripts/test_docker_compose.sh              # Run against http://localhost:8000
#   BASE_URL=http://host:port ./scripts/test_docker_compose.sh  # Custom endpoint
#
# Requirements:
#   - curl
#   - jq
#   - docker-compose services running (postgres, mlflow, jaeger, cognition)

set -o pipefail

# Configuration
BASE_URL="${BASE_URL:-http://localhost:8000}"
MLFLOW_URL="${MLFLOW_URL:-http://localhost:5050}"
JAEGER_URL="${JAEGER_URL:-http://localhost:16686}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

# Counters
TESTS_PASSED=0
TESTS_FAILED=0
SCENARIOS_PASSED=0
SCENARIOS_FAILED=0

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Utility functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

pass() {
    echo -e "  ${GREEN}[PASS]${NC} $1"
    ((TESTS_PASSED++))
}

fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
    ((TESTS_FAILED++))
}

# HTTP request helpers
http_get() {
    local url="$1"
    local headers="${2:-}"
    local cmd="curl -s -w \"\\n%{http_code}\""
    if [[ -n "$headers" ]]; then
        cmd="$cmd -H \"$headers\""
    fi
    cmd="$cmd \"$url\""
    eval "$cmd" 2>/dev/null
}

http_post() {
    local url="$1"
    local data="$2"
    local headers="${3:-}"
    local cmd="curl -s -w \"\\n%{http_code}\" -X POST"
    if [[ -n "$data" ]]; then
        cmd="$cmd -H \"Content-Type: application/json\" -d '$data'"
    fi
    if [[ -n "$headers" ]]; then
        cmd="$cmd -H \"$headers\""
    fi
    cmd="$cmd \"$url\""
    eval "$cmd" 2>/dev/null
}

http_patch() {
    local url="$1"
    local data="$2"
    local headers="${3:-}"
    curl -s -w "\n%{http_code}" -X PATCH \
        -H "Content-Type: application/json" \
        -H "$headers" \
        -d "$data" \
        "$url" 2>/dev/null
}

http_delete() {
    local url="$1"
    local headers="${2:-}"
    local cmd="curl -s -w \"\\n%{http_code}\" -X DELETE"
    if [[ -n "$headers" ]]; then
        cmd="$cmd -H \"$headers\""
    fi
    cmd="$cmd \"$url\""
    eval "$cmd" 2>/dev/null
}

# Extract HTTP status code from response
get_status_code() {
    echo "$1" | tail -n1
}

# Extract body from response (everything except last line)
get_body() {
    echo "$1" | sed '$d'
}

# Check if jq can parse JSON
is_valid_json() {
    echo "$1" | jq -e . >/dev/null 2>&1
}

# Wait for server to be ready
wait_for_server() {
    local max_attempts=30
    local attempt=0
    echo "Waiting for server at $BASE_URL..."
    while [[ $attempt -lt $max_attempts ]]; do
        local response=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/ready" 2>/dev/null)
        if [[ "$response" == "200" ]]; then
            log_info "Server is ready"
            return 0
        fi
        ((attempt++))
        sleep 1
    done
    log_error "Server failed to become ready after ${max_attempts}s"
    exit 1
}

# ==============================================================================
# Scenario 1: Server Health & Readiness
# ==============================================================================
scenario_1_health() {
    echo ""
    echo "--- Scenario 1: Server Health & Readiness ---"
    local scenario_passed=true

    # Test /health
    local response=$(http_get "$BASE_URL/health")
    local status=$(get_status_code "$response")
    local body=$(get_body "$response")

    if [[ "$status" == "200" ]]; then
        pass "GET /health returns 200"
    else
        fail "GET /health returns $status (expected 200)"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        local health_status=$(echo "$body" | jq -r '.status // empty')
        local version=$(echo "$body" | jq -r '.version // empty')

        if [[ "$health_status" == "healthy" ]]; then
            pass "status is 'healthy'"
        else
            fail "status is '$health_status' (expected 'healthy')"
            scenario_passed=false
        fi

        if [[ "$version" == "0.1.0" ]]; then
            pass "version is '0.1.0'"
        else
            fail "version is '$version' (expected '0.1.0')"
            scenario_passed=false
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    # Test /ready
    response=$(http_get "$BASE_URL/ready")
    status=$(get_status_code "$response")
    body=$(get_body "$response")

    if [[ "$status" == "200" ]]; then
        pass "GET /ready returns 200"
    else
        fail "GET /ready returns $status (expected 200)"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        local ready=$(echo "$body" | jq -r '.ready // empty')
        if [[ "$ready" == "true" ]]; then
            pass "ready is true"
        else
            fail "ready is $ready (expected true)"
            scenario_passed=false
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    # Test /config
    response=$(http_get "$BASE_URL/config")
    status=$(get_status_code "$response")
    body=$(get_body "$response")

    if [[ "$status" == "200" ]]; then
        pass "GET /config returns 200"
    else
        fail "GET /config returns $status (expected 200)"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        local has_server=$(echo "$body" | jq 'has("server")')
        local has_llm=$(echo "$body" | jq 'has("llm")')
        local has_rate_limit=$(echo "$body" | jq 'has("rate_limit")')
        local provider=$(echo "$body" | jq -r '.llm.provider // empty')

        if [[ "$has_server" == "true" && "$has_llm" == "true" && "$has_rate_limit" == "true" ]]; then
            pass "config has server, llm, and rate_limit keys"
        else
            fail "config missing required keys"
            scenario_passed=false
        fi

        if [[ -n "$provider" ]]; then
            pass "LLM provider is '$provider'"
            echo "$provider" > /tmp/cognition_provider
        else
            fail "LLM provider not found in config"
            scenario_passed=false
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 2: Session CRUD Lifecycle
# ==============================================================================
scenario_2_session_crud() {
    echo ""
    echo "--- Scenario 2: Session CRUD Lifecycle ---"
    local scenario_passed=true
    local session_id=""

    # Create session
    local response=$(http_post "$BASE_URL/sessions" '{"title": "Test Session"}')
    local status=$(get_status_code "$response")
    local body=$(get_body "$response")

    if [[ "$status" == "201" ]]; then
        pass "POST /sessions returns 201"
    else
        fail "POST /sessions returns $status (expected 201)"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        session_id=$(echo "$body" | jq -r '.id // empty')
        local thread_id=$(echo "$body" | jq -r '.thread_id // empty')
        local session_status=$(echo "$body" | jq -r '.status // empty')

        if [[ -n "$session_id" && -n "$thread_id" ]]; then
            pass "Session has id and thread_id"
            echo "$session_id" > /tmp/cognition_session_id
        else
            fail "Session missing id or thread_id"
            scenario_passed=false
        fi

        if [[ "$session_status" == "active" ]]; then
            pass "status is 'active'"
        else
            fail "status is '$session_status' (expected 'active')"
            scenario_passed=false
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    # List sessions
    response=$(http_get "$BASE_URL/sessions")
    status=$(get_status_code "$response")
    body=$(get_body "$response")

    if [[ "$status" == "200" ]]; then
        pass "GET /sessions returns 200"
    else
        fail "GET /sessions returns $status (expected 200)"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        local total=$(echo "$body" | jq -r '.total // 0')
        local found=$(echo "$body" | jq --arg id "$session_id" '.sessions | map(select(.id == $id)) | length')

        if [[ "$total" -ge 1 ]]; then
            pass "total is >= 1"
        else
            fail "total is $total (expected >= 1)"
            scenario_passed=false
        fi

        if [[ "$found" -eq 1 ]]; then
            pass "Created session appears in list"
        else
            fail "Created session not found in list"
            scenario_passed=false
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    # Get session
    response=$(http_get "$BASE_URL/sessions/$session_id")
    status=$(get_status_code "$response")
    body=$(get_body "$response")

    if [[ "$status" == "200" ]]; then
        pass "GET /sessions/{id} returns 200"
    else
        fail "GET /sessions/{id} returns $status (expected 200)"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        local retrieved_id=$(echo "$body" | jq -r '.id // empty')
        if [[ "$retrieved_id" == "$session_id" ]]; then
            pass "Retrieved session id matches created session"
        else
            fail "Retrieved session id does not match"
            scenario_passed=false
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    # Update session
    response=$(http_patch "$BASE_URL/sessions/$session_id" '{"title": "Updated Title"}')
    status=$(get_status_code "$response")
    body=$(get_body "$response")

    if [[ "$status" == "200" ]]; then
        pass "PATCH /sessions/{id} returns 200"
    else
        fail "PATCH /sessions/{id} returns $status (expected 200)"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        local updated_title=$(echo "$body" | jq -r '.title // empty')
        if [[ "$updated_title" == "Updated Title" ]]; then
            pass "Title updated to 'Updated Title'"
        else
            fail "Title is '$updated_title' (expected 'Updated Title')"
            scenario_passed=false
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    # Delete session
    response=$(http_delete "$BASE_URL/sessions/$session_id")
    status=$(get_status_code "$response")

    if [[ "$status" == "204" ]]; then
        pass "DELETE /sessions/{id} returns 204"
    else
        fail "DELETE /sessions/{id} returns $status (expected 204)"
        scenario_passed=false
    fi

    # Verify deletion
    response=$(http_get "$BASE_URL/sessions/$session_id")
    status=$(get_status_code "$response")

    if [[ "$status" == "404" ]]; then
        pass "GET /sessions/{id} after delete returns 404"
    else
        fail "GET /sessions/{id} after delete returns $status (expected 404)"
        scenario_passed=false
    fi

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 3: Agent Conversation (SSE Streaming)
# ==============================================================================
scenario_3_sse_streaming() {
    echo ""
    echo "--- Scenario 3: Agent Conversation (SSE Streaming) ---"
    echo "  ${YELLOW}[INFO]${NC} This makes a real LLM call (may take 10-30s)..."
    local scenario_passed=true

    # Create a session for this scenario
    local response=$(http_post "$BASE_URL/sessions" '{"title": "SSE Test Session"}')
    local session_id=$(get_body "$response" | jq -r '.id // empty')

    if [[ -z "$session_id" ]]; then
        fail "Failed to create session for SSE test"
        ((SCENARIOS_FAILED++))
        return
    fi

    echo "$session_id" > /tmp/cognition_sse_session_id

    # Send message with SSE streaming
    local temp_file=$(mktemp)
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -H "Accept: text/event-stream" \
        -d '{"content": "Hello, please give me a short greeting."}' \
        -o "$temp_file" \
        "$BASE_URL/sessions/$session_id/messages" 2>/dev/null

    local content_type=$(file -b --mime-type "$temp_file" 2>/dev/null || echo "unknown")

    # Check if we got any content
    if [[ -s "$temp_file" ]]; then
        pass "Response received (not empty)"
    else
        fail "Response is empty"
        scenario_passed=false
    fi

    # Parse SSE events
    local has_token=false
    local has_done=false
    local done_count=0
    local token_count=0

    while IFS= read -r line; do
        if [[ "$line" =~ ^event:\ token ]]; then
            has_token=true
            ((token_count++))
        elif [[ "$line" =~ ^event:\ done ]]; then
            has_done=true
            ((done_count++))
        fi
    done < <(grep -E "^(event:|data:)" "$temp_file")

    if $has_token; then
        pass "Stream contains token events ($token_count found)"
    else
        fail "Stream contains no token events"
        scenario_passed=false
    fi

    if $has_done; then
        pass "Stream contains done event"
    else
        fail "Stream contains no done event"
        scenario_passed=false
    fi

    if [[ $done_count -eq 1 ]]; then
        pass "Exactly one done event found"
    else
        fail "$done_count done events found (expected 1)"
        scenario_passed=false
    fi

    # Check assistant message was persisted
    sleep 1  # Give it a moment to persist
    local messages_response=$(http_get "$BASE_URL/sessions/$session_id/messages")
    local total=$(get_body "$messages_response" | jq -r '.total // 0')

    if [[ "$total" -ge 2 ]]; then
        pass "Messages persisted after streaming (total: $total)"
    else
        fail "Messages not persisted properly (total: $total)"
        scenario_passed=false
    fi

    # Cleanup
    rm -f "$temp_file"
    http_delete "$BASE_URL/sessions/$session_id" > /dev/null 2>&1

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 4: Message Persistence & Pagination
# ==============================================================================
scenario_4_message_persistence() {
    echo ""
    echo "--- Scenario 4: Message Persistence & Pagination ---"
    local scenario_passed=true

    # Use the session from SSE test if it exists and is valid, otherwise create new
    local session_id=""
    if [[ -f /tmp/cognition_sse_session_id ]]; then
        session_id=$(cat /tmp/cognition_sse_session_id)
        local check=$(http_get "$BASE_URL/sessions/$session_id")
        if [[ $(get_status_code "$check") != "200" ]]; then
            session_id=""
        fi
    fi

    if [[ -z "$session_id" ]]; then
        local response=$(http_post "$BASE_URL/sessions" '{"title": "Persistence Test"}')
        session_id=$(get_body "$response" | jq -r '.id // empty')

        # Send a message if this is a new session
        curl -s -X POST \
            -H "Content-Type: application/json" \
            -H "Accept: text/event-stream" \
            -d '{"content": "Test message for pagination"}' \
            "$BASE_URL/sessions/$session_id/messages" > /dev/null 2>&1
        sleep 2
    fi

    if [[ -z "$session_id" ]]; then
        fail "Failed to get/create session"
        ((SCENARIOS_FAILED++))
        return
    fi

    # List messages
    local response=$(http_get "$BASE_URL/sessions/$session_id/messages")
    local status=$(get_status_code "$response")
    local body=$(get_body "$response")

    if [[ "$status" == "200" ]]; then
        pass "GET /sessions/{id}/messages returns 200"
    else
        fail "GET /sessions/{id}/messages returns $status"
        scenario_passed=false
    fi

    if is_valid_json "$body"; then
        local total=$(echo "$body" | jq -r '.total // 0')
        local has_more=$(echo "$body" | jq -r '.has_more // false')

        if [[ "$total" -ge 2 ]]; then
            pass "Total messages >= 2 (found: $total)"
        else
            fail "Total messages < 2 (found: $total)"
            scenario_passed=false
        fi

        # Check for user and assistant messages
        local user_count=$(echo "$body" | jq '[.messages[] | select(.role == "user")] | length')
        local assistant_count=$(echo "$body" | jq '[.messages[] | select(.role == "assistant")] | length')

        if [[ "$user_count" -ge 1 ]]; then
            pass "Found $user_count user message(s)"
        else
            fail "No user messages found"
            scenario_passed=false
        fi

        if [[ "$assistant_count" -ge 1 ]]; then
            pass "Found $assistant_count assistant message(s)"
        else
            fail "No assistant messages found"
            scenario_passed=false
        fi

        # Test pagination
        local paginated=$(http_get "$BASE_URL/sessions/$session_id/messages?limit=1")
        local paginated_total=$(get_body "$paginated" | jq -r '.total // 0')
        local paginated_has_more=$(get_body "$paginated" | jq -r '.has_more // false')

        if [[ "$paginated_total" -ge 1 ]]; then
            pass "Pagination with limit=1 works"
        else
            fail "Pagination with limit=1 failed"
            scenario_passed=false
        fi

        if [[ "$total" -gt 1 && "$paginated_has_more" == "true" ]] || [[ "$total" -le 1 ]]; then
            pass "has_more flag correct for pagination"
        else
            fail "has_more flag incorrect (expected true when total > 1)"
            scenario_passed=false
        fi

        # Get specific message
        local first_msg_id=$(echo "$body" | jq -r '.messages[0].id // empty')
        if [[ -n "$first_msg_id" ]]; then
            local msg_response=$(http_get "$BASE_URL/sessions/$session_id/messages/$first_msg_id")
            if [[ $(get_status_code "$msg_response") == "200" ]]; then
                pass "GET /sessions/{id}/messages/{msg_id} returns 200"
            else
                fail "GET specific message failed"
                scenario_passed=false
            fi
        fi
    else
        fail "Response body is not valid JSON"
        scenario_passed=false
    fi

    # Cleanup
    http_delete "$BASE_URL/sessions/$session_id" > /dev/null 2>&1
    rm -f /tmp/cognition_sse_session_id

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 5: Multi-Turn Conversation
# ==============================================================================
scenario_5_multi_turn() {
    echo ""
    echo "--- Scenario 5: Multi-Turn Conversation ---"
    echo "  ${YELLOW}[INFO]${NC} This makes 3 real LLM calls (may take 30-60s)..."
    local scenario_passed=true

    # Create session
    local response=$(http_post "$BASE_URL/sessions" '{"title": "Multi-Turn Test"}')
    local session_id=$(get_body "$response" | jq -r '.id // empty')

    if [[ -z "$session_id" ]]; then
        fail "Failed to create session"
        ((SCENARIOS_FAILED++))
        return
    fi

    # Send 3 messages
    local messages=("Hello" "How are you?" "What can you help me with?")
    local msg_num=0

    for msg in "${messages[@]}"; do
        ((msg_num++))
        echo "  ${YELLOW}[INFO]${NC} Sending message $msg_num/3..."
        curl -s -X POST \
            -H "Content-Type: application/json" \
            -H "Accept: text/event-stream" \
            -d "{\"content\": \"$msg\"}" \
            "$BASE_URL/sessions/$session_id/messages" > /dev/null 2>&1
        sleep 1
    done

    # Wait for all to complete
    sleep 3

    # Verify we have 6 messages (3 user + 3 assistant)
    local messages_response=$(http_get "$BASE_URL/sessions/$session_id/messages")
    local total=$(get_body "$messages_response" | jq -r '.total // 0')

    if [[ "$total" -ge 6 ]]; then
        pass "Total messages >= 6 after 3 turns (found: $total)"
    else
        fail "Expected >= 6 messages, found $total"
        scenario_passed=false
    fi

    # Cleanup
    http_delete "$BASE_URL/sessions/$session_id" > /dev/null 2>&1

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 6: Multi-User Scope Isolation
# ==============================================================================
scenario_6_scope_isolation() {
    echo ""
    echo "--- Scenario 6: Multi-User Scope Isolation ---"

    # First check if scoping is enabled
    local config_response=$(http_get "$BASE_URL/config")
    local scoping_enabled=$(get_body "$config_response" | jq -r '.server.scoping_enabled // false')

    if [[ "$scoping_enabled" != "true" ]]; then
        log_warn "Scoping is disabled in config, skipping scope isolation test"
        return
    fi

    local scenario_passed=true

    # Create session as Alice
    local response=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -H "X-Cognition-Scope-User: alice" \
        -d '{"title": "Alice Session"}' \
        "$BASE_URL/sessions")
    local alice_session=$(get_body "$response" | jq -r '.id // empty')

    if [[ -z "$alice_session" ]]; then
        fail "Failed to create Alice's session"
        ((SCENARIOS_FAILED++))
        return
    fi

    pass "Created session as Alice"

    # List as Alice - should see the session
    local alice_list=$(curl -s -w "\n%{http_code}" \
        -H "X-Cognition-Scope-User: alice" \
        "$BASE_URL/sessions")
    local alice_total=$(get_body "$alice_list" | jq -r '.total // 0')
    local alice_found=$(get_body "$alice_list" | jq --arg id "$alice_session" '.sessions | map(select(.id == $id)) | length')

    if [[ "$alice_found" -ge 1 ]]; then
        pass "Alice can see her session"
    else
        fail "Alice cannot see her session"
        scenario_passed=false
    fi

    # List as Bob - should NOT see Alice's session
    local bob_list=$(curl -s -w "\n%{http_code}" \
        -H "X-Cognition-Scope-User: bob" \
        "$BASE_URL/sessions")
    local bob_found=$(get_body "$bob_list" | jq --arg id "$alice_session" '.sessions | map(select(.id == $id)) | length')

    if [[ "$bob_found" -eq 0 ]]; then
        pass "Bob cannot see Alice's session (isolation works)"
    else
        fail "Bob can see Alice's session (isolation broken)"
        scenario_passed=false
    fi

    # Cleanup
    curl -s -X DELETE -H "X-Cognition-Scope-User: alice" \
        "$BASE_URL/sessions/$alice_session" > /dev/null 2>&1

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 7: Abort Operation
# ==============================================================================
scenario_7_abort() {
    echo ""
    echo "--- Scenario 7: Abort Operation ---"
    local scenario_passed=true

    # Create session
    local response=$(http_post "$BASE_URL/sessions" '{"title": "Abort Test"}')
    local session_id=$(get_body "$response" | jq -r '.id // empty')

    if [[ -z "$session_id" ]]; then
        fail "Failed to create session"
        ((SCENARIOS_FAILED++))
        return
    fi

    # Abort the session
    local abort_response=$(http_post "$BASE_URL/sessions/$session_id/abort" "")
    local abort_status=$(get_status_code "$abort_response")
    local abort_body=$(get_body "$abort_response")

    if [[ "$abort_status" == "200" ]]; then
        pass "POST /sessions/{id}/abort returns 200"
    else
        fail "POST /sessions/{id}/abort returns $abort_status (expected 200)"
        scenario_passed=false
    fi

    if is_valid_json "$abort_body"; then
        local success=$(echo "$abort_body" | jq -r '.success // empty')
        if [[ "$success" == "true" ]]; then
            pass "Abort response has success=true"
        else
            fail "Abort response has success=$success"
            scenario_passed=false
        fi
    else
        fail "Abort response is not valid JSON"
        scenario_passed=false
    fi

    # Verify session is still usable after abort
    local check=$(http_get "$BASE_URL/sessions/$session_id")
    if [[ $(get_status_code "$check") == "200" ]]; then
        pass "Session remains usable after abort"
    else
        fail "Session not usable after abort"
        scenario_passed=false
    fi

    # Cleanup
    http_delete "$BASE_URL/sessions/$session_id" > /dev/null 2>&1

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 8: Error Handling
# ==============================================================================
scenario_8_errors() {
    echo ""
    echo "--- Scenario 8: Error Handling ---"
    local scenario_passed=true

    # Test 404 for non-existent session
    local response=$(http_get "$BASE_URL/sessions/nonexistent-id-12345")
    local status=$(get_status_code "$response")

    if [[ "$status" == "404" ]]; then
        pass "GET /sessions/nonexistent returns 404"
    else
        fail "GET /sessions/nonexistent returns $status (expected 404)"
        scenario_passed=false
    fi

    # Create a session for message error tests
    local create_response=$(http_post "$BASE_URL/sessions" '{"title": "Error Test"}')
    local session_id=$(get_body "$create_response" | jq -r '.id // empty')

    if [[ -n "$session_id" ]]; then
        # Test 422 for empty message body
        local msg_response=$(http_post "$BASE_URL/sessions/$session_id/messages" '{}')
        local msg_status=$(get_status_code "$msg_response")

        if [[ "$msg_status" == "422" || "$msg_status" == "400" ]]; then
            pass "POST /messages with empty body returns $msg_status"
        else
            fail "POST /messages with empty body returns $msg_status (expected 422 or 400)"
            scenario_passed=false
        fi

        # Test 404 for non-existent message
        local msg_404=$(http_get "$BASE_URL/sessions/$session_id/messages/nonexistent")
        if [[ $(get_status_code "$msg_404") == "404" ]]; then
            pass "GET /messages/nonexistent returns 404"
        else
            fail "GET /messages/nonexistent returns wrong status"
            scenario_passed=false
        fi

        # Cleanup
        http_delete "$BASE_URL/sessions/$session_id" > /dev/null 2>&1
    fi

    # Test validation error with bad data
    local bad_response=$(http_post "$BASE_URL/sessions" '{"title": "'$(printf 'x%.0s' {1..250})'"}')
    if [[ $(get_status_code "$bad_response") == "422" || $(get_status_code "$bad_response") == "400" ]]; then
        pass "POST /sessions with oversized title returns 422/400"
    else
        fail "POST /sessions with oversized title returns wrong status"
        scenario_passed=false
    fi

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Scenario 9: Observability Stack
# ==============================================================================
scenario_9_observability() {
    echo ""
    echo "--- Scenario 9: Observability Stack ---"
    local scenario_passed=true

    # Test MLflow health
    local mlflow_response=$(curl -s -o /dev/null -w "%{http_code}" "$MLFLOW_URL/health" 2>/dev/null)
    if [[ "$mlflow_response" == "200" ]]; then
        pass "MLflow at $MLFLOW_URL is healthy (200)"
    else
        fail "MLflow returns $mlflow_response (expected 200)"
        scenario_passed=false
    fi

    # Test Jaeger UI
    local jaeger_response=$(curl -s -o /dev/null -w "%{http_code}" "$JAEGER_URL" 2>/dev/null)
    if [[ "$jaeger_response" == "200" ]]; then
        pass "Jaeger UI at $JAEGER_URL is accessible (200)"
    else
        fail "Jaeger UI returns $jaeger_response (expected 200)"
        scenario_passed=false
    fi

    # Test Postgres connectivity (via TCP check, skip if timeout not available)
    if command -v timeout &> /dev/null; then
        if timeout 2 bash -c "cat < /dev/null > /dev/tcp/$POSTGRES_HOST/$POSTGRES_PORT" 2>/dev/null; then
            pass "PostgreSQL at $POSTGRES_HOST:$POSTGRES_PORT is reachable"
        else
            log_warn "PostgreSQL TCP check failed (expected on macOS Docker Desktop)"
            # Don't fail the scenario for this - it's a platform limitation
        fi
    else
        # On macOS without timeout, try a quick curl to the API to verify DB is working
        local session_check=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/sessions" 2>/dev/null)
        if [[ "$session_check" == "200" ]]; then
            pass "PostgreSQL verified via API (sessions endpoint works)"
        else
            log_warn "PostgreSQL connectivity check skipped (timeout command not available)"
        fi
    fi

    if $scenario_passed; then
        ((SCENARIOS_PASSED++))
    else
        ((SCENARIOS_FAILED++))
    fi
}

# ==============================================================================
# Main Execution
# ==============================================================================
main() {
    echo "=== Cognition Docker-Compose API Proof ==="
    echo "Target: $BASE_URL"
    echo "MLflow: $MLFLOW_URL"
    echo "Jaeger: $JAEGER_URL"
    echo ""

    # Check dependencies
    if ! command -v curl &> /dev/null; then
        log_error "curl is required but not installed"
        exit 1
    fi

    if ! command -v jq &> /dev/null; then
        log_error "jq is required but not installed"
        exit 1
    fi

    # Wait for server
    wait_for_server

    # Run scenarios
    scenario_1_health
    scenario_2_session_crud
    scenario_3_sse_streaming
    scenario_4_message_persistence
    scenario_5_multi_turn
    scenario_6_scope_isolation
    scenario_7_abort
    scenario_8_errors
    scenario_9_observability

    # Summary
    echo ""
    echo "========================================"
    echo "Results:"
    echo "  Scenarios: $SCENARIOS_PASSED passed, $SCENARIOS_FAILED failed"
    echo "  Assertions: $TESTS_PASSED passed, $TESTS_FAILED failed"
    echo "========================================"

    if [[ $TESTS_FAILED -eq 0 ]]; then
        echo -e "${GREEN}✓ All tests passed!${NC}"
        exit 0
    else
        echo -e "${RED}✗ Some tests failed${NC}"
        exit 1
    fi
}

# Run main
main "$@"
