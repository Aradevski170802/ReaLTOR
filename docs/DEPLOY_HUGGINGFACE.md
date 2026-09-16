# Run the site 24/7 for free on Hugging Face Spaces (no credit card)

Hugging Face's free CPU Space gives you 2 vCPUs and **16 GB of RAM** with **no credit card** — enough to run the whole
app *including* the browser automation (Delaware C-Track liens + Montgomery UJS judgments). Your colleague gets a clean
URL like `https://your-name-usi.hf.space`, protected by a password you set. Plan on ~20 minutes.

Two small files do all the work: a `Dockerfile` that clones the app from GitHub and installs Chromium, and a `README.md`
that configures the Space. Both are in [`deploy/hf/`](../deploy/hf/).

> **Sleep & data:** a free Space sleeps after ~48 h of no visitors and wakes on the next visit (a few seconds). When it
> restarts, files on its local disk reset — so for real ongoing work, do **Step 5 (free Neon database)**, which keeps all
> the projects, properties and results safe across restarts. Skip it only for a quick demo.

---

## Step 1 — Make the GitHub repo readable by the build

The Space's Dockerfile clones the code from GitHub with no credentials, so the repo must be **public**. The app itself is
still locked behind your password, so this only exposes the source code, not any data.

- On GitHub: **your repo → Settings → General → Danger Zone → Change visibility → Make public**.
- *Prefer to keep it private?* See [Private repo](#private-repo) at the bottom.

## Step 2 — Create a free Hugging Face account

Go to **https://huggingface.co/join** and sign up with an email. No credit card.

## Step 3 — Create the Space

1. **https://huggingface.co/new-space**.
2. **Owner:** you. **Space name:** e.g. `usi`. **License:** any.
3. **Select the Space SDK:** choose **Docker** → **Blank**.
4. **Visibility:** **Public** (so your friend can reach it; your password still guards the app).
5. Click **Create Space**.

## Step 4 — Add the two files

In the new Space, open the **Files** tab → **+ Add file → Create a new file**, once for each:

1. Name it **`Dockerfile`** and paste the contents of [`deploy/hf/Dockerfile`](../deploy/hf/Dockerfile). Commit.
2. Name it **`README.md`** and paste the contents of [`deploy/hf/README.md`](../deploy/hf/README.md). Commit.

*(If your GitHub username/repo differs, edit the `REPO=` line at the top of the `Dockerfile` before committing.)*

## Step 5 — (Recommended) a free database so data survives restarts

1. Sign up at **https://neon.tech** (free, no card) and create a project. Copy its connection string — it looks like
   `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`.
2. Change the prefix `postgresql://` to **`postgresql+psycopg://`** (keep the `?sslmode=require`). You'll paste this as
   `USI_DATABASE_URL` in the next step. Without this, the app uses a local file that resets when the Space restarts.

## Step 6 — Set the secrets

In the Space: **Settings → Variables and secrets → New secret**, add each of these (as **Secrets**, not Variables):

| Name | Value |
|---|---|
| `USI_MASTER_KEY` | Any random string of **40+ characters**. Generate one and keep it forever (changing it makes saved keys unreadable). |
| `USI_SITE_PASSWORD` | The password your friend will type to open the site. |
| `USI_SECRET_PROVIDER_ATTOM_API_KEY` | Your ATTOM API key (this is how the key reaches the app without being in the code). |
| `USI_DATABASE_URL` | The `postgresql+psycopg://…` string from Step 5 (omit if you skipped it). |

And these as **Variables** (not secret — they're not sensitive):

| Name | Value |
|---|---|
| `USI_SITE_USERNAME` | `demo` (or any username you like) |
| `USI_AUTO_ACCEPT_TERMS` | `true` |
| `USI_PUBLIC_BASE_URL` | `https://YOUR-SPACE-URL.hf.space` (fill in after the first build shows the URL) |

Browser automation is already switched on inside the Dockerfile, so you don't need a variable for it.

## Step 7 — Build and open

Adding the files triggers a build automatically (watch the **Logs** tab; the first build takes several minutes because it
installs Chromium). When it finishes, the Space shows your app. Click the **⋮ menu → Embed this Space** or just use the
direct URL `https://<your-name>-<space>.hf.space`. Sign in with your username (`demo`) and `USI_SITE_PASSWORD`.

## Step 8 — Hand it to your friend

Send them:
- The link: `https://<your-name>-<space>.hf.space`
- Username: `demo`
- The password (`USI_SITE_PASSWORD`)

They can upload sale lists, run **Autopilot**, and review results any time. As long as someone visits at least every
couple of days it stays awake; otherwise the first visit after a long idle takes a few seconds to wake it.

---

## Updating to the latest code

The Dockerfile clones GitHub at build time, so to pick up new commits: in the Space, **Settings → Factory rebuild**. That
does a clean rebuild and re-clones the repo. Your database (Step 5) is untouched.

## Private repo

If you'd rather keep the GitHub repo private, the build can't clone it anonymously. Two options:

- **Push the code straight to the Space** instead of cloning: the Space is itself a git repo. Add it as a remote and push
  a branch whose root `Dockerfile` is [`Dockerfile.browser`](../Dockerfile.browser) and whose `README.md` is
  [`deploy/hf/README.md`](../deploy/hf/README.md). Ask and I can prepare that branch for you.
- **Give the build a token:** create a GitHub read-only token and change the clone URL in the Dockerfile to
  `https://<TOKEN>@github.com/Aradevski170802/ReaLTOR.git`. (Simpler, but the token sits in the Space's Dockerfile, so
  use a minimal fine-grained token and rotate it if needed.)

## Troubleshooting

- **Build fails at `git clone`:** the repo isn't public yet (Step 1), or the `REPO=` URL is wrong.
- **App loads but ATTOM says "not configured":** `USI_SECRET_PROVIDER_ATTOM_API_KEY` is missing or misspelled.
- **Everything resets after a day or two:** you skipped Step 5 — add the Neon `USI_DATABASE_URL` so data persists.
- **Password not accepted / no prompt:** confirm `USI_SITE_PASSWORD` is set and `USI_AUTH_MODE` is left at its default
  (`local`).
