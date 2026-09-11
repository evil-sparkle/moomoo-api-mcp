#!/usr/bin/env bash
# Write ~/.aws/credentials for the ECR credential helper.
#
# Values come from the ecr-pull-iam-user Terraform module on your workstation:
#   terraform output -raw aws_access_key_id
#   terraform output -raw aws_secret_access_key
#
# Prompts by default so the secret never lands in argv or shell history.
# For automation, export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY instead.
set -euo pipefail

key_id="${AWS_ACCESS_KEY_ID:-}"
secret="${AWS_SECRET_ACCESS_KEY:-}"

if [ -z "$key_id" ]; then
  printf 'aws_access_key_id: ' >&2
  read -r key_id
fi
if [ -z "$secret" ]; then
  printf 'aws_secret_access_key (not echoed): ' >&2
  read -rs secret
  printf '\n' >&2
fi

# The AWS INI parser takes values literally: surrounding quotes become part of
# the value and break parsing with "Unable to parse config file".
for value in "$key_id" "$secret"; do
  case "$value" in
    '')
      echo 'Both the access key id and the secret are required.' >&2
      exit 1
      ;;
    '"'*|*'"'|"'"*|*"'")
      echo 'Do not quote the values; paste them bare.' >&2
      exit 1
      ;;
  esac
done

install -d -m 0700 "$HOME/.aws"
umask 077
printf '[default]\naws_access_key_id = %s\naws_secret_access_key = %s\n' \
  "$key_id" "$secret" > "$HOME/.aws/credentials"
chmod 0600 "$HOME/.aws/credentials"

echo "Wrote $HOME/.aws/credentials (mode 0600). Verifying..." >&2
aws sts get-caller-identity --no-cli-pager
