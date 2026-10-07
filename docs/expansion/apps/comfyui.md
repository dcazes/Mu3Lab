# ComfyUI (task 5-09)

Local image generation on the RTX 3060. **Owner/operators only** in its own
interface (it runs arbitrary custom-node code and has no accounts).
Household members generate images through the chat app (Open WebUI's
ComfyUI integration; LobeChat if supported).

## 1. VERIFY list

- ComfyUI release tags (`comfyanonymous/ComfyUI` or `Comfy-Org/ComfyUI`;
  VERIFY the canonical repo) and licence (GPL-3.0).
- **Image:** upstream publishes no official Docker image (VERIFY again; this
  may have changed). Options:
  a. **Mu3Lab-built image** (`apps/comfyui/Dockerfile`): pinned
     `nvidia/cuda:<ver>-runtime-ubuntu<ver>` or `pytorch/pytorch:<ver>-cuda<ver>`
     base by digest, ComfyUI at a pinned tag, `pip install` from a
     **hash-pinned** requirements lock (`--require-hashes`). Built by the
     release workflow like connector images. **amd64 only** (CUDA). VERIFY
     that the release workflow can skip arm64 for one image, and that GHCR
     size limits allow a ~6–10 GB image.
  b. A community image (e.g. `yanwk/comfyui-boot`): licence, maintenance,
     what it downloads at runtime, whether it runs ComfyUI-Manager by
     default.
  Recommend (a) for supply-chain control. Record the decision and the
  reasons.
- CPU mode flag (`--cpu`) for the VM test and non-NVIDIA hosts.
- Flags: `--listen 0.0.0.0 --port 8188`, `--disable-auto-launch`,
  memory flags (`--disable-smart-memory`, `--lowvram`/`--normalvram`), and
  whether `POST /free` (`{"unload_models": true, "free_memory": true}`) exists.
- API: `/prompt` (queue), `/queue`, `/history`, `/view`, `/system_stats`,
  `/object_info`; WebSocket `/ws`.
- **ComfyUI-Manager:** bundled or not; how to disable installing custom
  nodes (security). Is the new built-in "frontend" package pinned via
  requirements?
- Health: `/system_stats`.
- MCP servers for local ComfyUI (community; several exist). Licence and
  activity. Note: the official Comfy MCP targets **Comfy Cloud**, not local.
  Do not use it.
- Open WebUI's ComfyUI integration: required workflow format (API-format
  JSON) and node-ID mapping.
- LobeChat ComfyUI support (reported as a provider in recent versions;
  VERIFY).

## 2. Services and storage

| Service | Image | Port | Data |
|---|---|---|---|
| `comfyui` | per §1 | 8188 → `127.0.0.1:8188` | `data/comfyui/user` → `/app/user` (workflows, settings); `data/comfyui/output` → `/app/output`; `data/comfyui/input` → `/app/input`; media `ai-models` → `/app/models` (rw) |

- **Models live in `media/ai-models`** (large, re-downloadable, excluded from
  backups) with ComfyUI's subfolder layout (`checkpoints/`, `vae/`, `loras/`,
  …). If ComfyUI needs `extra_model_paths.yaml`, generate it.
- `service.uses_gpu: true`, `gpu: {typical_mb: 8000, idle_release: false}`
  (measure), and the `gpu_release` rule ([../05-platform.md](../05-platform.md)
  §4.5) calling `POST /free` after 300 s idle (idle = `/queue` empty and no
  WebSocket activity, VERIFY a reliable signal), plus
  `--disable-smart-memory` if testing shows it releases memory between jobs
  without large slowdowns.
- `install_requires: [{kind: hardware, gpu: recommended, message: …}]`.
- `docker-compose.nvidia.yml` overlay adds the GPU; without it, `--cpu`.

## 3. Starter model (open question Q4)

At install, offer a choice (`configuration: starter_model`):
`none`, or 1–2 options, each with size, licence summary and VRAM fit on 12 GB
(for example an SDXL-class model in fp16 at ~7 GB; VERIFY current good
options and their licences: CreativeML OpenRAIL-M, SDXL licence,
FLUX-schnell Apache-2.0 (fp8 variant fits?)). **Ask the owner which to offer
(Q4)** before implementing the download. Download with resume and checksum
(Hugging Face provides SHA256 in file metadata), progress shown, an optional
`HF_TOKEN` configuration field for gated models.

## 4. Access and security

- `route.access: gate`, `audience: operators`, `sign_in.method: gate`, no
  account. `streaming`/WebSockets must work (`/ws`).
- **Custom nodes:** disable installing new custom nodes via the Manager
  (VERIFY the flag/config, e.g. a security level setting). Custom nodes run
  arbitrary Python with GPU and network access. If the owner wants custom
  nodes, that is a separate decision. Note it in the app page:
  "Installing custom nodes is turned off for safety."
- Network: `mu3lab_frontend` (model downloads) plus an internal network
  shared with the chat app for image generation (`mu3lab_imagegen`). No
  `mu3lab_backend`.

## 5. Integration with the chat apps (capability `image_generation`)

- Open WebUI consumes `image_generation` ([../07-chat-choice.md](../07-chat-choice.md)
  §5): base URL `http://comfyui:8188`, a default workflow JSON shipped in
  `apps/open-webui/comfyui/` matched to the starter model, and its node
  mapping (prompt node, seed, size, model name). If the owner picked `none`
  as starter model, image generation stays off and the chat page says why.
- LobeChat: wire only if VERIFY confirms support.
- GPU busy → the plain `gpu_memory_busy` message.

## 6. Chat connector

Optional. A reviewed community MCP (read: list workflows/models/history;
write: queue prompt, off by default) or **none**. Image generation for
household members already works through the chat app. Recommend **none**
in this task. Record it.

## 7. Phone clients

None (desktop editor). Images generated through chat appear in the chat app
on phones.

## 8. Backups

`data/comfyui/user` (workflows) and `data/comfyui/output` (generated images;
can be large; owner can untick via a `backup.exclude` toggle (VERIFY the UI
supports per-folder toggles; otherwise include)). Models excluded (media).

## 9. Edge cases

| # | Case | Handling |
|---|---|---|
| C1 | CUDA OOM (other apps loaded) | Error from `/prompt` history; map to `gpu_memory_busy` in chat-provider responses; the ComfyUI UI shows its own error. |
| C2 | Driver/CUDA mismatch (host driver too old for the image's CUDA) | Install fails at health with `gpu_driver_too_old`, naming the needed driver version. Pick a CUDA base that the installer's supported driver range covers (VERIFY the minimum driver the installer accepts). |
| C3 | Huge image | `resource_guidance` states size; pre-pull on install shows progress (existing download manager). |
| C4 | Model folder empty | ComfyUI starts; the app page says "Add a model" with the folder path. |
| C5 | Output folder fills disk | A size display on the app page; a nightly-backup size warning. |
| C6 | VM acceptance has no GPU | CPU mode with a tiny test model (VERIFY a small SD1.5-class or test checkpoint) to prove the queue → image path. |

## 10. Acceptance

VM (CPU): install, open (operator), queue a tiny workflow via API, image
saved; household member gets 403. Owner's host (GPU), done **by the owner**
with your steps: generate an image from Open WebUI; GPU card shows ComfyUI
memory, released after 5 idle minutes.
