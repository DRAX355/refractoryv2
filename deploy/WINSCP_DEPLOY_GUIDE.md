# WinSCP + AWS EC2 Deployment Guide (`65.1.125.127`)

## 1. Server Specification
- **Region**: Mumbai (`ap-south-1`)
- **Instance**: `m7i.xlarge` (4 vCPUs, 16 GB RAM, 80 GB gp3)
- **OS**: Ubuntu Server 24.04 LTS (`x86_64`)
- **Public / Elastic IP**: `http://65.1.125.127`
- **Public DNS**: `ec2-65-1-125-127.ap-south-1.compute.amazonaws.com`
- **Open Ports**: `22` (SSH/SFTP), `80` (HTTP via Nginx), `443` (HTTPS)

---

## 2. Step-by-Step Deployment via WinSCP

### Step A — Connect in WinSCP
1. Open **WinSCP**.
2. Set **File protocol**: `SFTP`
3. Set **Host name**: `65.1.125.127` (or `ec2-65-1-125-127.ap-south-1.compute.amazonaws.com`)
4. Set **Port number**: `22`
5. Set **User name**: `ubuntu`
6. Click **Advanced...** -> **SSH** -> **Authentication** -> under **Private key file**, browse and select `AyushSrivastavaKey.pem` (WinSCP will automatically offer to convert `.pem` to `.ppk` — click **OK** and save `AyushSrivastavaKey.ppk`).
7. Click **Login**.

### Step B — Upload the Packaged Server Bundle
1. In the remote (right-hand) panel of WinSCP, navigate to `/home/ubuntu/`.
2. Drag and drop `server_deploy_ec2.zip` from your local PC (`refractory-master/server_deploy_ec2.zip`) into `/home/ubuntu/`.

### Step C — Run the One-Command Setup via SSH
Open PowerShell or PuTTY (or press `Ctrl+P` inside WinSCP to open a terminal) and run:

```bash
ssh -i "AyushSrivastavaKey.pem" ubuntu@ec2-65-1-125-127.ap-south-1.compute.amazonaws.com
```

Once logged into Ubuntu on EC2, run:

```bash
mkdir -p ~/refractory-server
unzip -o ~/server_deploy_ec2.zip -d ~/refractory-server
sed -i 's/\r$//' ~/refractory-server/deploy/setup_ec2.sh
chmod +x ~/refractory-server/deploy/setup_ec2.sh
~/refractory-server/deploy/setup_ec2.sh
```

---

## 3. Useful Server Management Commands (on EC2)

- **Check Server Status**:
  ```bash
  sudo systemctl status refractory
  curl http://127.0.0.1/health
  ```
- **View Live Server Logs**:
  ```bash
  sudo journalctl -u refractory -f
  ```
- **Restart Backend After Updating Files in WinSCP**:
  ```bash
  sudo systemctl restart refractory
  ```
- **Optional: Set Gemini API Key on Server**:
  Edit `/home/ubuntu/refractory-server/.env`:
  ```bash
  nano ~/refractory-server/.env
  # Set GEMINI_API_KEY=your_key_here
  sudo systemctl restart refractory
  ```
