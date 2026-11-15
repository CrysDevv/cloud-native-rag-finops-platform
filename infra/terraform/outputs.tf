output "s3_bucket_documentos" {
  description = "Nombre del bucket de documentos"
  value       = aws_s3_bucket.documentos.bucket
}

output "s3_bucket_arn" {
  description = "ARN del bucket de documentos"
  value       = aws_s3_bucket.documentos.arn
}

output "sqs_ingesta_url" {
  description = "URL de la cola de ingesta"
  value       = aws_sqs_queue.ingesta.id
}

output "sqs_ingesta_arn" {
  description = "ARN de la cola de ingesta"
  value       = aws_sqs_queue.ingesta.arn
}

output "sqs_dlq_url" {
  description = "URL de la Dead-Letter Queue"
  value       = aws_sqs_queue.ingesta_dlq.id
}

output "sqs_dlq_arn" {
  description = "ARN de la Dead-Letter Queue"
  value       = aws_sqs_queue.ingesta_dlq.arn
}

output "secreto_database_arn" {
  description = "ARN del secreto de base de datos"
  value       = aws_secretsmanager_secret.database.arn
}

output "presupuesto_finops_nombre" {
  description = "Nombre del presupuesto FinOps (null en LocalStack)"
  value       = try(aws_budgets_budget.finops_mensual[0].name, null)
}

output "region" {
  description = "Región AWS utilizada"
  value       = var.aws_region
}
