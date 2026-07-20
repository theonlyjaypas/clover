# Deployment Guide

This guide covers deploying the FastAPI application to Render.

## Render Deployment

Render is a modern cloud platform that makes deploying FastAPI applications straightforward. It handles Docker containerization, auto-scaling, and provides a simple Git-based workflow.

### Prerequisites

- A Render account (sign up at https://render.com)
- GitHub account with this repository pushed
- Repository access configured in Render

### Step 1: Prepare Your Repository

Ensure your repository is pushed to GitHub and is public or you've authorized Render to access private repos.

### Step 2: Create render.yaml Configuration

Create a `render.yaml` file in the root of your repository:

```yaml
services:
  - type: web
    name: clover-api
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn server:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: PYTHON_VERSION
        value: "3.11"
      - key: PORT
        value: "8000"
    healthCheckPath: /health
    healthCheckInterval: 30
```

### Step 3: Add Environment Variables

1. Go to https://render.com/dashboard
2. Click "New +" and select "Web Service"
3. Connect your GitHub repository
4. Fill in the service details:
   - **Name**: `clover-api` (or your preferred name)
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn server:app --host 0.0.0.0 --port $PORT`

5. Under "Advanced", add environment variables:
   - Click "Add Environment Variable"
   - Add your `.env` variables:
     - `ANTHROPIC_API_KEY` (your API key from .env)
     - Any other variables from your .env file

### Step 4: Configure Health Check (Optional but Recommended)

1. Add a health check endpoint to your FastAPI app if not already present:

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "healthy"}
```

2. In Render dashboard under "Health Check", set:
   - Path: `/health`
   - Interval: 30 seconds

### Step 5: Deploy

**Option A: Deploy from Render Dashboard**
1. Click "Create Web Service"
2. Select your GitHub repository
3. Render will automatically deploy from `render.yaml`
4. Wait for the build to complete (usually 2-5 minutes)

**Option B: Deploy via GitHub Push**
1. If you created `render.yaml`, just push to GitHub:
   ```bash
   git add render.yaml
   git commit -m "Add render deployment config"
   git push origin main
   ```
2. Render will automatically detect and deploy

### Step 6: Verify Deployment

1. After deployment completes, you'll see a live URL: `https://your-service-name.onrender.com`
2. Test your API:
   ```bash
   curl https://your-service-name.onrender.com/health
   ```
3. Check the Logs tab in Render dashboard for any errors

## Environment Variables Setup

Critical variables to set in Render:

| Variable | Source | Required |
|----------|--------|----------|
| `ANTHROPIC_API_KEY` | Your .env file | Yes |
| Other API keys | Your .env file | As needed |

**To add variables:**
1. Dashboard > Your Service > Environment
2. Click "Add Environment Variable"
3. Enter key and value
4. Click "Save Changes" - this triggers a redeploy

## Important Notes

### Secrets Management
- Never commit `.env` file to GitHub
- Always use Render's environment variable system
- `.env` file is in `.gitignore` (good!)

### Cold Starts
- Free tier may have 15-minute inactivity cold starts
- Paid tier (Pro) has no cold starts
- Use health checks to keep service warm

### File Storage
- Render instances are ephemeral
- Files written to disk are lost on redeploy
- Use external storage (S3, etc.) if needed

### Upgrading Plan

Free tier limitations:
- Spins down after 15 minutes inactivity
- Limited to 750 hours/month
- 1GB RAM

To upgrade:
1. Dashboard > Your Service > Plan
2. Select "Pro" ($12/month) or higher
3. No cold starts, automatic scaling

## Troubleshooting

### Service Won't Start
Check logs:
1. Dashboard > Your Service > Logs
2. Look for build errors or startup errors
3. Common issues:
   - Missing dependencies: Check `requirements.txt`
   - Port binding: Ensure using `$PORT` environment variable
   - Missing environment variables: Add missing keys

### 502 Bad Gateway
- Service crashed: Check Logs for error messages
- Health check failing: Verify health endpoint returns 200
- Out of memory: Upgrade to higher plan

### Slow Performance
- Check "Metrics" tab for CPU/Memory usage
- Free tier may be underpowered
- Consider upgrading to Pro or higher

### Logs Not Showing
- Give deployment 30 seconds after starting
- Refresh the logs page
- Check if build failed (top of logs)

## Rollback

If deployment fails:
1. Dashboard > Your Service > Deployments
2. Click on previous successful deployment
3. Click "Redeploy"

## Monitoring

Render provides:
- **Logs**: Real-time application logs
- **Metrics**: CPU, Memory, Network usage
- **Status**: Service health status
- **Events**: Deployment history and errors

Access via Dashboard > Your Service

## Next Steps

After deployment:
1. Set up custom domain (optional):
   - Settings > Custom Domains
   - Add your domain and configure DNS

2. Set up alerting (optional):
   - Settings > Alerts
   - Get notified of service issues

3. Enable auto-deploy:
   - Already enabled if using GitHub connection
   - Automatic redeploy on git push to `main`

## Support

- Render Docs: https://render.com/docs
- For issues: https://render.com/support
- FastAPI docs: https://fastapi.tiangolo.com/deployment/

