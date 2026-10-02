#!/usr/bin/env bash
# PanWatch 生产部署脚本(由 CI 经 SSH 上传后以 root bash 执行,见 .github/workflows/deploy.yml)。
# 结构沿用 stock-agent-deploy.sh 的成熟模式:SHA 校验 → compose 校验 → 拉镜像 →
# 容器内冒烟 → 重建服务 → 本机健康检查 → 原子写 image.env;失败时回滚上一版本。
# 防阻塞要点:
#  - 每步都有界等待并输出诊断(docker logs),不无限等;
#  - compose/env 缺失时立刻报"第三步未完成",不猜测、不创建;
#  - 失败经 trap 恢复上一镜像,image.env 原子更新。

set -Eeuo pipefail

readonly IMAGE_REPOSITORY="ghcr.io/mingyuexinc/panwatch"
readonly COMPOSE_DIR="${PANWATCH_COMPOSE_DIR:-/srv/compose/panwatch}"
readonly COMPOSE_FILE="${PANWATCH_COMPOSE_FILE:-$COMPOSE_DIR/compose.yaml}"
readonly APP_ENV_FILE="${PANWATCH_ENV_FILE:-/etc/panwatch/panwatch.env}"
readonly IMAGE_ENV_FILE="${PANWATCH_IMAGE_ENV_FILE:-/etc/panwatch/image.env}"
# 首启会向 /app/data/playwright 下载 Chromium(约 170MB),健康窗口给足 5 分钟。
readonly HEALTH_ATTEMPTS="${PANWATCH_HEALTH_ATTEMPTS:-60}"
readonly HEALTH_INTERVAL_SECONDS="${PANWATCH_HEALTH_INTERVAL_SECONDS:-5}"
# compose 需把 8000 端口发布到宿主机 127.0.0.1(第三步的约定),Caddy 反代同一地址。
readonly HEALTH_URL="${PANWATCH_HEALTH_URL:-http://127.0.0.1:8000/api/health}"

usage() {
  echo "Usage: $0 <40-character-git-sha>" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 64
fi

commit_sha="$1"
if [[ ! "$commit_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid commit SHA: $commit_sha" >&2
  exit 64
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: Compose file not found: $COMPOSE_FILE" >&2
  echo "服务器侧尚未初始化(部署三件套第三步):参考 deploy/README.md 创建 compose 服务后再开启部署。" >&2
  exit 70
fi
if [[ ! -f "$APP_ENV_FILE" ]]; then
  echo "ERROR: Environment file not found: $APP_ENV_FILE" >&2
  echo "按 deploy/README.md 准备生产环境变量文件后重试。" >&2
  exit 78
fi

candidate_image="$IMAGE_REPOSITORY:$commit_sha"
previous_image=""
previous_image_file_exists=false
service_started=false
image_env_updated=false
if [[ -f "$IMAGE_ENV_FILE" ]]; then
  previous_image="$(sed -n 's/^PANWATCH_IMAGE=//p' "$IMAGE_ENV_FILE" | head -n 1)"
  if [[ -n "$previous_image" ]]; then
    previous_image_file_exists=true
  fi
fi

compose() {
  PANWATCH_IMAGE="$candidate_image" \
    docker compose --env-file "$APP_ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

compose_with_image() {
  local image="$1"
  shift
  PANWATCH_IMAGE="$image" \
    docker compose --env-file "$APP_ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

write_image_env_value() {
  local image="$1"
  local image_env_directory temporary_file
  image_env_directory="$(dirname "$IMAGE_ENV_FILE")"
  install -d -m 0750 "$image_env_directory"
  temporary_file="$(mktemp "$image_env_directory/.image.env.XXXXXX")"
  printf 'PANWATCH_IMAGE=%s\n' "$image" > "$temporary_file"
  chmod 0640 "$temporary_file"
  mv -f "$temporary_file" "$IMAGE_ENV_FILE"
}

restore_image_env() {
  if [[ "$previous_image_file_exists" == true ]]; then
    write_image_env_value "$previous_image"
  else
    rm -f "$IMAGE_ENV_FILE"
  fi
}

dump_service_logs() {
  # compose.yaml 引用 ${PANWATCH_IMAGE},诊断路径也必须带上该变量,
  # 否则回滚/排障输出会先被 compose 的变量校验失败淹没。
  PANWATCH_IMAGE="${PANWATCH_IMAGE:-$candidate_image}" \
    docker compose --env-file "$APP_ENV_FILE" -f "$COMPOSE_FILE" \
    logs --tail 80 panwatch 2>&1 || true
}

rollback_service() {
  if [[ -z "$previous_image" ]]; then
    compose_with_image "$candidate_image" stop panwatch >/dev/null 2>&1 || true
    return
  fi
  compose_with_image "$previous_image" \
    up -d --force-recreate --no-deps panwatch >/dev/null 2>&1 || true
}

handle_exit() {
  local status="$?"
  if [[ "$status" -ne 0 ]]; then
    set +e
    echo "Deployment failed (exit $status); service logs:" >&2
    dump_service_logs
    if [[ "$service_started" == true ]]; then
      echo "Restoring the previous service image" >&2
      rollback_service
    fi
    if [[ "$image_env_updated" == true ]]; then
      restore_image_env
    fi
  fi
  exit "$status"
}

trap handle_exit EXIT

echo "Validating Compose configuration"
compose config --quiet

echo "Pulling $candidate_image"
compose pull panwatch

echo "Running in-container smoke test (imports)"
# --pull never: 镜像已在上一步显式拉取,后续步骤不再触网(避免隐式拉取
# 慢仓库造成阻塞)。
compose run --rm --no-deps --pull never panwatch \
  python -c "import marketdata, pan_agent, pan_agent_token_meter, pan_agent_tool_research, akshare, apprise, weasyprint, playwright; print('smoke imports OK')" \
  >/dev/null

echo "Recreating service"
compose up -d --force-recreate --no-deps --pull never panwatch
service_started=true

service_ready=false
for attempt in $(seq 1 "$HEALTH_ATTEMPTS"); do
  if curl --fail --silent --show-error --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then
    service_ready=true
    break
  fi
  sleep "$HEALTH_INTERVAL_SECONDS"
done

if [[ "$service_ready" != true ]]; then
  echo "ERROR: service did not become healthy within $((HEALTH_ATTEMPTS * HEALTH_INTERVAL_SECONDS))s" >&2
  exit 1
fi

write_image_env_value "$candidate_image"
image_env_updated=true
echo "Deployment succeeded: $candidate_image"
