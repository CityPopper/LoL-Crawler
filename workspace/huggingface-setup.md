# Hugging Face Dataset Setup

_For storing anonymized LoL pipeline seed data._

---

## 1. Create the Dataset Repository

1. Go to https://huggingface.co/new-dataset
2. **Repository name**: `lol-pipeline-seed` (or similar)
3. **Type**: Dataset
4. **Visibility**: **Public** — anonymized data only; no PII
5. Click **Create dataset**

Note the full repo ID: `{your-username}/lol-pipeline-seed`

---

## 2. Generate a Token (write access, this repo only)

Use a **fine-grained token** — minimal blast radius:

1. Go to https://huggingface.co/settings/tokens
2. Click **New token**
3. **Name**: `lol-pipeline-upload`
4. **Type**: **Fine-grained** (not Classic)
5. Under **Repositories**:
   - Click **Add a repository**
   - Select `{your-username}/lol-pipeline-seed`
   - Permission: **Write**
6. No other permissions needed — leave everything else off
7. Click **Create token** → copy immediately (shown once)

---

## 3. Add the Token to `.env`

In the project root `.env`:

```
HUGGINGFACE_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

The repo ID (`{your-username}/lol-pipeline-seed`) is derived automatically from the token at runtime via `HfApi().whoami()` — no second env var needed.

---

## 4. Security Notes

- `.env` is **gitignored** — token never enters git history
- Token has **write access to one repo only** — no org access, no model access
- Contributors pulling seed data need **no token** — public dataset, `huggingface-cli download` works unauthenticated
- If the token is leaked: revoke at https://huggingface.co/settings/tokens and generate a new one — the dataset is unaffected

---

## 5. Verify (optional)

After adding the token:

```bash
pip install huggingface_hub
python -c "from huggingface_hub import HfApi; print(HfApi().whoami(token=open('.env').read().split('HUGGINGFACE_TOKEN=')[1].split()[0])['name'])"
```

Should print your username without errors.
