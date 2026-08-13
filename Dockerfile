FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./llm_shield_proxy ./llm_shield_proxy

EXPOSE 8000

CMD ["uvicorn", "llm_shield_proxy.main:app", "--host", "0.0.0.0", "--port", "8000"]
