# Initial Deployment Checklist

✓ Project setup complete!
Your project is located at: `C:\Users\timschmidt\Documents\Projects\AI-Assistant`

## Python Project Developer
*Assumes you have Python 3.10 or newer installed as developer*

1. Open a terminal, navigate to project:
   ```bat
   cd C:\Users\timschmidt\Documents\Projects\AI-Assistant
   ```

2. Setup a virtual environment:
   ```bat
   python -m venv .venv
   ```

3. Activate it (the prompt should start with `(.venv)`):
   ```bat
   .venv\Scripts\activate.bat
   ```

4. Install the project dependencies and the project itself (libraries go to `<your project>\.venv\Lib\site-packages`):
   ```bat
   pip install -r requirements-dev.txt
   pip install -e .
   ```
   `pip install -e .` makes the `ai_assistant` package importable from `src\`. It is a one-time step per virtual environment.

5. Run the tests (all of them should pass):
   ```bat
   pytest
   ```

6. Run the app (get a free key at https://aistudio.google.com/app/apikey) and open http://localhost:8000:
   ```bat
   set GEMINI_API_KEY=your-key
   set OLLAMA_BASE_URL=http://localhost:11434
   uvicorn ai_assistant.web.main:app --reload --port 8000
   ```
   Never save your key in a file inside the project or commit it.

7. Where the code goes: `src\ai_assistant\` holds the code (`config.py`, `llm\` for the model providers and router, `web\` for the FastAPI app and chat page). Test files go in `tests\`.

8. To add a package, add a line to `requirements.txt` (or `requirements-dev.txt` for test tools) by hand, then run `pip install -r requirements-dev.txt`. Do **not** run `pip freeze > requirements.txt`: it would replace the curated list with every package installed on the machine.

## GitHub Source
*Assumes you created a GitHub Account*

### 1. Download and Install Git
Download from: https://git-scm.com/download/win

### 2. Configure Git
Open Git Bash and run:
```bash
git --version
git config --global --list
git config --global user.name "timothypaulschmidt"
git config --global user.email "timothypaulschmidt@gmail.com"
git config --global --list
```

### 3. Initialize Git in Your Project
```bash
cd C:/Users/timschmidt/Documents/Projects/AI-Assistant
git init
```

### 4. Make Your First Commit
Everything except what's in `.gitignore`. Run `git status` first and check that `.venv` and `.env` are **not** listed:
```bash
# See what will be added
git status

# Add all files to staging
git add .

# Create your first commit
git commit -m "Initial commit: project setup"
```

### 5. Create Repository on GitHub
- Go to https://github.com and log in
- Click the "+" in the top right, then "New repository"
- Name it: `AI-Assistant`
- Don't initialize with README (you already have one)
- Click "Create repository"

### 6. Connect Local Project to GitHub
Replace with your actual username/repo:
```bash
git remote add origin https://github.com/timothypaulschmidt/AI-Assistant.git
git branch -M main
git push -u origin main
```

## Docker Source
*Assumes you created a Docker Account*

### 1. Install Docker Desktop
Download from: https://www.docker.com/products/docker-desktop/

### 2. Verify Installation
```bat
docker --version
```

### 3. Navigate to Project
```bat
cd C:\Users\timschmidt\Documents\Projects\AI-Assistant
```

### 4. Understanding Docker Files
These files are in the project:

**Dockerfile** - Instructions for building your Docker image (simplified):
```dockerfile
FROM python:3.12-slim                 # Start with Python 3.12
ENV PYTHONPATH=/srv/src               # So `ai_assistant` can be imported
WORKDIR /srv                          # Set working directory
COPY requirements.txt .               # Copy requirements first (better caching)
RUN pip install -r requirements.txt   # Install dependencies
COPY src ./src                        # Copy your code
CMD ["uvicorn", "ai_assistant.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**.dockerignore** - Tells Docker what NOT to copy (like .gitignore but for Docker)

### 5. Build the Docker Image
Must be in lowercase:
```bat
docker build -t ai-assistant:dev .
```
(Optional, for ARM machines later: `docker build -t ai-assistant:dev --platform linux/amd64,linux/arm64 .`)

### 6. Run It
This is a web server, so you don't need `-it`. You need `-p` to reach it from your browser and `-e` to pass settings:
- `-p 8000:8000` = open container port 8000 on your computer's port 8000
- `-e NAME=value` = set an environment variable inside the container
- `--rm` = delete the container when it stops

In `cmd.exe`:
```bat
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=your-key -e OLLAMA_BASE_URL=http://host.docker.internal:11434 ai-assistant:dev
```
In PowerShell, replace each line break with a backtick (`` ` ``) if you split the command across lines.

Then open http://localhost:8000.

### 7. Tag Your Image for Docker Hub
Docker Hub names look like `username/repository` (your Docker Hub username, **not** an email address):
```bat
docker tag ai-assistant:dev timothypaulschmidt/ai-assistant:dev
```

### 8. Login to Docker Hub
```bat
docker login
```

### 9. Push Your Image
```bat
docker push timothypaulschmidt/ai-assistant:dev
```

---

## GitHub Destination Clone (2nd Computer)

### 1. Install Git on Destination Computer
Download from: https://git-scm.com/download/win

### 2. Navigate to Projects Folder
```bat
cd C:\Users\YourUsername\Documents\Projects
```

### 3. Clone the Repository
```bat
git clone https://github.com/timothypaulschmidt/AI-Assistant.git
```

## Python Project User (2nd Computer)
*Assumes you have Python 3.10 or newer installed*

1. Open a terminal:
   ```bat
   cd C:\Users\YourUsername\Documents\Projects\AI-Assistant
   ```

2. Setup a virtual environment:
   ```bat
   python -m venv .venv
   ```

3. Activate it:
   ```bat
   .venv\Scripts\activate.bat
   ```

4. Install project dependencies and the project itself:
   ```bat
   pip install -r requirements-dev.txt
   pip install -e .
   ```

5. Run the app and open http://localhost:8000:
   ```bat
   set GEMINI_API_KEY=your-key
   uvicorn ai_assistant.web.main:app --reload --port 8000
   ```

---

## Docker Destination (3rd Computer)

### 1. Install Docker Desktop
Download from: https://www.docker.com/products/docker-desktop/
*You don't need an account to use it*

### 2. Verify Installation
```bat
docker --version
```

### 3. Pull Your Image
```bat
docker pull timothypaulschmidt/ai-assistant:dev
```

### 4. Run It
```bat
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=your-key -e OLLAMA_BASE_URL=http://host.docker.internal:11434 timothypaulschmidt/ai-assistant:dev
```
Then open http://localhost:8000.

---

## Summary

### Initial Deployment (Without Docker)
1. Python: venv → activate → install requirements → `pip install -e .` → test
2. GitHub: Commit → Push
3. Other Computer: GitHub Clone
4. Other Computer: Python → venv → activate → install requirements → `pip install -e .`

### Initial Deployment (With Docker)
1. Python: venv → activate → install requirements → `pip install -e .` → test
2. GitHub: Commit → Push
3. Docker: Build image → Tag → Push to Docker Hub
4. Other Computer: Docker Pull → Run (with `-p` and `-e`)
