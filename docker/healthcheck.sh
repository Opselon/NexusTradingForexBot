#!/usr/bin/env sh
set -e

python -m nexus_scalp.cli.main doctor > /dev/null 2>&1
if [ $? -eq 0 ]; then
    exit 0
else
    exit 1
fi
