#!/bin/bash
set -euo pipefail

cd /app 2>/dev/null || cd /testbed 2>/dev/null
git apply --verbose "$(dirname "$0")/oracle.patch"
