#!/usr/bin/env bash
# Usage: deploy.sh [--prepare] [commit]
set -euo pipefail
ORIGINAL_ARGS=("$@")

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO_ROOT"
prepare=false
if [ "${1:-}" = "--prepare" ]; then
  prepare=true
  shift
fi
if [ "$#" -gt 1 ]; then
  echo 'Usage: scripts/deploy.sh [--prepare] [commit]' >&2
  exit 1
fi
registry="${ECR_REGISTRY:-}"
if [ -z "$registry" ] && [ -f .deploy.env ]; then
  while IFS= read -r line; do
    if [ "${line%%=*}" = ECR_REGISTRY ]; then
      registry="${line#*=}"
    fi
  done < .deploy.env
fi
if [ -z "$registry" ]; then
  echo 'Set ECR_REGISTRY to the Terraform ecr_registry output for the first deploy.' >&2
  exit 1
fi
IFS=. read -r account dkr ecr region domain suffix extra <<< "$registry"
if [ "$dkr.$ecr.$domain.$suffix" != 'dkr.ecr.amazonaws.com' ] || [ -n "${extra:-}" ] || [ -z "$region" ]; then
  echo 'ECR_REGISTRY must be a private ECR registry hostname.' >&2
  exit 1
fi
# Resolve one variable from an env file the way Docker Compose does, so the
# verification probe authenticates with the same bytes the container was
# started with. compose-prod.sh passes --env-file .env to Compose, and a naive
# "everything after the first =" is not that: MCP_AUTH_TOKEN="abc" starts the
# container with abc but would send "abc" — quotes included — as a bearer
# token, 401 the healthy deploy and roll it back.
#
# What is implemented is a subset of Compose's dotenv grammar: CRLF, comment
# lines, surrounding whitespace, an optional export prefix, the '=' and ':'
# delimiters, single and double quotes on one line, and " #" inline comments
# on unquoted values. Everything in that subset resolves to the same bytes
# Compose resolves.
#
# What is deliberately NOT implemented is refused, never guessed at, because
# guessing wrong sends bytes the container never saw:
#   - escape sequences and literal backslashes in quoted values (Compose
#     translates \n, \", \\; refusing beats mistranslating)
#   - quoted values spanning lines
#   - variable references, which Compose expands in bare and double-quoted
#     values, braced and unbraced alike. Refusing is not a limitation the
#     operator cannot escape: exporting MCP_AUTH_TOKEN in the environment
#     wins in both tools.
# Single-quoted values are otherwise sent as written, which matches Compose
# treating them literally.
parse_env_token() {
  local file="$1" name="$2" raw line key value dq='"' sq="'" tab
  tab="$(printf '\t')"
  [ -f "$file" ] || return 0
  while IFS= read -r raw || [ -n "$raw" ]; do
    line="${raw%$'\r'}"
    # Compose trims whitespace around the key and off the value.
    while :; do
      case "$line" in
        ' '*) line="${line# }" ;;
        "$tab"*) line="${line#"$tab"}" ;;
        *) break ;;
      esac
    done
    case "$line" in
      ''|'#'*) continue ;;
    esac
    # Compose accepts an `export` prefix on assignments, for files that can
    # also be sourced by a shell.
    case "$line" in
      'export '*|'export'"$tab"*) line="${line#export}" ;;
      *) ;;
    esac
    while :; do
      case "$line" in
        ' '*) line="${line# }" ;;
        "$tab"*) line="${line#"$tab"}" ;;
        *) break ;;
      esac
    done
    key="${line%%=*}"
    value="${line#*=}"
    if [ "$key" = "$line" ]; then
      # No '='. Docker's env-file documentation also names ':' as a
      # delimiter, and a silently skipped MCP_AUTH_TOKEN is the worst
      # outcome here: the container would start authenticated while the
      # probe sent no token. So honor the colon form too; if this Compose
      # rejects colon lines outright, compose fails the deploy first, at
      # `up`, and the resolution below never runs.
      key="${line%%:*}"
      value="${line#*:}"
      if [ "$key" = "$line" ]; then
        continue  # no delimiter on the line: not an assignment
      fi
    fi
    while :; do
      case "$key" in
        ' '*) key="${key# }" ;;
        "$tab"*) key="${key#"$tab"}" ;;
        *" ") key="${key% }" ;;
        *"$tab") key="${key%$tab}" ;;
        *) break ;;
      esac
    done
    if [ "$key" != "$name" ]; then
      continue
    fi
    while :; do
      case "$value" in
        ' '*) value="${value# }" ;;
        "$tab"*) value="${value#"$tab"}" ;;
        *) break ;;
      esac
    done
    case "$value" in
      # Single-quoted: Compose takes the content literally — no interpolation
      # — so the content up to the closing quote is final. Docker's docs have
      # also shown escaped quotes working inside single quotes, which this
      # first-quote stop cannot honor; a backslash therefore refuses rather
      # than risks parsing a different value than the container received.
      "${sq}"*)
        case "$value" in
          *'\'*)
            echo "deploy.sh does not resolve escape sequences in single-quoted $name=... (from $file). Set $name without backslashes." >&2
            exit 1
            ;;
        esac
        value="${value#"$sq"}"
        case "$value" in
          *"${sq}"*) value="${value%%"$sq"*}" ;;
          *)
            echo "deploy.sh cannot resolve a quoted value spanning lines ($name=... from $file). Set $name on one line." >&2
            exit 1
            ;;
        esac
        ;;
      # Double-quoted: Compose would process backslash escapes and expand
      # variable references; neither is reimplemented, so a value needing
      # either is refused rather than sent as different bytes.
      "${dq}"*)
        case "$value" in
          *'\'*)
            echo "deploy.sh cannot resolve the escape sequences in $name=... (from $file). Set $name as a literal value without backslashes." >&2
            exit 1
            ;;
        esac
        value="${value#"$dq"}"
        case "$value" in
          *"$dq"*) value="${value%%"$dq"*}" ;;
          *)
            echo "deploy.sh cannot resolve a quoted value spanning lines ($name=... from $file). Set $name on one line." >&2
            exit 1
            ;;
        esac
        case "$value" in
          *'$'*)
            echo "deploy.sh cannot resolve the variable reference in $name=... (from $file). Set a literal value, or export $name in the environment." >&2
            exit 1
            ;;
        esac
        ;;
      *)
        # Unquoted: whitespace followed by # begins an inline comment.
        case "$value" in
          *' #'*) value="${value%%' #'*}" ;;
        esac
        while :; do
          case "$value" in
            *" ") value="${value% }" ;;
            *"$tab") value="${value%$tab}" ;;
            *) break ;;
          esac
        done
        case "$value" in
          # Unquoted values are still interpolated by Compose — $VAR and
          # ${VAR} alike; refusing any $ is the only honest answer without
          # reimplementing that too.
          *'$'*)
            echo "deploy.sh cannot resolve the variable reference in $name=... (from $file). Set a literal value, or export $name in the environment." >&2
            exit 1
            ;;
        esac
        ;;
    esac
    printf '%s' "$value"
    return 0
  done < "$file"
}

