# 货代询价助手 FreightHub — 网页/服务器模式
#   docker build -t freighthub .
#   docker run -d --name freighthub -p 8765:8765 -v fh-data:/data -e FREIGHT_HUB_ADMIN_PASSWORD='改成强密码' freighthub
# 云途「登录模式」需要无头浏览器：docker build --build-arg WITH_CHROMIUM=1 -t freighthub .
FROM python:3.11-slim
ARG WITH_CHROMIUM=0
WORKDIR /srv/freighthub
RUN if [ "$WITH_CHROMIUM" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-cjk && rm -rf /var/lib/apt/lists/*; fi
COPY requirements.txt .
RUN grep -v '^pywebview' requirements.txt > req.txt && pip install --no-cache-dir -r req.txt
COPY app ./app
COPY run.py ./
RUN useradd -m -u 10001 fh && mkdir /data && chown fh /data
USER fh
ENV FREIGHT_HUB_MODE=server FREIGHT_HUB_HOST=0.0.0.0 FREIGHT_HUB_PORT=8765 FREIGHT_HUB_DATA=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/api/ping')" || exit 1
CMD ["python", "run.py", "--server"]
