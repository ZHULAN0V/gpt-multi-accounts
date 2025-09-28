FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" docker requests
COPY spawner.py /app/spawner.py
EXPOSE 8080
CMD ["uvicorn","spawner:app","--host","0.0.0.0","--port","8080"]