# Update Cycle Checklist

## Quick Summary
Every update cycle:
1. Code → Test → Commit → Push to GitHub
2. Build Docker image → Tag → Push to Docker Hub
3. On other computer: Pull → Run

---

## Complete Update Workflow

### On Your Source Computer (Development Machine)

#### 1. Make Changes to Your Python Code
Edit your files in your IDE (VS Code, Spyder, etc.). The code is in `src\ai_assistant\`.

#### 2. Test Locally
```bat
.venv\Scripts\activate.bat
pytest
set GEMINI_API_KEY=your-key
uvicorn ai_assistant.web.main:app --reload --port 8000
```
Then open http://localhost:8000.

#### 3. Update Requirements (If You Added Packages)
Add the package to `requirements.txt` by hand (or `requirements-dev.txt` for test tools), then install it:
```bat
pip install -r requirements-dev.txt
```
Do **not** run `pip freeze > requirements.txt`: it would overwrite the curated list with every package installed on your machine.

#### 4. Commit and Push to GitHub
```bat
git status
git add .
git commit -m "Bugfix: description of what you fixed"
git push origin main
```

#### 5. Rebuild Your Docker Image
```bat
docker build -t ai-assistant:dev .
```

#### 6. Tag It for Docker Hub
Docker Hub names are `username/repository` (your username, not your email):
```bat
docker tag ai-assistant:dev timothypaulschmidt/ai-assistant:dev
```

#### 7. Push to Docker Hub
```bat
docker push timothypaulschmidt/ai-assistant:dev
```

---

### On Your Destination Computer

#### Option A: Using Python/GitHub Only

1. Navigate to project:
   ```bat
   cd C:\Users\YourUsername\Documents\Projects\AI-Assistant
   ```

2. Pull latest changes:
   ```bat
   git pull origin main
   ```

3. Activate venv:
   ```bat
   .venv\Scripts\activate.bat
   ```

4. Update dependencies (if requirements.txt changed):
   ```bat
   pip install -r requirements-dev.txt
   pip install -e .
   ```

5. Run the updated code and open http://localhost:8000:
   ```bat
   set GEMINI_API_KEY=your-key
   uvicorn ai_assistant.web.main:app --reload --port 8000
   ```

#### Option B: Using Docker Only

1. Pull the updated image:
   ```bat
   docker pull timothypaulschmidt/ai-assistant:dev
   ```

2. Run the new version (`-p` makes it reachable from your browser, `-e` passes settings):
   ```bat
   docker run --rm -p 8000:8000 -e GEMINI_API_KEY=your-key -e OLLAMA_BASE_URL=http://host.docker.internal:11434 timothypaulschmidt/ai-assistant:dev
   ```
   Then open http://localhost:8000.

---

## Pro Tips

### Version Tagging
Instead of always using one moving tag like `dev`, you can version your Docker images so you always know what is running:

```bat
rem On source computer
docker build -t ai-assistant:v0.1.0 .
docker tag ai-assistant:v0.1.0 timothypaulschmidt/ai-assistant:v0.1.0
docker push timothypaulschmidt/ai-assistant:v0.1.0
```

Then on destination:
```bat
rem Pull and run a specific version
docker pull timothypaulschmidt/ai-assistant:v0.1.0
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=your-key timothypaulschmidt/ai-assistant:v0.1.0
```

### Git Best Practices
- Use descriptive commit messages
- Commit often (small, logical changes)
- Run `pytest` before committing
- Run `git status` before `git add .` and check that no `.venv` or `.env` files are listed
- Always pull before pushing if working on multiple computers
- Never commit API keys

### Docker Best Practices
- Rebuild image after any code changes
- Always test locally before pushing to Docker Hub
- Use version tags for important releases
- Keep your Dockerfile and .dockerignore up to date
