# 基础镜像：Python 3.11 精简版
FROM python:3.11-slim

# 环境变量：关闭输出缓冲、不生成 .pyc 字节码文件
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 工作目录
WORKDIR /app

# 创建非 root 用户运行服务，并预先创建 /app/data 数据目录并授权
RUN useradd -m -u 1000 appuser && mkdir -p /app/data && chown -R appuser:appuser /app/data

# 先复制依赖清单并安装（与业务代码分层，利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制全部业务代码
COPY . .

# 容器内监听端口
EXPOSE 8765

# 健康检查：通过 python urllib 探测本地健康检查端点
# 间隔 30s、超时 5s、重试 3 次、启动宽限期 20s
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=5)"

# 以非 root 用户启动（--host/--port 固定为容器内监听地址，--no-browser 不打开浏览器）
USER appuser
ENTRYPOINT ["python", "run.py", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
