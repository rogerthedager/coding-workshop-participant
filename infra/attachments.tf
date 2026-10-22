# Private bucket for facility-service incident photos.
# Browsers upload with presigned POSTs and view with presigned GETs (5-minute links);
# the bucket itself is never public.
resource "aws_s3_bucket" "attachments" {
  bucket        = format("%s-attachments-%s", var.aws_project, local.app_id)
  force_destroy = true

  tags = local.app_tags
}

resource "aws_s3_bucket_public_access_block" "attachments" {
  bucket                  = aws_s3_bucket.attachments.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# The browser posts files straight to S3 from the CloudFront site, so S3 must allow that origin.
# CloudFront only exists on real AWS (not LocalStack), so neither does this rule.
resource "aws_s3_bucket_cors_configuration" "attachments" {
  count  = length(aws_cloudfront_distribution.this)
  bucket = aws_s3_bucket.attachments.id

  cors_rule {
    allowed_methods = ["POST", "GET"]
    allowed_origins = [format("https://%s", aws_cloudfront_distribution.this[count.index].domain_name)]
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}
