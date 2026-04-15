FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /shoppingassistantservice

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shoppingassistantservice.py demo_pb2.py demo_pb2_grpc.py ./

ENV PORT=8080
EXPOSE 8080
CMD ["python", "shoppingassistantservice.py"]
