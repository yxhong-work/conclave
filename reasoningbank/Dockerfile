FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY reasoning_bank/ ./reasoning_bank/
ENV RB_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "reasoning_bank.api:app", "--host", "0.0.0.0", "--port", "8000"]
