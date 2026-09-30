# Dota 2 选人助手 —— Web 面板容器
#
# 设计要点
#   * 零第三方依赖：项目只用标准库，所以不装任何 pip 包，镜像就是 python-slim + 源码。
#   * 数据内置：data/ 下的英雄库、别名表、对位矩阵、版本强度都在构建时打进镜像，
#     所以容器起来后**离线可用**；只有主动刷新/同步时才需要出网。
#   * 可写区只有挂载的卷：/app/data（缓存 + 配置）。
#   * 健康检查用标准库 urllib，不依赖 curl/wget。
#
# 构建：docker build -t dota2-assistant:latest .
# 运行：见 docs/agent-deploy.md 或 docker-compose.yml

FROM python:3.11-slim

LABEL org.opencontainers.image.title="dota2-assistant" \
      org.opencontainers.image.description="Dota 2 BP 阶段选人助手（Web 面板，仅使用公开数据）" \
      org.opencontainers.image.source="https://github.com/ChanlerYve/dota2-assistant" \
      org.opencontainers.image.licenses="MIT"

# 只使用公开 HTTP 接口，不需要注入/读内存/OCR
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app \
    D2A_HOST=0.0.0.0 \
    D2A_PORT=8787 \
    D2A_CONFIG=/app/data/config.json \
    D2A_DATA_DIR=/app/data \
    D2A_BASELINE_DIR=/opt/d2a-baseline/data

WORKDIR /app

# 先拷最小集合，让源码改动尽量复用缓存层
# tools/ 里有容器内需要的东西：docker_entrypoint.py（入口/健康检查）
# 与 sync_results.py（赛后同步，用户可 docker exec 调用）
COPY d2a/ ./d2a/
COPY tools/ ./tools/
COPY data/ ./data/

# 基线数据副本：data 卷挂上来后会**遮住** /app/data，
# 所以把一份原始数据另存到 /opt 下，由入口脚本在卷为空时播种进去。
# 这样无论用 bind mount 还是全新命名卷，首次启动都能直接跑。
RUN mkdir -p /opt/d2a-baseline/data \
    && cp -a /app/data/. /opt/d2a-baseline/data/ \
    && rm -rf /opt/d2a-baseline/data/cache \
    && mkdir -p /app/data/cache \
    && chmod -R a+rX /opt/d2a-baseline \
    && chmod -R 777 /app/data

# 非 root 运行更安全；用固定 uid 便于宿主卷权限可预期
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin d2a \
    && chown -R 10001:10001 /app
USER 10001

# 构建期验证：运行用户必须能读到基线（否则首次启动播种会失败）。
# 这条断言失败会让构建直接中断，而不是等到运行时才发现挂载卷是空的。
RUN test -r /opt/d2a-baseline/data/heroes.json \
    && python -c "import json,pathlib; d=json.loads(pathlib.Path('/opt/d2a-baseline/data/heroes.json').read_text(encoding='utf-8')); n=len(d['heroes']); assert n >= 100, n; print(f'baseline ok: {n} heroes')"

EXPOSE 8787

# 健康检查：读 /api/health（该接口在启用 token 时也放行）
# 用绝对路径 + 显式 PYTHONPATH，不依赖 WORKDIR/当前目录，避免编排器改 cwd 后失效
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "/app/tools/docker_entrypoint.py", "--healthcheck"]

ENTRYPOINT ["python", "/app/tools/docker_entrypoint.py"]
