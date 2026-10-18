provider "aws" { # pisemo da ovo treba da se desi na aws-u, pre ovog smo uradili aws login
  region = "eu-central-1"
  default_tags {
    tags = { project = "uber-lite", layer = "bootstrap", owner = "admin-luka", managed-by = "terraform" }
  }
}

data "aws_caller_identity" "me" {} # upisemo da je moj user caller_idenity tako da ne mora rucno da se zapisuje

resource "aws_s3_bucket" "tfstate" { # napravimo s3 bucket sa mojim account_id u ime, jer bucket mora da bude unique name za ceo aws na svetu

  # Bucket names are global; the account ID makes it unique without committing it anywhere.
  bucket = "uber-lite-tfstate-${data.aws_caller_identity.me.account_id}"
  lifecycle { prevent_destroy = true } # ovo sprecava da mozes obrisati resurs sa terraform destroy nego moras rucno
}

resource "aws_s3_bucket_versioning" "tfstate" { # ukljucuje versionning bucketa, tako da ako izgubimo tfstate file moze da se vrati na prthodnu verziju bucketa
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" { # ukljucujemo enkripciju buketa
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" { # blokiramo javni pristup buketa sa inteneta
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" { # da se stare verzije tfstate fajla brisu nakon 30 dana
  bucket = aws_s3_bucket.tfstate.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

output "state_bucket" { value = aws_s3_bucket.tfstate.bucket } # print created bucket name
