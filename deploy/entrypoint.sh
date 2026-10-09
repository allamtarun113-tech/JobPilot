#!/bin/sh
# Start the worker and dispatcher. The Gmail refresh token (an encrypted file on the laptop) arrives as
# GMAIL_TOKEN_ENC_B64 on a cloud host, because the container's disk starts empty.
set -e
mkdir -p var
if [ -n "$GMAIL_TOKEN_ENC_B64" ]; then
    echo "$GMAIL_TOKEN_ENC_B64" | base64 -d > var/gmail_token.enc
fi
exec .venv/bin/supervisord -n -c deploy/supervisor-cloud.conf
