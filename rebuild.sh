#!/bin/bash
# Fetches short-lived ECR credentials from Palana agent-api locally,
# injects them as GitLab CI variables, then triggers the pipeline to
# build and push the image (since Docker can't run locally on this machine).
set -e

AWS="$PWD/aws-local/Amazon/AWSCLIV2/aws.exe"
AGENT_API="https://agent-api.agent.palana.engtools.net"
KUBECONFIG_PATH="$HOME/.palana/kubeconfig"
IMAGE_NAME="gmail-classifier"
GITLAB_PROJECT="grab%2Fentities%2Fworkspaces%2Fanalytics-country-vn%2Fdispute-tracker"

# Get OAuth token from kubeconfig
TOKEN=$(kubectl --kubeconfig="$KUBECONFIG_PATH" config view --raw \
  -o jsonpath='{.users[?(@.name=="oauth-user-palana-eks-remote")].user.token}')

if [[ -z "$TOKEN" ]]; then
  echo "✗ No Palana token found — run: ./pcli login"
  exit 1
fi
echo "✓ OAuth token found"

# Get ECR credentials from Palana agent-api
echo "→ Requesting ECR credentials..."
CREDS=$(curl -sf -X POST "$AGENT_API/api/v1/ecr/credentials" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"image_name\": \"$IMAGE_NAME\"}")

ECR_REGISTRY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['registry'])")
ECR_REPOSITORY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['repository'])")
ACCESS_KEY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_key_id'])")
SECRET_KEY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['secret_access_key'])")
SESSION_TOKEN=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_token'])")
ECR_REGION=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['region'])")

echo "✓ ECR credentials received"
echo "  Image: $ECR_REGISTRY/$ECR_REPOSITORY:latest"

# Push ECR creds as GitLab CI variables
echo "→ Injecting ECR credentials into GitLab CI..."
for VAR_NAME in ECR_REGISTRY ECR_REPOSITORY ECR_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  case "$VAR_NAME" in
    ECR_REGISTRY)      VAL="$ECR_REGISTRY" ;;
    ECR_REPOSITORY)    VAL="$ECR_REPOSITORY" ;;
    ECR_REGION)        VAL="$ECR_REGION" ;;
    AWS_ACCESS_KEY_ID) VAL="$ACCESS_KEY" ;;
    AWS_SECRET_ACCESS_KEY) VAL="$SECRET_KEY" ;;
    AWS_SESSION_TOKEN) VAL="$SESSION_TOKEN" ;;
  esac
  # Update if exists, create if not
  HTTP=$(glab api --method PUT "projects/${GITLAB_PROJECT}/variables/${VAR_NAME}" \
    --field "value=${VAL}" --field "masked=true" --field "protected=false" \
    -o /dev/null -q 2>&1 && echo 200 || echo 0)
  if [[ "$HTTP" == "0" ]]; then
    glab api --method POST "projects/${GITLAB_PROJECT}/variables" \
      --field "key=${VAR_NAME}" --field "value=${VAL}" \
      --field "masked=true" --field "protected=false" > /dev/null
  fi
done
echo "✓ GitLab CI variables updated"

# Trigger pipeline
echo "→ Triggering GitLab CI pipeline..."
PIPELINE=$(glab api --method POST "projects/${GITLAB_PROJECT}/pipeline" --field "ref=master")
PIPELINE_ID=$(echo "$PIPELINE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
PIPELINE_URL=$(echo "$PIPELINE" | python3 -c "import sys,json; print(json.load(sys.stdin)['web_url'])")
echo "✓ Pipeline #${PIPELINE_ID} triggered"
echo "  $PIPELINE_URL"

# Also refresh PALANA_TOKEN while we're here
echo "→ Refreshing PALANA_TOKEN in GitLab CI..."
glab api --method PUT "projects/${GITLAB_PROJECT}/variables/PALANA_TOKEN" \
  --field "value=${TOKEN}" --field "masked=true" --field "protected=false" > /dev/null
echo "✓ PALANA_TOKEN refreshed"

echo ""
echo "The pipeline will build and push your image to Palana's ECR."
echo "Once it succeeds, run:"
echo "  KUBECONFIG=~/.palana/kubeconfig ./pcli run gmail-classifier --replace"
