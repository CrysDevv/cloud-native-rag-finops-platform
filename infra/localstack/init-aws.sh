#!/usr/bin/env bash
# Inicialización de recursos AWS en LocalStack (S3, SQS + DLQ, Secrets Manager)
# Se ejecuta automáticamente en la fase "ready" de LocalStack.
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-2}"
BUCKET="${S3_BUCKET_DOCUMENTOS:-rag-documentos-local}"
QUEUE="${SQS_QUEUE_INGESTA:-rag-ingesta-queue}"
DLQ="${SQS_QUEUE_DLQ:-rag-ingesta-dlq}"
MAX_RECEIVE="${SQS_MAX_RECEIVE_COUNT:-3}"
SECRET_DB="${SECRETS_DB_NAME:-rag/database}"
SECRET_APP="${SECRETS_APP_NAME:-rag/app}"

echo "[init-aws] Región: ${REGION}"

# --- S3 -------------------------------------------------------------------
echo "[init-aws] Creando bucket S3: ${BUCKET}"
awslocal s3api create-bucket \
  --bucket "${BUCKET}" \
  --region "${REGION}" \
  --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null 2>&1 || true

awslocal s3api put-bucket-versioning \
  --bucket "${BUCKET}" \
  --versioning-configuration Status=Enabled

awslocal s3api put-public-access-block \
  --bucket "${BUCKET}" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# --- SQS: Dead-Letter Queue ----------------------------------------------
echo "[init-aws] Creando DLQ: ${DLQ}"
awslocal sqs create-queue \
  --queue-name "${DLQ}" \
  --attributes MessageRetentionPeriod=1209600 >/dev/null

DLQ_URL="$(awslocal sqs get-queue-url --queue-name "${DLQ}" --query 'QueueUrl' --output text)"
DLQ_ARN="$(awslocal sqs get-queue-attributes \
  --queue-url "${DLQ_URL}" \
  --attribute-names QueueArn \
  --query 'Attributes.QueueArn' --output text)"

# --- SQS: cola principal con política de redrive ---------------------------
echo "[init-aws] Creando cola de ingesta: ${QUEUE}"
ATTR_FILE="$(mktemp)"
cat > "${ATTR_FILE}" <<JSON
{
  "VisibilityTimeout": "60",
  "MessageRetentionPeriod": "345600",
  "ReceiveMessageWaitTimeSeconds": "10",
  "RedrivePolicy": "{\"deadLetterTargetArn\":\"${DLQ_ARN}\",\"maxReceiveCount\":\"${MAX_RECEIVE}\"}"
}
JSON

awslocal sqs create-queue \
  --queue-name "${QUEUE}" \
  --attributes "file://${ATTR_FILE}" >/dev/null
rm -f "${ATTR_FILE}"

# --- Secrets Manager -------------------------------------------------------
echo "[init-aws] Creando secretos"
DB_SECRET_JSON="$(printf '{"host":"%s","port":"%s","dbname":"%s","username":"%s","password":"%s"}' \
  "${POSTGRES_HOST:-postgres}" \
  "${POSTGRES_PORT:-5432}" \
  "${POSTGRES_DB:-rag_finops}" \
  "${POSTGRES_USER:-rag_admin}" \
  "${POSTGRES_PASSWORD:-}")"

awslocal secretsmanager create-secret \
  --name "${SECRET_DB}" \
  --description "Credenciales de PostgreSQL (LocalStack)" \
  --secret-string "${DB_SECRET_JSON}" >/dev/null 2>&1 || \
awslocal secretsmanager put-secret-value \
  --secret-id "${SECRET_DB}" \
  --secret-string "${DB_SECRET_JSON}" >/dev/null

APP_SECRET_JSON='{"jwt_signing_key":"local-dev-only-no-usar-en-produccion","embedding_provider":"local-deterministic"}'

awslocal secretsmanager create-secret \
  --name "${SECRET_APP}" \
  --description "Configuración sensible de la aplicación (LocalStack)" \
  --secret-string "${APP_SECRET_JSON}" >/dev/null 2>&1 || \
awslocal secretsmanager put-secret-value \
  --secret-id "${SECRET_APP}" \
  --secret-string "${APP_SECRET_JSON}" >/dev/null

echo "[init-aws] Recursos listos:"
awslocal s3 ls
awslocal sqs list-queues
awslocal secretsmanager list-secrets --query 'SecretList[].Name' --output text
echo "[init-aws] Inicialización completada."
