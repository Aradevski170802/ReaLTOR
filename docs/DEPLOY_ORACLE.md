# Run the site 24/7 for free on an Oracle Cloud VM

This puts the whole app — the site, the API, the daily refresh, **and** the browser automation (Delaware C‑Track liens
and the Montgomery UJS judgments) — on one always‑on machine that costs nothing, so a colleague can just open a URL and
work. Plan on ~30 minutes the first time.

Why Oracle and not Render for this: the browser automation runs Chromium, which needs ~1 GB of RAM — more than Render's
free tier. Oracle Cloud's **Always Free** tier includes an Ampere (ARM) VM with up to 4 CPUs and 24 GB of RAM at no
cost, which runs everything comfortably.

---

## Part A — Create the free VM (in the Oracle web console)

1. Sign up at **https://www.oracle.com/cloud/free/**. It asks for a credit card to verify identity, but **Always Free
   resources never charge** (leave the account on the free plan). Pick a home region close to you.
2. In the console: **☰ Menu → Compute → Instances → Create instance**.
   - **Name:** `usi`
   - **Image:** click *Edit* → *Change image* → **Canonical Ubuntu 22.04**.
   - **Shape:** click *Edit* → *Change shape* → **Ampere** → `VM.Standard.A1.Flex`, set **2 OCPUs** and **12 GB** memory
     (well inside the Always‑Free limit of 4 OCPUs / 24 GB, and plenty for Chromium).
     - *If Ampere says "out of capacity"* (common in busy regions): try again later or another availability domain, or
       fall back to `VM.Standard.E2.1.Micro` (Always Free, but only 1 GB RAM — then you MUST add swap, see Part B‑4).
   - **SSH keys:** choose *Generate a key pair for me* and **Download the private key** (you'll need it to log in). Keep
     it safe.
   - Leave networking on the defaults (it creates a VCN + public subnet and gives the VM a public IP).
   - Click **Create**. Wait until it's *Running*, then copy the **Public IP address**.
3. **Open the app's port on the cloud firewall.** Still in the console:
   **Networking → Virtual Cloud Networks → (your VCN) → Subnets → (public subnet) → Security Lists → (default) →
   Add Ingress Rules**:
   - Source CIDR: `0.0.0.0/0`  · IP Protocol: `TCP`  · Destination Port Range: `8000`  → **Add**.
   - (If you set up HTTPS later, also add `443`.)

---

## Part B — Log in and prepare the machine (SSH)

From your own computer (Mac/Linux Terminal, or Git Bash / PowerShell on Windows):

**1. Connect** (replace the key path and IP):
```bash
ssh -i /path/to/your-private-key.key ubuntu@YOUR_PUBLIC_IP
```
(On the first connect type `yes`. If it complains the key is "too open", run `chmod 600 /path/to/your-private-key.key`.)

**2. Open the port on the VM's own firewall.** *This is the #1 thing people forget* — Oracle's Ubuntu image blocks every
port except SSH, in addition to the cloud firewall in Part A‑3:
```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

**3. Install Docker and git:**
```bash
sudo apt-get update && sudo apt-get install -y docker.io git
sudo systemctl enable --now docker
```

**4. Only if you had to use the 1 GB E2.1.Micro shape** — add swap so Chromium doesn't run out of memory (skip this on
Ampere):
```bash
sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## Part C — Get the code and configure secrets

**1. Clone the repository.** It's private, so use a GitHub **Personal Access Token** (github.com → Settings → Developer
settings → *Fine‑grained tokens* → give it read access to the `ReaLTOR` repo):
```bash
git clone https://YOUR_GITHUB_TOKEN@github.com/Aradevski170802/ReaLTOR.git
cd ReaLTOR
```
*(If you make the repo public, drop the token: `git clone https://github.com/Aradevski170802/ReaLTOR.git`.)*

**2. Create one master key and keep it.** The app encrypts stored secrets with this key; if it changes, previously saved
secrets can't be read — so generate it **once** and reuse it:
```bash
openssl rand -base64 48 > ~/usi_master_key && chmod 600 ~/usi_master_key
```

