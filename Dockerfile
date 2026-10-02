# TalentLens — one image, two services (API + dashboard), chosen by the command.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# dependencies first, so code changes do not reinstall them
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY sample_resumes ./sample_resumes
COPY streamlit_app.py seed.py TalentLens_Console.html ./
# scripts/ and tests/ are copied so the PostgreSQL smoke test and the suites can be run
# inside the container (docker compose exec api python scripts/check_postgres.py)
COPY scripts ./scripts
COPY tests ./tests
ENV TALENTLENS_DB=/app/data/talentlens.db
RUN mkdir -p /app/data

# run as an unprivileged user
RUN useradd --create-home talentlens && chown -R talentlens /app
USER talentlens

EXPOSE 8501 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1
CMD ["streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
