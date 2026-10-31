#!/usr/bin/env bash
# =============================================================================
# setup_local_db.sh — Create the local Hover AI development database
#
# Usage:
#   bash scripts/setup_local_db.sh
#
# What it does:
#   1. Asks for your sudo password ONCE at the start
#   2. Creates a PostgreSQL user called 'hoverai'
#   3. Creates a database called 'hoverai_dev' owned by that user
#   4. Runs `alembic upgrade head` to create all tables
#   5. Prints the DATABASE_URL to paste into your .env file
#
# Requirements:
#   - PostgreSQL must be installed and running
#     Ubuntu/Debian: sudo apt install postgresql && sudo service postgresql start
#   - You must be in the sudo group (you are — run: id | grep sudo)
# =============================================================================

set -euo pipefail

# ── Config — change these if you want different credentials ──────────────────
DB_USER="hoverai"
DB_PASS="hoverai_dev_password"
DB_NAME="hoverai_dev"
DB_HOST="localhost"
DB_PORT="5432"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/.."

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}✓${NC} $*"; }
warning() { echo -e "${YELLOW}!${NC} $*"; }
error()   { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "Hover AI — Local Database Setup"
echo "================================"
echo ""

# ── Ask for sudo password once, cache it for the whole script ─────────────────
echo "This script needs sudo to talk to PostgreSQL."
echo "Enter your password once and it will be cached for all steps below."
echo ""
sudo -v || error "sudo authentication failed"
# Keep the sudo timestamp alive in the background
( while true; do sudo -n true; sleep 50; done ) &
SUDO_REFRESH_PID=$!
# Kill the background process when the script exits
trap "kill $SUDO_REFRESH_PID 2>/dev/null || true" EXIT

# ── Check postgres is reachable ───────────────────────────────────────────────
if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -q; then
    error "PostgreSQL is not running on ${DB_HOST}:${DB_PORT}.
    Start it with:  sudo service postgresql start
    Install it with: sudo apt install postgresql"
fi
info "PostgreSQL is running on ${DB_HOST}:${DB_PORT}"
echo ""

# ── Create user ───────────────────────────────────────────────────────────────
USER_EXISTS=$(sudo -n -u postgres psql -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" 2>/dev/null || echo "")

if [ "$USER_EXISTS" = "1" ]; then
    warning "User '${DB_USER}' already exists — skipping"
else
    sudo -n -u postgres psql -c \
        "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';" > /dev/null
    info "User '${DB_USER}' created"
fi

# ── Create database ───────────────────────────────────────────────────────────
DB_EXISTS=$(sudo -n -u postgres psql -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" 2>/dev/null || echo "")

if [ "$DB_EXISTS" = "1" ]; then
    warning "Database '${DB_NAME}' already exists — skipping"
else
    sudo -n -u postgres psql -c \
        "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};" > /dev/null
    info "Database '${DB_NAME}' created"
fi

# ── Grant privileges (PG 15+ requires explicit schema grant) ──────────────────
sudo -n -u postgres psql -c \
    "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};" > /dev/null
sudo -n -u postgres psql -d "${DB_NAME}" -c \
    "GRANT ALL ON SCHEMA public TO ${DB_USER};" > /dev/null
info "Privileges granted"

# ── Run migrations (Alembic) ──────────────────────────────────────────────────
echo ""
DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}" \
    uv --directory "$BACKEND_DIR" run alembic upgrade head

info "alembic upgrade head applied"

# ── Verify tables ─────────────────────────────────────────────────────────────
echo ""
echo "Tables created:"
PGPASSWORD="${DB_PASS}" psql \
    -h "$DB_HOST" -p "$DB_PORT" \
    -U "$DB_USER" -d "$DB_NAME" \
    -tAc "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;" \
    | while read -r t; do info "$t"; done

# ── Print result ──────────────────────────────────────────────────────────────
DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

echo ""
echo "========================================================"
echo "  Done! Add this to backend/.env:"
echo ""
echo -e "  ${YELLOW}DATABASE_URL=${DATABASE_URL}${NC}"
echo ""
echo "  Start the dev server:"
echo "  cd backend && uv run hover-dev"
echo "========================================================"
echo ""