# The token the server is started with, so the verification probe can
# authenticate the way a real client does. Only ever sent as a header, never
# echoed. Resolution order matches Compose: the environment first, then the
# env files compose-prod.sh passes, later file winning.
mcp_auth_token="${MCP_AUTH_TOKEN:-}"
if [ -z "$mcp_auth_token" ]; then
  for env_file in .env .deploy.env; do
    resolved_token="$(parse_env_token "$env_file" MCP_AUTH_TOKEN)"
    if [ -n "$resolved_token" ]; then
      mcp_auth_token="$resolved_token"
    fi
  done
fi
tools=(git aws docker)
if [ "$prepare" = false ]; then
  tools+=(curl)
  case "${DEPLOY_VERIFY_TIMEOUT:-90}" in
    ""|*[!0-9]*)
      echo "Error: DEPLOY_VERIFY_TIMEOUT must be a numeric value." >&2
      exit 1
      ;;
  esac
fi
for tool in "${tools[@]}"; do
  command -v "$tool" >/dev/null || { echo "Missing required command: $tool" >&2; exit 1; }
done
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo 'Commit or stash tracked working-tree changes before deploying.' >&2
  exit 1
fi
git fetch --quiet origin main
commit="$(git rev-parse --verify --end-of-options "${1:-origin/main}^{commit}")"
short="${commit:0:7}"

