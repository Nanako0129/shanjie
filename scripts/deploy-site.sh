#!/bin/bash
# Publishes site/ to the Cloudflare Pages project "shanjie" (https://shanjie.nyanako.com).
# The API token (Cloudflare Pages: Edit, DNS: Edit on nyanako.com) lives only in the login Keychain, item
# "cloudflare-pages"; it reaches wrangler through the environment of this one command and is never printed or written.
set -euo pipefail
cd "$(dirname "$0")/.."
token="$(security find-generic-password -s cloudflare-pages -w ~/Library/Keychains/login.keychain-db)"
CLOUDFLARE_ACCOUNT_ID=2ce88ede6a612df9fd22b10c9b0a1348 CLOUDFLARE_API_TOKEN="$token" npx --yes wrangler@4 pages deploy site --project-name shanjie --branch main --commit-dirty=true
