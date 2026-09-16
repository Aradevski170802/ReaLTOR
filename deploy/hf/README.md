---
title: Upset Sale Intel
emoji: 🏠
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
---

# Upset Sale Intel

Property-research app for Montgomery & Delaware County (PA) upset-sale lists. This Hugging Face Space runs the whole app
— the site, the API, the daily refresh, and the browser automation — from the Dockerfile, which clones the code from
GitHub at build time.

The site is protected by a username and password (`USI_SITE_USERNAME` / `USI_SITE_PASSWORD`), set as Space secrets. See
`docs/DEPLOY_HUGGINGFACE.md` in the repository for the full setup.
