# OTP

## Local start

```powershell
npm install
npm run dev
```

## Production build

```powershell
npm run build
```

`dist/` will contain ready static files.

## Chat2Desk daily metrics sync

The backend can import chat-manager response time and ratings from Chat2Desk once per day for the previous day.

Required:

```env
CHAT2DESK_API_TOKEN=your_api_token
```

Optional:

```env
CHAT2DESK_API_BASE_URL=https://api.chat2desk.com
CHAT2DESK_SYNC_ENABLED=true
CHAT2DESK_SYNC_TIMEZONE=Asia/Almaty
CHAT2DESK_SYNC_HOUR=4
CHAT2DESK_SYNC_MINUTE=10
CHAT2DESK_SYNC_DAYS_BACK=1
CHAT2DESK_API_MAX_PAGES=100
```

## GitHub Pages

1. Push repository to GitHub (branch `main` or `master`).
2. Open repository settings: `Settings -> Pages`.
3. In **Build and deployment**, choose **Source: GitHub Actions**.
4. Workflow `.github/workflows/deploy-pages.yml` will build and publish automatically.
5. Wait for workflow completion in `Actions` tab and open published URL.

### Base path notes

- For GitHub Pages we use `VITE_BASE_PATH=/${REPO_NAME}/` in workflow.
- For custom domain (root), use `/` as base path.
