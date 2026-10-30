#!/usr/bin/env bash
# ==============================================================================
# RefractoryAI / LiningOps Copilot - Ubuntu 24.04 LTS EC2 Provisioning Script
# Target Instance: m7i.xlarge (4 vCPUs, 16 GB RAM, 80 GB gp3) - 65.1.125.127
# ==============================================================================
set -euo pipefail

APP_DIR="/home/ubuntu/refractory-server"
echo "=================================================================="
echo " Starting RefractoryAI EC2 Server Setup in ${APP_DIR}"
echo "=================================================================="

# 1. Install Ubuntu 24.04 system packages (OCR, PDF rendering, OpenCV libs, Nginx)
echo "[1/6] Installing Ubuntu system dependencies..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-pip python3-dev build-essential \
    tesseract-ocr libtesseract-dev poppler-utils \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    nginx unzip curl jq

# 2. Ensure directory structure exists
echo "[2/6] Preparing application directories..."
mkdir -p "${APP_DIR}/uploads" "${APP_DIR}/outputs" "${APP_DIR}/data/sessions"
cd "${APP_DIR}"

# Fix CRLF line endings if uploaded from Windows via WinSCP
sed -i 's/\r$//' "${APP_DIR}/requirements.txt" 2>/dev/null || true

# 3. Create Python Virtual Environment & Install Dependencies (CPU-optimized PyTorch)
echo "[3/6] Creating Python virtual environment and installing packages..."
if [ ! -d "${APP_DIR}/venv" ]; then
    python3 -m venv "${APP_DIR}/venv"
fi

source "${APP_DIR}/venv/bin/activate"
pip install --upgrade pip setuptools wheel

# Install CPU-optimized PyTorch & Torchvision first (saves ~2.5 GB vs CUDA wheels on m7i.xlarge)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install project requirements and editable refractory_core package
pip install -r "${APP_DIR}/requirements.txt"
pip install -e "${APP_DIR}"

# 4. Configure .env if not present
echo "[4/6] Configuring environment variables..."
if [ ! -f "${APP_DIR}/.env" ] && [ -f "${APP_DIR}/.env.example" ]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    sed -i 's|^TESSERACT_CMD=.*|TESSERACT_CMD=/usr/bin/tesseract|' "${APP_DIR}/.env"
    sed -i 's|^POPPLER_PATH=.*|POPPLER_PATH=|' "${APP_DIR}/.env"
fi

# 5. Configure systemd service (auto-start on boot & auto-restart)
echo "[5/6] Configuring systemd service (refractory.service)..."
sed -i 's/\r$//' "${APP_DIR}/deploy/refractory.service" 2>/dev/null || true
sudo cp "${APP_DIR}/deploy/refractory.service" /etc/systemd/system/refractory.service
sudo systemctl daemon-reload
sudo systemctl enable refractory.service
sudo systemctl restart refractory.service

# 6. Configure Nginx Reverse Proxy on Port 80 -> 127.0.0.1:8000
echo "[6/6] Configuring Nginx reverse proxy on port 80..."
sed -i 's/\r$//' "${APP_DIR}/deploy/nginx_refractory.conf" 2>/dev/null || true
sudo rm -f /etc/nginx/sites-enabled/*
sudo cp "${APP_DIR}/deploy/nginx_refractory.conf" /etc/nginx/sites-available/refractory
sudo ln -sf /etc/nginx/sites-available/refractory /etc/nginx/sites-enabled/refractory
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo "=================================================================="
echo " Deployment Complete!"
echo " Server URL:  http://65.1.125.127"
echo " Health API:  http://65.1.125.127/health"
echo " Service Log: sudo journalctl -u refractory -f"
echo "=================================================================="
