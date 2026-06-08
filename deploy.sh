#!/usr/bin/env bash
# deploy.sh — Build and deploy the server to GCP Cloud Run
# Usage: ./deploy.sh

set -euo pipefail

# ---- Configuration — edit these ----
PROJECT_ID="your-gcp-project-id"
REGION="us-central1"
SERVICE_NAME="file-chunk-server"
GCS_BUCKET_NAME="your-bucket-name"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
# ------------------------------------

echo "==> Authenticating with GCP..."
gcloud config set project "$PROJECT_ID"

echo "==> Building container image..."
gcloud builds submit --tag "$IMAGE" .

echo "==> Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --platform managed \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "GCS_BUCKET_NAME=${GCS_BUCKET_NAME}" \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --concurrency 10

echo ""
echo "==> Deployment complete!"
echo "Service URL:"
gcloud run services describe "$SERVICE_NAME" \
  --platform managed \
  --region "$REGION" \
  --format "value(status.url)"
