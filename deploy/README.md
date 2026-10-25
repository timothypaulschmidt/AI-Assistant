# Deploy to k3s

Runs the AI Assistant and a small CPU-only Ollama in a k3s cluster.

```
Browser ──► NodePort :30080 ──► ai-assistant pod ──► Gemini (internet)
                                       │
                                       └──► ollama pod (in-cluster only, models on a 50 Gi volume)
```

What is here:

| File | What it is |
|---|---|
| `k8s/kustomization.yaml` | Applies everything; **image tags live here** |
| `k8s/namespace.yaml` | The `ai-assistant` namespace |
| `k8s/app/` | Assistant: Deployment, NodePort Service, `config.env` settings |
| `k8s/ollama/` | Ollama: Deployment, ClusterIP Service, 50 Gi volume for models |

These manifests were checked against the Kubernetes schemas and cross-checked
for matching names, labels, ports and settings, but they have not been applied
to a cluster yet. Steps 4 and 6 below are dry runs that let the cluster check
them before anything is created.

## Before you start

- k3s is running and `kubectl get nodes` works on the VM.
- The Docker Hub repository `timothypaulschmidt/ai-assistant` is **public**
  (Docker Hub → Repositories → ai-assistant → Settings → Visibility). The image
  contains no secrets: the Gemini key comes from a Kubernetes Secret.
  If you keep it private, see "Private image" at the bottom.

## 1. Build and push the image (Windows, `cmd`)

First merge the pull request on GitHub (the deploy folder only reaches your PC
after the merge), then get it:

```bat
cd C:\Users\timschmidt\Documents\Projects\AI-Assistant
git checkout main
git pull origin main
dir deploy\k8s
.venv\Scripts\activate.bat
pytest
docker build -t timothypaulschmidt/ai-assistant:v0.2.2 .
docker login
docker push timothypaulschmidt/ai-assistant:v0.2.2
```

`dir` must list `kustomization.yaml`, `namespace.yaml`, `app` and `ollama`. The tag you
push must match `newTag` in `deploy/k8s/kustomization.yaml`.

Check that anyone can pull it (this should work after `docker logout`):

```bat
docker logout
docker pull timothypaulschmidt/ai-assistant:v0.2.2
```

Also confirm the Ollama tag in `kustomization.yaml` exists (it prints JSON if it does):

```bat
docker manifest inspect ollama/ollama:0.40.10
```

If that tag is missing, pick a tag that exists on https://hub.docker.com/r/ollama/ollama/tags
and change `newTag` for `ollama/ollama` in `deploy/k8s/kustomization.yaml`.

## 2. Copy the manifests to the k3s VM (Windows, `cmd`)

From the project folder:

```bat
scp -r deploy youruser@VM_IP:~/
```

Then SSH in: `ssh youruser@VM_IP`. Everything below runs on the VM.

## 3. Create the namespace

```bash
kubectl apply -f deploy/k8s/namespace.yaml
```

## 4. Create the Gemini key Secret

`read -rs` hides what you type and keeps the key out of your shell history:

```bash
read -rs GEMINI_KEY
kubectl -n ai-assistant create secret generic gemini-api-key --from-literal=GEMINI_API_KEY="$GEMINI_KEY"
unset GEMINI_KEY
```

Paste the key, press Enter, and nothing is shown. Check it exists (this shows
the name only, not the value): `kubectl -n ai-assistant get secret gemini-api-key`

## 5. Preview, then apply

```bash
kubectl kustomize deploy/k8s | head -80          # just prints the final YAML
kubectl apply -k deploy/k8s --dry-run=server     # the cluster checks it; creates nothing
kubectl apply -k deploy/k8s
```

## 6. Watch it start

```bash
kubectl -n ai-assistant get pods -w
```

Wait until both pods show `Running` and `1/1` (Ctrl+C to stop watching). The
Ollama image is large, so its first start can take several minutes.

## 7. Pull a model into Ollama

Models are stored on the 50 Gi volume, so you do this once, not on every restart:

```bash
kubectl -n ai-assistant exec deploy/ollama -- ollama pull llama3.2:3b
kubectl -n ai-assistant exec deploy/ollama -- ollama list
```

To try another model, pull it the same way and change `OLLAMA_MODEL` in
`deploy/k8s/app/config.env` (step "Change settings" below).

## 8. Open it

Browse to `http://VM_IP:30080` from your Windows machine. The status line at the
top should show Gemini ready and Local ready. Send a message with the dropdown on
**Auto**, then once on **Local only** to prove the Ollama path works (expect it to
be slower).

## Day-to-day

**Deploy a new version of the assistant** (the app is already running)

1. On Windows: merge the PR on GitHub, then `git checkout main` and `git pull origin main`.
2. Build and push the new tag, **before** touching the cluster (use the `newTag` value from
   `deploy/k8s/kustomization.yaml`):
   ```bat
   docker build -t timothypaulschmidt/ai-assistant:v0.2.2 .
   docker push timothypaulschmidt/ai-assistant:v0.2.2
   ```
3. Copy the manifests: `scp -r deploy youruser@VM_IP:~/`
4. On the VM:
   ```bash
   kubectl apply -k deploy/k8s --dry-run=server
   kubectl apply -k deploy/k8s
   kubectl -n ai-assistant rollout status deploy/ai-assistant
   ```
   Only what changed restarts. The assistant pod is replaced; Ollama and its models are untouched.
