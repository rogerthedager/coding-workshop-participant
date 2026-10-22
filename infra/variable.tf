variable "aws_project" {
  description = "The AWS project name."
  type        = string
  default     = "coding-workshop"
}

variable "aws_bucket" {
  description = "The AWS S3 bucket name for terraform state storage."
  type        = string
  default     = "coding-workshop-us-east-1-abcd1234"
}

variable "aws_app_code" {
  description = "The AWS application unique code."
  type        = string
  default     = "abcd1234"
}

variable "aws_ds_ip" {
  description = "The AWS Directory Service ip address."
  type        = string
  default     = ""
}

variable "aws_vpc_id" {
  description = "The AWS VPC identifier."
  type        = string
  default     = null
}

variable "aws_postgres_enabled" {
  description = "Enable or disable PostgreSQL (AWS Aurora). Default: true (set to 'false' to disable it)."
  type        = bool
  default     = true

  validation {
    condition     = contains([true, false], var.aws_postgres_enabled)
    error_message = "The aws_postgres_enabled variable must be either 'true' or 'false'."
  }
}

variable "aws_postgres_host" {
  description = "PostgreSQL host for LocalStack. Default: 'host.docker.internal' (set to '172.17.0.1' on Linux)."
  type        = string
  default     = null
}

variable "aws_mongo_enabled" {
  description = "Enable or disable MongoDB (AWS DocumentDB). Default: false (set to 'true' to enable it)."
  type        = bool
  default     = false

  validation {
    condition     = contains([true, false], var.aws_mongo_enabled)
    error_message = "The aws_mongo_enabled variable must be either 'true' or 'false'."
  }
}

variable "aws_mongo_host" {
  description = "MongoDB host for LocalStack. Default: 'host.docker.internal' (set to '172.17.0.1' on Linux)."
  type        = string
  default     = null
}

variable "aws_eks_enabled" {
  description = "Enable or disable Jupyter Notebook (AWS EKS). Default: false (set to 'true' to enable it)."
  type        = bool
  default     = false

  validation {
    condition     = contains([true, false], var.aws_eks_enabled)
    error_message = "The aws_eks_enabled variable must be either 'true' or 'false'."
  }
}

variable "aws_eks_type" {
  description = "EKS nodes type. Default: SPOT."
  type        = string
  default     = "SPOT"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.aws_eks_type)
    error_message = "The aws_eks_type variable must be either 'ON_DEMAND' or 'SPOT'."
  }
}

variable "facility_allowed_origins" {
  description = "JSON array of exact HTTPS browser origins for facility-service, e.g. [\"https://xxxx.cloudfront.net\"]. Default: empty (not set)."
  type        = string
  default     = ""
}

variable "aws_lambda_memory_mb" {
  description = "Memory for backend Lambda functions in MB. Lambda CPU scales with memory, so this also sets request speed (bcrypt logins, cold starts). Default: 1024."
  type        = number
  default     = 1024

  validation {
    condition     = var.aws_lambda_memory_mb >= 128 && var.aws_lambda_memory_mb <= 10240
    error_message = "The aws_lambda_memory_mb variable must be between 128 and 10240."
  }
}
