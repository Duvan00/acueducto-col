FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
WORKDIR /app/backend
CMD ["sh", "-c", "python -m app.semillas && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