5. Reload the page in your browser.

The Gemini secret is not touched by any of this.

**Roll back:** `kubectl -n ai-assistant rollout undo deploy/ai-assistant`

**Change settings** (model list, cooldowns, Ollama model): edit `deploy/k8s/app/config.env`,
copy it to the VM, `kubectl apply -k deploy/k8s`. The pod restarts by itself with the new values.

**Rotate the Gemini key:**

```bash
kubectl -n ai-assistant delete secret gemini-api-key
read -rs GEMINI_KEY
kubectl -n ai-assistant create secret generic gemini-api-key --from-literal=GEMINI_API_KEY="$GEMINI_KEY"
unset GEMINI_KEY
kubectl -n ai-assistant rollout restart deploy/ai-assistant
```

**Look at logs:** `kubectl -n ai-assistant logs deploy/ai-assistant` (or `deploy/ollama`).

**Remove only the assistant** (keeps the downloaded models):
`kubectl -n ai-assistant delete deploy/ai-assistant svc/ai-assistant`

**Remove everything:** `kubectl delete -k deploy/k8s` also deletes the model volume, so
the models must be downloaded again.

## If something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| `ImagePullBackOff` | Wrong tag, or the Docker Hub repo is private | `kubectl -n ai-assistant describe pod <name>`; check the tag was pushed and the repo is public |
| `CreateContainerConfigError` | The `gemini-api-key` Secret is missing | Do step 4 |
| `CrashLoopBackOff` | The app failed to start | `kubectl -n ai-assistant logs deploy/ai-assistant` |
| Ollama pod `Pending` | The volume could not be created | `kubectl -n ai-assistant describe pvc ollama-models` |
| Page says "Local: Ollama unreachable" | Ollama pod is not ready yet | `kubectl -n ai-assistant get pods`; wait for `1/1` |
| Page says "Local: model … not pulled" | Step 7 not done, or `OLLAMA_MODEL` differs from what you pulled | Pull it, or fix `config.env` |
| A Gemini model shows "not available to this key" | That model name does not exist for your key | Expected; it is skipped for an hour. Remove it from `GEMINI_MODELS` if you like |
| Cannot open `:30080` | VM firewall, or wrong IP | `kubectl -n ai-assistant get svc`; try `curl http://localhost:30080/healthz` on the VM |

## Prompt lab

`ALLOW_PROMPT_OVERRIDE=true` in `deploy/k8s/app/config.env` shows a **Prompt lab** button in
the page. A lab prompt replaces the system prompt for that chat, is sent to the **local model
only** (the server refuses it for Gemini), and saved presets live in your browser, not in git
or on the server. Set the value to `false` and apply to switch the feature off. With it off the
server rejects any custom prompt, even from a hand-made request.

## After a reboot

Everything comes back by itself, as long as the VM itself starts:

- k3s runs as a systemd service that is enabled at install. Check: `systemctl is-enabled k3s`
  (should say `enabled`).
- k3s restarts both pods, because they belong to Deployments. Allow 1 to 3 minutes. The images
  are already on the VM, so nothing is downloaded.
- The model volume and the Gemini Secret live on the VM's disk, so downloaded models and the key
  survive. You do not pull the model again.
- Not kept: Gemini cooldown timers (they reset, which is harmless) and any model loaded in RAM.
  The first local answer after a boot is slower while Ollama loads the model.

Check with `kubectl get nodes` and `kubectl -n ai-assistant get pods`. Right after boot
`kubectl` may say the connection was refused for a minute. Wait and retry.

Two things outside k3s can still stop you:

- If the **ESXi host** reboots, the VM only starts on its own if autostart is on: in the ESXi
  web UI go to Host, Manage, Autostart, and add the k3s VM.
- If the VM gets a different IP after boot, `http://VM_IP:30080` stops working. Give it a static
  IP or a DHCP reservation on your router.

## Security notes

- The assistant has **no login yet**. The NodePort makes it reachable by anything
  that can reach the VM's network, so keep it on your home LAN. Before using it
  away from home, put it behind a VPN (Tailscale or WireGuard) or add a token and HTTPS.
- Ollama has no login either, so its Service is `ClusterIP` (inside the cluster
  only). Do not change it to NodePort or LoadBalancer.
- The Gemini key is only in the Kubernetes Secret, never in git.

## Private image

If you make the Docker Hub repository private, create a pull secret and attach it:

```bash
kubectl -n ai-assistant create secret docker-registry dockerhub \
  --docker-username=timothypaulschmidt --docker-password=<Docker Hub access token>
```

Then add this under `spec.template.spec` in `deploy/k8s/app/deployment.yaml`:

```yaml
imagePullSecrets:
  - name: dockerhub
```

## Later: other sites and GPUs

The manifests use only standard Kubernetes, so they also run on full Kubernetes
(EKS, AKS, GKE). The parts that differ per site (storage class, how you reach the
service, GPU settings, where Ollama lives) are meant to move into small Kustomize
overlays. If Ollama runs on a separate GPU machine, leave out `k8s/ollama/` and
set `OLLAMA_BASE_URL` in `config.env` to that machine's address.
