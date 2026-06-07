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

ECR_REGISTRY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['registry'])" | tr -d '\r\n')
ECR_REPOSITORY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['repository'])" | tr -d '\r\n')
ACCESS_KEY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_key_id'])" | tr -d '\r\n')
SECRET_KEY=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['secret_access_key'])" | tr -d '\r\n')
SESSION_TOKEN=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_token'])" | tr -d '\r\n')
ECR_REGION=$(echo "$CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['region'])" | tr -d '\r\n')

echo "✓ ECR credentials received"
echo "  Image: $ECR_REGISTRY/$ECR_REPOSITORY:latest"

# Pre-compute the ECR Docker auth token locally
echo "→ Computing Docker auth token..."
ECR_PASSWORD=$(AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
  AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
  AWS_SESSION_TOKEN="$SESSION_TOKEN" \
  "$AWS" ecr get-login-password --region "$ECR_REGION")
DOCKER_AUTH=$(printf 'AWS:%s' "$ECR_PASSWORD" | base64 | tr -d '\n')
DOCKER_AUTH_CONFIG=$(printf '{"auths":{"%s":{"auth":"%s"}}}' "$ECR_REGISTRY" "$DOCKER_AUTH")
echo "✓ Docker auth token ready"

# Push ECR creds as GitLab CI variables (delete + recreate to avoid PUT/POST ambiguity)
echo "→ Injecting ECR credentials into GitLab CI..."
declare -A VARS=(
  [ECR_REGISTRY]="$ECR_REGISTRY"
  [ECR_REPOSITORY]="$ECR_REPOSITORY"
  [ECR_REGION]="$ECR_REGION"
  [AWS_ACCESS_KEY_ID]="$ACCESS_KEY"
  [AWS_SECRET_ACCESS_KEY]="$SECRET_KEY"
  [AWS_SESSION_TOKEN]="$SESSION_TOKEN"
)
DOCKER_AUTH_CONFIG_B64=$(printf '%s' "$DOCKER_AUTH_CONFIG" | base64 | tr -d '\n')
for VAR_NAME in "${!VARS[@]}"; do
  VAL="${VARS[$VAR_NAME]}"
  # Delete if exists (ignore errors), then create fresh
  glab api --method DELETE "projects/${GITLAB_PROJECT}/variables/${VAR_NAME}" > /dev/null 2>&1 || true
  glab api --method POST "projects/${GITLAB_PROJECT}/variables" \
    --field "key=${VAR_NAME}" \
    --field "value=${VAL}" \
    --field "masked=true" \
    --field "protected=false" > /dev/null
  echo "  ✓ $VAR_NAME"
done
# DOCKER_AUTH_CONFIG can't be masked (contains special chars) — store base64-encoded
glab api --method DELETE "projects/${GITLAB_PROJECT}/variables/DOCKER_AUTH_CONFIG_B64" > /dev/null 2>&1 || true
glab api --method POST "projects/${GITLAB_PROJECT}/variables" \
  --field "key=DOCKER_AUTH_CONFIG_B64" \
  --field "value=${DOCKER_AUTH_CONFIG_B64}" \
  --field "masked=false" \
  --field "protected=false" > /dev/null
echo "  ✓ DOCKER_AUTH_CONFIG_B64"

# Bake secrets.toml into the image via CI variable
SECRETS_TOML="${CI_PROJECT_DIR:-.}/.streamlit/secrets.toml"
if [[ ! -f "$SECRETS_TOML" ]]; then
  SECRETS_TOML="$(dirname "$0")/.streamlit/secrets.toml"
fi
if [[ ! -f "$SECRETS_TOML" ]]; then
  echo "✗ .streamlit/secrets.toml not found — cannot inject STREAMLIT_SECRETS_B64"
  exit 1
fi
STREAMLIT_SECRETS_B64=$(base64 < "$SECRETS_TOML" | tr -d '\n')
glab api --method DELETE "projects/${GITLAB_PROJECT}/variables/STREAMLIT_SECRETS_B64" > /dev/null 2>&1 || true
GITLAB_HOST="https://gitlab.myteksi.net"
GLAB_CONFIG="$APPDATA/Local/glab-cli/config.yml"
if [[ ! -f "$GLAB_CONFIG" ]]; then
  GLAB_CONFIG="$HOME/AppData/Local/glab-cli/config.yml"
fi
GITLAB_TOKEN=$(grep -A1 'gitlab.myteksi.net:' "$GLAB_CONFIG" | grep 'token:' | awk '{print $2}' | tr -d '\r\n')
if [[ -z "$GITLAB_TOKEN" ]]; then
  echo "✗ Could not retrieve GitLab token from $GLAB_CONFIG — run: glab auth login"
  exit 1
fi
TMPFILE=$(mktemp)
python3 -c "
import json, sys
payload = {'key': 'STREAMLIT_SECRETS_B64', 'value': sys.argv[1], 'masked': False, 'protected': False}
print(json.dumps(payload))
" "$STREAMLIT_SECRETS_B64" > "$TMPFILE"
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${GITLAB_HOST}/api/v4/projects/${GITLAB_PROJECT}/variables" \
  -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  -H "Content-Type: application/json" \
  --data @"$TMPFILE")
rm -f "$TMPFILE"
if [[ "$HTTP_STATUS" != "201" && "$HTTP_STATUS" != "200" ]]; then
  echo "✗ Failed to set STREAMLIT_SECRETS_B64 (HTTP $HTTP_STATUS)"
  exit 1
fi
echo "  ✓ STREAMLIT_SECRETS_B64"
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
