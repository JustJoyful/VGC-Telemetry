resource "aws_dynamodb_table" "main" {
  name         = var.table_name
  billing_mode = "PROVISIONED"

  # Explicit Capacity Budget: Regional Always-Free Pool is <= 25 WCU / <= 25 RCU
  # Base Table: 18 WCU / 15 RCU
  # GSI1: 5 WCU / 8 RCU
  # GSI2: 2 WCU / 2 RCU
  # Total: exactly 25 WCU / 25 RCU
  read_capacity  = 15
  write_capacity = 18

  hash_key  = "PK"
  range_key = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  attribute {
    name = "GSI2PK"
    type = "S"
  }

  attribute {
    name = "GSI2SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
    read_capacity   = 8
    write_capacity  = 5
  }

  global_secondary_index {
    name            = "GSI2"
    hash_key        = "GSI2PK"
    range_key       = "GSI2SK"
    projection_type = "ALL"
    read_capacity   = 2
    write_capacity  = 2
  }

  ttl {
    attribute_name = "expiry"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Environment = var.environment
    Project     = "vgc-telemetry"
    ManagedBy   = "terraform"
  }
}
