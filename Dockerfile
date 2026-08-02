FROM python:3.12-slim

# Tesseract is a system binary, so uv cannot install it. It is the OCR fallback that is
# always present, which is what lets the container work with no API key at all.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# uv comes from PyPI rather than the ghcr.io image, so the build only needs one registry.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependencies before source, so a code change does not re-resolve the whole tree.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra api --extra vision --extra obs

# The PubChem cache ships inside app/data, so the index builds offline in under a second.
COPY app ./app
COPY forbidden_ingredients.csv product_index.csv ./

# Sample labels, so the buttons in the UI work in a fresh container.
COPY texts ./texts
COPY images ./images
COPY pdfs ./pdfs

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# No keys are baked in. GEMINI_API_KEY and the Phoenix variables are passed at run time with
# `docker run -e NAME`, which sends the name and lets Docker read the value from your shell,
# so a secret never reaches the image, the build log, or the shell history.

CMD ["uvicorn", "app.api:api", "--host", "0.0.0.0", "--port", "8000"]
