#!/bin/bash
# Publishes site/ to the Cloudflare Pages project "shanjie" (https://shanjie.nyanako.com).
# The API token (Cloudflare Pages: Edit, DNS: Edit on nyanako.com) is read at run time; where from is set by CF_KEYCHAIN_ITEM
# and CF_KEYCHAIN in the environment, or by an untracked .deploy.env in the repo root that sets them. The token reaches
# wrangler through the environment of this one command and is never printed or written.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .deploy.env ] && . ./.deploy.env
: "${CF_KEYCHAIN_ITEM:?set CF_KEYCHAIN_ITEM (the Keychain item holding the Cloudflare token), or put it in .deploy.env}"
: "${CF_KEYCHAIN:?set CF_KEYCHAIN (the path of the keychain that holds the item), or put it in .deploy.env}"
token="$(security find-generic-password -s "$CF_KEYCHAIN_ITEM" -w "$CF_KEYCHAIN")"
CLOUDFLARE_ACCOUNT_ID=2ce88ede6a612df9fd22b10c9b0a1348 CLOUDFLARE_API_TOKEN="$token" npx --yes wrangler@4 pages deploy site --project-name shanjie --branch main --commit-dirty=true
