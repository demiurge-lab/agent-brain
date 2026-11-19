#!/bin/bash
set -eu
cd "${VAULT_REPO:-$PWD}"
exec brain doctor check --trigger "${DOCTOR_TRIGGER:-night}"
