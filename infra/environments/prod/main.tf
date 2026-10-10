terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }

  backend "s3" {
    bucket       = "vgc-telemetry-tfstate"
    key          = "environments/prod/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "vgc-telemetry"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  table_name  = "vgc-telemetry-${var.environment}"
  bucket_name = "vgc-telemetry-raw-${var.environment}"
}

module "dynamodb" {
  source      = "../../modules/dynamodb"
  table_name  = local.table_name
  environment = var.environment
}

module "iam" {
  source                = "../../modules/iam"
  environment           = var.environment
  table_arn             = module.dynamodb.table_arn
  bucket_arn            = module.s3.bucket_arn
  ingestion_lambda_name = "vgc-telemetry-ingestion-${var.environment}"
  aws_region            = var.aws_region
}

module "lambda_api" {
  source                  = "../../modules/lambda_api"
  environment             = var.environment
  image_uri               = "${var.ecr_repository_url}:${var.image_tag}-api"
  role_arn                = module.iam.api_role_arn
  table_name              = module.dynamodb.table_name
  reserved_concurrency    = 5
  bucket_name             = local.bucket_name
  catalog_runtime_enabled = var.catalog_runtime_enabled
}

module "lambda_ingestion" {
  source      = "../../modules/lambda_ingestion"
  environment = var.environment
  image_uri   = "${var.ecr_repository_url}:${var.image_tag}-ingestion"
  role_arn    = module.iam.ingestion_role_arn
  table_name  = module.dynamodb.table_name
  bucket_name = local.bucket_name
  bucket_arn  = module.s3.bucket_arn
}

module "lambda_scraper" {
  source                    = "../../modules/lambda_scraper"
  environment               = var.environment
  image_uri                 = "${var.ecr_repository_url}:${var.image_tag}-scraper"
  role_arn                  = module.iam.scraper_role_arn
  bucket_name               = local.bucket_name
  table_name                = module.dynamodb.table_name
  ingestion_function_name   = module.lambda_ingestion.function_name
  collection_page_size      = var.collection_page_size
  collection_batch_size     = var.collection_batch_size
  collection_max_pages      = var.collection_max_pages
  collection_interval_hours = var.discovery_hours
  recent_days               = var.recent_days
  reconcile_days            = var.reconcile_days
}

module "s3" {
  source               = "../../modules/s3"
  environment          = var.environment
  bucket_name          = local.bucket_name
  ingestion_lambda_arn = module.lambda_ingestion.function_arn
}

module "eventbridge_scheduler" {
  source                    = "../../modules/eventbridge_scheduler"
  environment               = var.environment
  scraper_lambda_arn        = module.lambda_scraper.function_arn
  scraper_lambda_name       = module.lambda_scraper.function_name
  ingestion_lambda_arn      = module.lambda_ingestion.function_arn
  ingestion_lambda_name     = module.lambda_ingestion.function_name
  source_admission_verified = var.source_admission_verified
  discovery_hours           = var.discovery_hours
  reference_hours           = var.reference_hours
  recovery_minutes          = var.recovery_minutes
  reconcile_expression      = var.reconcile_expression
}

module "budget" {
  source             = "../../modules/budget"
  environment        = var.environment
  notification_email = var.notification_email
}

module "observability" {
  source             = "../../modules/observability"
  environment        = var.environment
  notification_email = var.notification_email
}
