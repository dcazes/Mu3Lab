FROM python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322
WORKDIR /app
COPY gateway.py /app/gateway.py
ENTRYPOINT ["python", "-u", "/app/gateway.py"]
