#!/bin/sh
set -eu
/app/catalogctl batch /app/import.json
/app/catalogctl checkpoint