**3. Write the settings file** (keeps secrets out of your shell history). Replace the two placeholders:
```bash
cat > ~/usi.env <<EOF
USI_APP_ENV=production
USI_AUTH_MODE=local
USI_MASTER_KEY=$(cat ~/usi_master_key)
USI_SITE_USERNAME=demo
USI_SITE_PASSWORD=CHANGE_ME_a_strong_password
USI_SECRET_PROVIDER_ATTOM_API_KEY=CHANGE_ME_your_attom_key
USI_AUTO_ACCEPT_TERMS=true
USI_SEED_DEMO=false
USI_PUBLIC_BASE_URL=http://YOUR_PUBLIC_IP:8000
EOF
chmod 600 ~/usi.env
```
- `USI_SITE_PASSWORD` — what your friend types to get into the site (username stays `demo`).
- `USI_SECRET_PROVIDER_ATTOM_API_KEY` — your ATTOM key (this is how it's supplied without living in the code).
- `USI_SEED_DEMO=false` uses real data; set it to `true` if you want the demo projects loaded to look around first.
- Browser automation is **already on** in this image, so C‑Track liens and UJS judgments run automatically.

---

## Part D — Build and run

**1. Build the browser image** (first build pulls Chromium and takes a few minutes):
```bash
sudo docker build -f Dockerfile.browser -t usi-browser .
```

**2. Start it** (restarts on reboot; data persists in a Docker volume):
```bash
sudo docker run -d --name usi --restart unless-stopped -p 8000:8000 \
  --env-file ~/usi.env -v usi-data:/app/data usi-browser
```

**3. Check it's healthy:**
```bash
sudo docker logs -f usi        # look for "Uvicorn running on http://0.0.0.0:8000"; Ctrl+C to stop watching
```

**4. Open it:** in a browser go to `http://YOUR_PUBLIC_IP:8000`, sign in with username **`demo`** and your password.

---

## Part E — Give it to your friend

Send them three things:
- The link: `http://YOUR_PUBLIC_IP:8000`
- Username: `demo`
- The password you set

That's it — they can upload sale lists, run Autopilot, and review results from anywhere, any time.

---

## Running it: updates, logs, backups

- **See logs:** `sudo docker logs -f usi`
- **Update to the latest code** (data is kept in the `usi-data` volume):
  ```bash
  cd ~/ReaLTOR && git pull
  sudo docker build -f Dockerfile.browser -t usi-browser .
  sudo docker rm -f usi
  sudo docker run -d --name usi --restart unless-stopped -p 8000:8000 --env-file ~/usi.env -v usi-data:/app/data usi-browser
  ```
- **Back up the data:** it lives in the `usi-data` Docker volume (uploaded PDFs, evidence, results). Copy it with
  `sudo docker run --rm -v usi-data:/data -v ~:/backup alpine tar czf /backup/usi-backup.tgz -C /data .`

---

## Optional: a real domain + HTTPS (nicer for sharing)

Plain `http://IP:8000` works and is protected by the password, but the browser will say "Not secure" and the password
travels unencrypted. To get `https://` with a free name:

1. Get a free subdomain at **https://www.duckdns.org** (e.g. `yourname.duckdns.org`) and point it at your VM's IP.
2. Open port **443** in *both* firewalls (Part A‑3 and: `sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT && sudo netfilter-persistent save`).
3. Put **Caddy** (automatic HTTPS) in front of the app:
   ```bash
   echo "yourname.duckdns.org {
     reverse_proxy localhost:8000
   }" | sudo tee ~/Caddyfile
   sudo docker run -d --name caddy --restart unless-stopped --network host \
     -v ~/Caddyfile:/etc/caddy/Caddyfile -v caddy-data:/data caddy
   ```
   Also set `USI_PUBLIC_BASE_URL=https://yourname.duckdns.org` in `~/usi.env` and recreate the `usi` container (Part D‑2).
4. Share `https://yourname.duckdns.org` instead.

---

## Troubleshooting

- **Page won't load / times out:** almost always a firewall. Confirm *both* the Ingress rule (Part A‑3) **and** the
  `iptables` rule (Part B‑2) exist. Test locally on the VM first: `curl -I http://localhost:8000/api/health`.
- **Container keeps restarting:** `sudo docker logs usi`. If it mentions the master key, make sure `USI_MASTER_KEY` in
  `~/usi.env` is the same value every run (Part C‑2).
- **Out of memory / Chromium killed:** you're on the 1 GB micro shape — add swap (Part B‑4) or rebuild on an Ampere VM.
- **ATTOM shows "not configured":** the key in `USI_SECRET_PROVIDER_ATTOM_API_KEY` is missing or wrong; fix `~/usi.env`
  and recreate the container.
