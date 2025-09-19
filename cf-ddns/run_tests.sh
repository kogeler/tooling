#!/bin/bash

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

# DDNS Service Test Runner
# This script sets up a virtual environment and runs all tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo "============================================================"
echo "DDNS Service Test Runner"
echo "============================================================"

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is required but not installed."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "✅ Virtual environment created"
else
    echo "📦 Virtual environment already exists"
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Install/upgrade dependencies
echo "📚 Installing dependencies..."
pip3 install --quiet --upgrade pip
pip3 install --quiet -r requirements.txt

# Run tests
echo ""
echo "🧪 Running tests..."
echo "------------------------------------------------------------"

cd "$SCRIPT_DIR"
python3 test_ddns.py

# Deactivate virtual environment
deactivate

echo ""
echo "✅ Test execution completed"
