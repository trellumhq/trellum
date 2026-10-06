#!/bin/sh
# git calls GIT_ASKPASS with a human prompt as $1 ("Username for ..." or
# "Password for ..."). Answer from the environment the sync sets up, so the
# token never lands in a URL, .git/config, or the process argv.
case "$1" in
  *sername*) printf '%s' "${TRELLUM_GIT_ASKPASS_USER:-x-token-auth}" ;;
  *)         printf '%s' "${TRELLUM_GIT_ASKPASS_TOKEN:-}" ;;
esac
