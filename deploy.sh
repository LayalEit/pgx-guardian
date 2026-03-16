#!/bin/bash
# PGx-Guardian — Automated Cloud Run Deployment
# Usage: ./deploy.sh
# Requires: gcloud CLI authenticated, .env file present

set -e

PROJECT_ID="pgx-guardian"
REGION="europe-west1"
SERVICE_NAME="pgx-guardian"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "🧬 PGx-Guardian — Cloud Run Deployment"
echo "========================================"
echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "Image:    $IMAGE"
echo ""

# Load env vars
if [ ! -f .env ]; then
    echo "❌ .env file not found. Create it with GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY"
    exit 1
fi
export $(cat .env | xargs)

# Confirm required env vars
for var in GEMINI_API_KEY SUPABASE_URL SUPABASE_KEY; do
    if [ -z "${!var}" ]; then
        echo "❌ Missing required env var: $var"
        exit 1
    fi
done
echo "✅ Environment variables loaded"

# Set project
gcloud config set project $PROJECT_ID

# Build and push image
echo ""
echo "📦 Building container image..."
gcloud builds submit --tag $IMAGE .
echo "✅ Image built and pushed"

# Deploy to Cloud Run
echo ""
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
    --image $IMAGE \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY},SUPABASE_URL=${SUPABASE_URL},SUPABASE_KEY=${SUPABASE_KEY}" \
    --min-instances 1 \
    --max-instances 3 \
    --memory 1Gi \
    --cpu 1 \
    --timeout 3600 \
    --port 8080

echo ""
echo "✅ Deployment complete!"
echo ""
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format="value(status.url)")
echo "🌐 Service URL: $SERVICE_URL"
echo ""
echo "📝 Next step: update WS_URL in voice_ui.html"
echo "   Change: ws://localhost:8000"
echo "   To:     wss://${SERVICE_URL#https://}"
