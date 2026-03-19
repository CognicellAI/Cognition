#!/bin/bash
#
# Deploy Cognition to Kubernetes (dubaimetal cluster)
#
# Usage:
#   ./deploy.sh                    # Deploy with defaults
#   ./deploy.sh --api-key sk-...   # Deploy with API key
#   ./deploy.sh --tag 0.3.0        # Deploy specific version
#   ./deploy.sh --dry-run          # Preview changes without deploying
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE="cognition"
DB_NAMESPACE="cognition"
CHART_PATH="$(dirname "$0")/helm/cognition"
CNPG_PATH="$(dirname "$0")/cnpg/cluster.yaml"
DRY_RUN=false
API_KEY=""
IMAGE_TAG=""
WAIT_TIMEOUT="300s"

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check kubectl
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl is not installed"
        exit 1
    fi
    
    # Check helm
    if ! command -v helm &> /dev/null; then
        log_error "helm is not installed"
        exit 1
    fi
    
    # Check cluster connection
    if ! kubectl cluster-info &> /dev/null; then
        log_error "Cannot connect to Kubernetes cluster"
        exit 1
    fi
    
    # Check current context
    CURRENT_CONTEXT=$(kubectl config current-context)
    log_info "Connected to cluster: $CURRENT_CONTEXT"
    
    log_success "Prerequisites check passed"
}

# Deploy PostgreSQL cluster
deploy_postgres() {
    log_info "Deploying PostgreSQL CNPG cluster..."
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY-RUN] Would apply: $CNPG_PATH"
        return
    fi
    
    # Create namespace if it doesn't exist
    kubectl create namespace "$DB_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    
    # Apply CNPG cluster
    kubectl apply -f "$CNPG_PATH"
    
    log_info "Waiting for PostgreSQL cluster to be ready (timeout: $WAIT_TIMEOUT)..."
    if kubectl wait --for=condition=Ready "cluster/cognition-db" -n "$DB_NAMESPACE" --timeout="$WAIT_TIMEOUT"; then
        log_success "PostgreSQL cluster is ready"
    else
        log_error "PostgreSQL cluster failed to become ready"
        log_info "Check status with: kubectl get clusters -n $DB_NAMESPACE"
        exit 1
    fi
}

# Create database credentials secret
create_db_secret() {
    log_info "Creating database credentials secret..."
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY-RUN] Would create secret: cognition-db-credentials"
        return
    fi
    
    # Get password from init secret
    DB_PASSWORD=$(kubectl get secret cognition-db-init-credentials -n "$DB_NAMESPACE" -o jsonpath='{.data.password}' | base64 -d)
    
    if [ -z "$DB_PASSWORD" ]; then
        log_error "Could not retrieve database password"
        exit 1
    fi
    
    # Create or update secret
    kubectl create secret generic cognition-db-credentials \
        --namespace "$NAMESPACE" \
        --from-literal=uri="postgresql+asyncpg://cognition:${DB_PASSWORD}@cognition-db-rw:5432/cognition" \
        --dry-run=client -o yaml | kubectl apply -f -
    
    log_success "Database credentials secret created"
}

# Create API keys secret
create_api_secret() {
    if [ -z "$API_KEY" ]; then
        log_warn "No API key provided. Cognition will fail to start without LLM credentials."
        log_info "Set API key with: --api-key sk-..."
        return
    fi
    
    log_info "Creating API keys secret..."
    
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY-RUN] Would create secret: cognition-api-keys"
        return
    fi
    
    kubectl create secret generic cognition-api-keys \
        --namespace "$NAMESPACE" \
        --from-literal=openai-compatible-api-key="$API_KEY" \
        --dry-run=client -o yaml | kubectl apply -f -
    
    log_success "API keys secret created"
}

# Deploy Cognition Helm chart
deploy_cognition() {
    log_info "Deploying Cognition Helm chart..."
    
    # Build helm command
    HELM_CMD="helm upgrade --install cognition $CHART_PATH \
        --namespace $NAMESPACE \
        --create-namespace"
    
    if [ -n "$IMAGE_TAG" ]; then
        HELM_CMD="$HELM_CMD --set image.tag=$IMAGE_TAG"
    fi
    
    if [ "$DRY_RUN" = true ]; then
        HELM_CMD="$HELM_CMD --dry-run --debug"
        log_info "[DRY-RUN] Helm command:"
        echo "$HELM_CMD"
    fi
    
    # Execute helm command
    eval "$HELM_CMD"
    
    if [ "$DRY_RUN" = false ]; then
        log_success "Cognition deployed"
        
        log_info "Waiting for Cognition pods to be ready..."
        kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cognition -n "$NAMESPACE" --timeout="$WAIT_TIMEOUT"
        log_success "Cognition is ready"
    fi
}

# Wait for deployment
wait_for_deployment() {
    if [ "$DRY_RUN" = true ]; then
        return
    fi
    
    log_info "Checking deployment status..."
    
    # Check if Tailscale ingress is ready
    log_info "Checking Tailscale ingress..."
    if kubectl get ingress cognition -n "$NAMESPACE" &> /dev/null; then
        log_success "Tailscale ingress configured"
        log_info "Access Cognition at: http://cognition:8000"
    fi
}

# Print deployment info
print_info() {
    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY-RUN] Deployment preview complete"
        return
    fi
    
    echo ""
    log_success "Cognition deployment complete!"
    echo ""
    echo -e "${GREEN}Access Information:${NC}"
    echo "  - API Endpoint: http://cognition:8000 (via Tailscale)"
    echo "  - Health Check: curl http://cognition:8000/health"
    echo ""
    echo -e "${GREEN}Useful Commands:${NC}"
    echo "  - View logs: kubectl logs -n $NAMESPACE deployment/cognition -f"
    echo "  - Get pods: kubectl get pods -n $NAMESPACE"
    echo "  - Port forward: kubectl port-forward -n $NAMESPACE svc/cognition 8000:8000"
    echo "  - Upgrade: ./deploy.sh --api-key <key>"
    echo ""
    echo -e "${GREEN}Observability (optional):${NC}"
    echo "  - Deploy: helm install observe $(dirname "$0")/helm/cognition-observe --namespace cognition-observe --create-namespace"
    echo ""
}

# Cleanup function
cleanup() {
    if [ "$DRY_RUN" = true ]; then
        return
    fi
    
    log_info "Cleaning up..."
    # Nothing to clean up by default
}

# Main function
main() {
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --api-key)
                API_KEY="$2"
                shift 2
                ;;
            --tag)
                IMAGE_TAG="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --timeout)
                WAIT_TIMEOUT="$2"
                shift 2
                ;;
            --help|-h)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --api-key <key>     Set OpenAI Compatible API key"
                echo "  --tag <version>     Deploy specific image tag (e.g., 0.3.0, latest)"
                echo "  --dry-run           Preview changes without deploying"
                echo "  --timeout <duration> Timeout for waits (default: 300s)"
                echo "  --help, -h          Show this help message"
                echo ""
                echo "Examples:"
                echo "  $0                                    # Deploy with defaults"
                echo "  $0 --api-key sk-or-xxx                # Deploy with API key"
                echo "  $0 --tag 0.3.0                        # Deploy specific version"
                echo "  $0 --api-key sk-or-xxx --tag latest   # Deploy latest with key"
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
    
    # Trap cleanup
    trap cleanup EXIT
    
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}  Cognition Deployment Script${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""
    
    # Run deployment steps
    check_prerequisites
    deploy_postgres
    create_db_secret
    create_api_secret
    deploy_cognition
    wait_for_deployment
    print_info
}

# Run main function
main "$@"
