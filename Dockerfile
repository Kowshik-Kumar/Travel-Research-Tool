FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
# AGENT_SEED / AGENTVERSE_API_KEY / ASI1_API_KEY are passed at runtime (never baked in)
CMD ["python", "agent_app.py"]
