# Initial Deployment Checklist

✓ Project setup complete!
Your project is located at: `C:\Users\timschmidt\Documents\Projects\AI-Assistant`

## Python Project Developer
*Assumes you have Python installed as developer*

1. Open a terminal, navigate to project:
   ```bash
   cd C:\Users\timschmidt\Documents\Projects\AI-Assistant
   ```

2. Setup a virtual environment:
   ```bash
   python -m venv venv
   ```

3. Activate it:
   ```bash
   venv\Scripts\activate
   ```

4. Install project dependencies (they go to `<your project>\venv\Lib\site-packages`):
   ```bash
   pip install pandas  # Example
   ```

5. Add code to `\src\main.py`, add functions to `\src\utils` and test files to `\tests`

6. Save dependencies:
   ```bash
   pip freeze > requirements.txt
   ```

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
Everything except what's in `.gitignore`:
```bash
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
```bash
docker --version
```

### 3. Navigate to Project
```bash
cd C:\Users\timschmidt\Documents\Projects\AI-Assistant
```

### 4. Understanding Docker Files
These files were already added by the setup script:

**Dockerfile** - Instructions for building your Docker image:
```dockerfile
FROM python:3.11-slim          # Start with Python 3.11
WORKDIR /app                   # Set working directory
COPY requirements.txt .        # Copy requirements first
RUN pip install ...            # Install dependencies
COPY src/ ./src/               # Copy your code
CMD ["python", "src/main.py"]  # Run your app
```

**.dockerignore** - Tells Docker what NOT to copy (like .gitignore but for Docker)

### 5. Build the Docker Image
Must be in lowercase:
```bash
docker build -t ai-assistant .
# docker build -t ai-assistant --platform linux/amd64,linux/arm64 .
```

### 6. Run It
Add the `-it` flags for interactive mode:
- `-i` = interactive (keeps STDIN open so you can type input)
- `-t` = tty (allocates a pseudo-terminal)

```bash
docker run -it ai-assistant
```

### 7. Tag Your Image for Docker Hub
```bash
docker tag ai-assistant timothypaulschmidt@gmail.com/ai-assistant
```

### 8. Login to Docker Hub
```bash
docker login
```

### 9. Push Your Image
```bash
docker push timothypaulschmidt@gmail.com/ai-assistant
```

---

## GitHub Destination Clone (2nd Computer)

### 1. Install Git on Destination Computer
Download from: https://git-scm.com/download/win

### 2. Navigate to Projects Folder
```bash
cd C:\Users\YourUsername\Documents\Projects
```

### 3. Clone the Repository
```bash
git clone https://github.com/timothypaulschmidt@gmail.com/AI-Assistant.git
```

## Python Project User (2nd Computer)
*Assumes you have Python installed*

1. Open a terminal:
   ```bash
   cd C:\Users\YourUsername\Documents\Projects\AI-Assistant
   ```

2. Setup a virtual environment:
   ```bash
   python -m venv venv
   ```

3. Activate it:
   ```bash
   venv\Scripts\activate
   ```

4. Install project dependencies:
   ```bash
   pip install -r requirements.txt
   ```

5. Run code:
   ```bash
   python src/main.py
   ```

---

## Docker Destination (3rd Computer)

### 1. Install Docker Desktop
Download from: https://www.docker.com/products/docker-desktop/
*You don't need an account to use it*

### 2. Verify Installation
```bash
docker --version
```

### 3. Pull Your Image
```bash
docker pull timothypaulschmidt@gmail.com/ai-assistant
```

### 4. Run It
Add the `-it` flags for interactive mode:
```bash
docker run -it timothypaulschmidt@gmail.com/ai-assistant
```

---

## Summary

### Initial Deployment (Without Docker)
1. Python: venv → activate → install dependencies → freeze
2. GitHub: Commit → Push
3. Other Computer: GitHub Clone
4. Other Computer: Python → venv → activate → install requirements

### Initial Deployment (With Docker)
1. Python: venv → activate → install dependencies → freeze
2. GitHub: Commit → Push
3. Docker: Build image → Tag → Push to Docker Hub
4. Other Computer: Docker Pull → Run
