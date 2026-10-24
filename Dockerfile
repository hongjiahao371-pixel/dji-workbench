FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY app/ app/

ENV DJI_WB_DATA=/data
VOLUME ["/data", "/media"]
EXPOSE 8920

CMD ["python3", "-m", "app.web_app"]
