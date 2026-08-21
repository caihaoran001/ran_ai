ARG BACKEND_IMAGE=ran-ai-backend:local
FROM ${BACKEND_IMAGE}

# The derived image owns its own filesystem layer. Installing evaluation-only
# frameworks here does not change the backend image or any running production
# container, while the built layer is reused by every later evaluation run.
COPY eval/requirements.txt /tmp/rag-eval-requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python \
      --requirements /tmp/rag-eval-requirements.txt

ENTRYPOINT ["/app/.venv/bin/python", "/app/eval/run_full_eval.py"]
