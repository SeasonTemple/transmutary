# Transmutary 常驻服务镜像。多阶段：构建 wheel → 精简运行层。
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim AS runtime
# 非 root 运行（产物目录 0700/0600，凭据只走 env — R24/KTD4/KTD5）。
RUN useradd --create-home --uid 10001 transmutary
WORKDIR /app
COPY --from=build /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

# 运行时配置目录 + 状态/产物目录（compose 挂卷覆盖；凭据从 env，不入镜像）。
ENV TRANSMUTARY_CONFIG_DIR=/config
RUN mkdir -p /config /var/lib/transmutary && chown -R transmutary:transmutary /config /var/lib/transmutary
USER transmutary

# 常驻服务入口（内嵌 APScheduler 分级调度）。
ENTRYPOINT ["transmutary-serve"]
