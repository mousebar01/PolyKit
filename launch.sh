#!/bin/bash

echo " PolyKit — Production Launcher"
echo "================================"
echo

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "[ERROR] Node.js is not installed or not in PATH."
    echo "        Download it from https://nodejs.org"
    exit 1
fi

# Install dependencies if node_modules is missing
if [ ! -d "node_modules" ]; then
    echo "[1/2] Installing dependencies..."
    npm install || { echo "[ERROR] npm install failed."; exit 1; }
    echo
fi

# Build if the Web bundle is missing
if [ ! -d "dist-web" ]; then
    echo "[2/2] Building the app..."
    npm run web:build || { echo "[ERROR] Build failed."; exit 1; }
    echo
fi

# Launch
echo "Launching PolyKit..."
npm run web:preview
