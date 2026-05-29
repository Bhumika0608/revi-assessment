#!/usr/bin/env bash
set -e

echo "Setting up Talkin' Tacos..."

# Python dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# DB Browser for SQLite (macOS only)
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v brew &>/dev/null; then
        echo "Homebrew not found — install it from https://brew.sh, then re-run this script."
        echo "Or download DB Browser for SQLite manually from https://sqlitebrowser.org"
    elif ! brew list --cask db-browser-for-sqlite &>/dev/null; then
        echo "Installing DB Browser for SQLite..."
        brew install --cask db-browser-for-sqlite
    else
        echo "DB Browser for SQLite already installed."
    fi
fi

echo ""
echo "Done! Activate your venv with: source venv/bin/activate"
echo "Run the UI with: streamlit run ui/app.py"