if [ "${DEPLOY_REEXEC:-0}" != "1" ]; then
  if git cat-file -e "${commit}:scripts/deploy.sh" 2>/dev/null; then
    if ! git diff --quiet "${commit}" -- scripts/deploy.sh 2>/dev/null; then
      echo "scripts/deploy.sh has changed in ${short}; re-executing latest deploy script..." >&2
      reexec_file="$(mktemp "${TMPDIR:-/tmp}/deploy.XXXXXX")"
      trap 'rm -f "${reexec_file}"' EXIT INT TERM
      git show "${commit}:scripts/deploy.sh" > "${reexec_file}"
      chmod +x "${reexec_file}"
      export DEPLOY_REEXEC=1
      export REPO_ROOT
      if [ "${#ORIGINAL_ARGS[@]}" -eq 0 ]; then
        "${reexec_file}"
      else
        "${reexec_file}" "${ORIGINAL_ARGS[@]}"
      fi
      exit $?
    fi
  fi
fi

# CI tags every main build with its short commit, so the image matches the source
# about to be checked out; git v* tags are bookmarks and are deliberately ignored here.
ecr_has_tag() {
  local image="$1" tag="$2"
  local got
  got="$(aws ecr batch-get-image --region "$region" --registry-id "$account" \
    --repository-name "$image" --image-ids "imageTag=$tag" \
    --query 'images[0].imageId.imageDigest' --output text)"
  [ -n "$got" ] && [ "$got" != None ]
}

# What a successful initialize must look like: a JSON-RPC success carrying the
# result fields the MCP spec requires. Checked by substring, deliberately —
# the reply may be JSON or SSE-framed and both contain the same markers,
# while a JSON-RPC error carries "error" and a non-MCP endpoint answering 200
# carries neither "result" nor "serverInfo". A bare HTTP 200 proves nothing:
# smoke-test.sh once caught a server answering 200 with an empty tool list.
initialize_result_ok() {
  local body="$1"
  case "$body" in
    *'"error"'*) return 1 ;;
  esac
  case "$body" in
    *'"result"'*) ;;
    *) return 1 ;;
  esac
  case "$body" in
    *'"protocolVersion"'*) ;;
    *) return 1 ;;
  esac
  case "$body" in
    *'"serverInfo"'*) return 0 ;;
    *) return 1 ;;
  esac
}

# One image now carries both the gateway and the server, so there is one tag to
# confirm rather than two that had to agree.
if ecr_has_tag moomoo-api-mcp "${short}"; then
  image_tag="${short}"
else
  # Not necessarily "no such tag": a denied or failed probe lands here too, and
  # its AWS error is printed above. Saying "missing" would hide that.
  echo "Aborting deploy of ${short}: could not confirm moomoo-api-mcp:${short} (missing, or the AWS check failed — see any error above). Check that CI's main push landed." >&2
  exit 1
fi
echo "Deploying ${short} as ${image_tag}" >&2

