FROM python:3.12-slim
WORKDIR /srv
COPY services/privacy_filter/requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY services/__init__.py services/__init__.py
COPY services/privacy_filter services/privacy_filter
EXPOSE 8790
CMD ["uvicorn", "services.privacy_filter.server:app", "--host", "0.0.0.0", "--port", "8790"]
