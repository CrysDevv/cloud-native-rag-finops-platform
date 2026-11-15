variable "aws_region" {
  description = "Región AWS (Ohio, capa gratuita)"
  type        = string
  default     = "us-east-2"
}

variable "project_name" {
  description = "Nombre del proyecto usado como prefijo de recursos"
  type        = string
  default     = "cloud-native-rag-finops"
}

variable "environment" {
  description = "Entorno de despliegue"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment debe ser dev, staging o prod."
  }
}

variable "use_localstack" {
  description = "Si es true, apunta el proveedor a LocalStack (costo $0) y omite recursos no soportados (Budgets)"
  type        = bool
  default     = true
}

variable "localstack_endpoint" {
  description = "Endpoint de LocalStack"
  type        = string
  default     = "http://localhost:4566"
}

variable "budget_limit_usd" {
  description = "Límite mensual FinOps en USD"
  type        = number
  default     = 1

  validation {
    condition     = var.budget_limit_usd >= 0
    error_message = "budget_limit_usd no puede ser negativo."
  }
}

variable "budget_alert_threshold_pct" {
  description = "Porcentaje del presupuesto que dispara la alerta"
  type        = number
  default     = 80
}

variable "budget_alert_emails" {
  description = "Correos que reciben alertas del presupuesto"
  type        = list(string)
  default     = ["finops@example.com"]
}

variable "sqs_max_receive_count" {
  description = "Intentos antes de enviar el mensaje a la DLQ"
  type        = number
  default     = 3
}

variable "documents_expiration_days" {
  description = "Días tras los cuales expiran versiones no vigentes en S3 (control de costo)"
  type        = number
  default     = 30
}

variable "db_password" {
  description = "Contraseña de PostgreSQL almacenada en Secrets Manager"
  type        = string
  sensitive   = true
}

variable "db_username" {
  description = "Usuario de PostgreSQL"
  type        = string
  default     = "rag_admin"
}

variable "tags" {
  description = "Etiquetas adicionales"
  type        = map(string)
  default     = {}
}