# Read the whole function before checkout can replace this script on disk.
finish_deploy() {
  local previous_commit=""
  local previous_env=""
  local has_previous_env=false

  rollback() {
    local started_services="${1:-true}"
    if [ "$has_previous_env" = true ]; then
      printf '%s\n' "$previous_env" > .deploy.env
      git checkout --quiet --detach "$previous_commit"
      local prev_short="${previous_commit:0:7}"
      if [ "$started_services" = true ]; then
        if ! ./scripts/compose-prod.sh up -d --remove-orphans; then
          echo "Rolled back .deploy.env and the checkout to ${prev_short} but restarting the previous images FAILED. Stack needs manual attention: scripts/compose-prod.sh up -d" >&2
          exit 1
        fi
      fi
      echo "Rolled back to ${prev_short}" >&2
    else
      echo "No previous deploy state to roll back to." >&2
    fi
    exit 1
  }

  if [ "$prepare" = false ]; then
    previous_commit="$(git rev-parse HEAD)"
    if [ -f .deploy.env ]; then
      previous_env="$(cat .deploy.env)"
      has_previous_env=true
    fi
  fi

  git checkout --quiet --detach "$commit"
  if [ ! -f docker-compose.prod.yml ] || [ ! -x scripts/compose-prod.sh ]; then
    echo 'Target commit lacks production deployment files; choose a newer commit.' >&2
    return 1
  fi
  printf 'ECR_REGISTRY=%s\nIMAGE_TAG=%s\n' "$registry" "${image_tag}" > .deploy.env

  if [ "$prepare" = false ]; then
    ./scripts/compose-prod.sh pull || rollback false
    # --remove-orphans clears containers left behind by manual troubleshooting,
    # which otherwise fail the start with "container name is already in use".
    ./scripts/compose-prod.sh up -d --remove-orphans || rollback true
    local timeout="${DEPLOY_VERIFY_TIMEOUT:-90}"
    local verify_url="${DEPLOY_VERIFY_URL:-http://127.0.0.1:8000/mcp}"
    local elapsed=0
    local verified=false
    # A plain GET proves only that something is listening, and with bearer auth
    # on it answers 401 — which is exactly the line an operator then finds at
    # the bottom of a *successful* deploy's logs. Probe as a real client
    # instead: the same authenticated initialize smoke-test.sh sends. The
    # access log then shows POST /mcp 200 for a healthy deploy, and a refused
    # probe stops the deploy with a diagnosis instead of decorating a success.
    # A 200 alone is still not verification: the reply must be an MCP
    # initialize result, which is what initialize_result_ok demands.
    local -a probe_args=(
      -s --max-time 10 -w '\n%{http_code}'
      -H 'Content-Type: application/json'
      -H 'Accept: application/json, text/event-stream'
    )
    local header_file=""
    if [ -n "$mcp_auth_token" ]; then
      # The bearer token never goes in curl's argv, where ps-style inspection
      # would read it: curl takes the header from a 0600 file instead.
      header_file="$(mktemp "${TMPDIR:-/tmp}/deploy-verify.XXXXXX")"
      chmod 600 "$header_file"
      printf 'Authorization: Bearer %s\n' "$mcp_auth_token" > "$header_file"
      probe_args+=(-H "@${header_file}")
      # The EXIT trap may fire after finish_deploy's scope is gone (normal
      # return) or from inside it (rollback's exit), hence the :- default.
      trap 'rm -f "${header_file:-}"' EXIT
    fi
    local initialize_request='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"deploy-verify","version":"0"}}}'
    # Note: Verification proves the MCP endpoint completes an initialize, not
    # that OpenD is logged in to the broker.
    while :; do
      local response body code
      # curl prints the body, then the status on its own line (-w).
      response="$(curl "${probe_args[@]}" -d "$initialize_request" "$verify_url" 2>/dev/null || true)"
      [ -z "$response" ] && response="000"
      code="${response##*$'\n'}"
      body="${response%$'\n'*}"
      [ -z "$code" ] && code="000"
      case "$code" in
        # Not listening yet, or a server error it may grow out of: keep polling.
        000 | 5?? ) ;;
        # The endpoint completed an MCP initialize handshake.
        200 )
          if initialize_result_ok "$body"; then
            verified=true
          fi
          break
          ;;
        # Listening but refusing the probe: a wrong MCP_AUTH_TOKEN, or a
        # verify_url that does not speak MCP. Polling longer cannot fix either.
        * ) break ;;
      esac
      if [ "$timeout" = "0" ] || [ "$elapsed" -ge "$timeout" ]; then
        break
      fi
      sleep 3
      elapsed=$((elapsed + 3))
    done
    # Delete the secret while header_file is still in scope. The EXIT trap
    # cannot do this on the success path: it fires after finish_deploy has
    # returned, when this local is out of scope and the expansion is empty —
    # the file would survive the deploy holding the bearer token. The trap
    # stays for abnormal exits (rollback's exit), which happen from inside
    # this function while the variable is still set.
    if [ -n "$header_file" ]; then
      rm -f "$header_file"
    fi
    if [ "$verified" = true ]; then
      echo "Deploy verified: ${short} as ${image_tag}" >&2
      ./scripts/compose-prod.sh logs --tail=200 moomoo-mcp
    else
      echo "Deploy verification failed (last HTTP status from ${verify_url}: ${code})." >&2
      case "$code" in
        401 | 403)
          echo "The server is up but rejected the deploy probe: MCP_AUTH_TOKEN in .env does not match what the server was started with. Clients using that token are refused the same way." >&2
          ;;
        200)
          echo "The endpoint answered 200 but not with an MCP initialize result; DEPLOY_VERIFY_URL may not point at the MCP endpoint." >&2
          ;;
      esac
      ./scripts/compose-prod.sh logs --tail=200 moomoo-mcp
      rollback true
    fi
  else
    ./scripts/compose-prod.sh pull
  fi
}
finish_deploy
