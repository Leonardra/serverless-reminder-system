terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive" }
  }
  backend "s3" {
    bucket       = "serverless-reminder-system"
    key          = "reminders/terraform.tfstate"
    region       = "eu-north-1"
    use_lockfile = true
  }
}

provider "aws" { region = var.region }

variable "region" { default = "eu-north-1" }
variable "site_bucket" {}
variable "alert_email" {}

# ---------- Storage ----------
resource "aws_dynamodb_table" "reminders" {
  name         = "reminders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
}

# ---------- Notifications ----------
resource "aws_sns_topic" "reminders" { name = "reminders" }

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.reminders.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------- IAM role for the Lambdas ----------
resource "aws_iam_role" "lambda" {
  name = "reminders-lambda" # must start with reminders- (matches the pipeline's policy)
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "app" {
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:Scan", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.reminders.arn
      },
      {
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.reminders.arn
      }
    ]
  })
}


data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/placeholder.zip"
  source {
    content  = "def handler(e, c): return {'statusCode': 200}"
    filename = "app.py"
  }
}

locals {
  env = {
    TABLE     = aws_dynamodb_table.reminders.name
    TOPIC_ARN = aws_sns_topic.reminders.arn
  }
}

resource "aws_lambda_function" "http" {
  function_name = "reminders-http"
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  handler       = "app.handler"
  filename      = data.archive_file.placeholder.output_path
  environment { variables = local.env }
  lifecycle { ignore_changes = [filename, source_code_hash] }
}

resource "aws_lambda_function" "timer" {
  function_name = "reminders-timer"
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  handler       = "app.handler"
  timeout       = 30
  filename      = data.archive_file.placeholder.output_path
  environment { variables = local.env }
  lifecycle { ignore_changes = [filename, source_code_hash] }
}

resource "aws_lambda_function_url" "http" {
  function_name      = aws_lambda_function.http.function_name
  authorization_type = "NONE"
  cors {
    allow_origins = ["http://${aws_s3_bucket_website_configuration.site.website_endpoint}"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
  }
}

resource "aws_lambda_permission" "url" {
  statement_id           = "public-url"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.http.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# ---------- Timer trigger ----------
resource "aws_cloudwatch_event_rule" "every_5_min" {
  name                = "reminders-timer"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "timer" {
  rule = aws_cloudwatch_event_rule.every_5_min.name
  arn  = aws_lambda_function.timer.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "allow-eventbridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.timer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.every_5_min.arn
}

# ---------- Static web page ----------
resource "aws_s3_bucket" "site" { bucket = var.site_bucket }

resource "aws_s3_bucket_website_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  index_document { suffix = "index.html" }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "site" {
  bucket     = aws_s3_bucket.site.id
  depends_on = [aws_s3_bucket_public_access_block.site]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
    }]
  })
}

output "function_url" { value = aws_lambda_function_url.http.function_url }
output "website_url" { value = "http://${aws_s3_bucket_website_configuration.site.website_endpoint}" }