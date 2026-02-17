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

## GitHub Pages

1. Push repository to GitHub (branch `main` or `master`).
2. Open repository settings: `Settings -> Pages`.
3. In **Build and deployment**, choose **Source: GitHub Actions**.
4. Workflow `.github/workflows/deploy-pages.yml` will build and publish automatically.
5. Wait for workflow completion in `Actions` tab and open published URL.

### Base path notes

- For GitHub Pages we use `VITE_BASE_PATH=/${REPO_NAME}/` in workflow.
- For custom domain (root), use `/` as base path.
