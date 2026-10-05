FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn python-multipart
COPY mock_api.py .
EXPOSE 8085
CMD ["uvicorn", "mock_api:app", "--host", "0.0.0.0", "--port", "8085"]
