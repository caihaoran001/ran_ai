"""【生产配置推送脚本】——单独手动运行，用于把生产配置覆盖到 ECS。

写两个文件到服务器 /root/ran_ai/：
  - docker-compose.prod.yml（生产编排：Dockerfile.prod + caddy 反代）
  - .env（生产密钥 / 数据库 / QiWe guid 等真实配置）

⚠️ 重要：日常部署代码的 local_prebuilt_deploy.py 已不再调用本脚本，也不会动这两个文件。
只有在【改动生产配置】时才手动运行本脚本；运行前请确保下方模板已是最新配置，
因为它会整体覆盖服务器上的 .env 与 docker-compose.prod.yml（服务器上的手改会被覆盖）。

运行：uv run --with paramiko python scratch/configure_remote_files.py
"""
import paramiko
import sys

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect('43.98.170.61', username='root', password='ranAI3621715280', timeout=10)
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    print("✅ SSH Connected. Writing docker-compose.prod.yml on ECS...")
    
    docker_compose_content = """services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile.prod
    restart: unless-stopped
    environment:
      ENVIRONMENT: production
      DATABASE_URL: ${DATABASE_URL:?DATABASE_URL is required}
      FRONTEND_PUBLIC_BASE_URL: ${FRONTEND_PUBLIC_BASE_URL:?FRONTEND_PUBLIC_BASE_URL is required}
      CORS_ALLOWED_ORIGINS: ${CORS_ALLOWED_ORIGINS:?CORS_ALLOWED_ORIGINS is required}
      BACKEND_ADMIN_API_KEY: ${BACKEND_ADMIN_API_KEY:?BACKEND_ADMIN_API_KEY is required}
      AI_BASE_URL: ${AI_BASE_URL:-https://api.deepseek.com}
      AI_API_KEY: ${AI_API_KEY:?AI_API_KEY is required}
      AI_MODEL: ${AI_MODEL:-deepseek-v4-pro}
      AI_DOCUMENT_MODEL: ${AI_DOCUMENT_MODEL:-}
      AI_TEMPERATURE: ${AI_TEMPERATURE:-0.3}
      AI_MAX_TOKENS: ${AI_MAX_TOKENS:-8000}
      AI_TIMEOUT_SECONDS: ${AI_TIMEOUT_SECONDS:-60}
      AI_MAX_CONCURRENT_REQUESTS: ${AI_MAX_CONCURRENT_REQUESTS:-10}
      AI_PROMPT_COST_PER_1M_TOKENS: ${AI_PROMPT_COST_PER_1M_TOKENS:-0}
      AI_COMPLETION_COST_PER_1M_TOKENS: ${AI_COMPLETION_COST_PER_1M_TOKENS:-0}
      AI_AGENT_ENABLED: ${AI_AGENT_ENABLED:-false}
      AGENT_MAX_LLM_CALLS: ${AGENT_MAX_LLM_CALLS:-5}
      AGENT_MAX_TOOL_CALLS: ${AGENT_MAX_TOOL_CALLS:-8}
      AGENT_TIMEOUT_SECONDS: ${AGENT_TIMEOUT_SECONDS:-40}
      WEB_SEARCH_ENABLED: ${WEB_SEARCH_ENABLED:-false}
      WEB_SEARCH_PROVIDER: ${WEB_SEARCH_PROVIDER:-tavily}
      TAVILY_API_KEY: ${TAVILY_API_KEY:-}
      WEB_SEARCH_TIMEOUT_SECONDS: ${WEB_SEARCH_TIMEOUT_SECONDS:-8}
      WEB_SEARCH_MAX_RESULTS: ${WEB_SEARCH_MAX_RESULTS:-5}
      QWEN_BASE_URL: ${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}
      QWEN_API_KEY: ${QWEN_API_KEY:-}
      QWEN_MODEL: ${QWEN_MODEL:-qwen3.7-plus}
      QWEN_IMAGE_TIMEOUT_SECONDS: ${QWEN_IMAGE_TIMEOUT_SECONDS:-180}
      EMBEDDING_BASE_URL: ${EMBEDDING_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}
      EMBEDDING_API_KEY: ${EMBEDDING_API_KEY:-}
      EMBEDDING_MODEL: ${EMBEDDING_MODEL:-text-embedding-v4}
      FAQ_VECTOR_DISTANCE_THRESHOLD: ${FAQ_VECTOR_DISTANCE_THRESHOLD:-0.35}
      CASE_VECTOR_DISTANCE_THRESHOLD: ${CASE_VECTOR_DISTANCE_THRESHOLD:-0.45}
      QIWEI_ENABLED: ${QIWEI_ENABLED:-true}
      QIWEI_TOKEN: ${QIWEI_TOKEN:?QIWEI_TOKEN is required}
      QIWEI_GUID: ${QIWEI_GUID:?QIWEI_GUID is required}
      QIWEI_CALLBACK_SECRET: ${QIWEI_CALLBACK_SECRET:?QIWEI_CALLBACK_SECRET is required}
      QIWEI_HANDOFF_AT_USER_ID: ${QIWEI_HANDOFF_AT_USER_ID:-}
      QIWEI_BASE_URL: ${QIWEI_BASE_URL:-http://manager.qiweapi.com/qiwe}
      QIWEI_BATCH_WINDOW_SECONDS: ${QIWEI_BATCH_WINDOW_SECONDS:-8}
      REMINDER_ENABLED: ${REMINDER_ENABLED:-true}
      REMINDER_CHECK_INTERVAL_SECONDS: ${REMINDER_CHECK_INTERVAL_SECONDS:-30}
      REMINDER_AT: ${REMINDER_AT:-12:00}
      REMINDER_TIMEZONE: ${REMINDER_TIMEZONE:-Asia/Shanghai}
      REMINDER_WEEKDAYS: ${REMINDER_WEEKDAYS:-1,2,3,4,5}
      INACTIVE_FOLLOWUP_ENABLED: ${INACTIVE_FOLLOWUP_ENABLED:-true}
      INACTIVE_FOLLOWUP_HOURS: ${INACTIVE_FOLLOWUP_HOURS:-24}
      OUTCOME_SNAPSHOTS_ENABLED: ${OUTCOME_SNAPSHOTS_ENABLED:-true}
      KNOWLEDGE_EVOLUTION_ENABLED: ${KNOWLEDGE_EVOLUTION_ENABLED:-false}
      KNOWLEDGE_EVOLUTION_INTERVAL_SECONDS: ${KNOWLEDGE_EVOLUTION_INTERVAL_SECONDS:-3600}
      KNOWLEDGE_EVOLUTION_LOOKBACK_HOURS: ${KNOWLEDGE_EVOLUTION_LOOKBACK_HOURS:-24}
      KNOWLEDGE_EVOLUTION_QA_AT: ${KNOWLEDGE_EVOLUTION_QA_AT:-00:00}
      KNOWLEDGE_EVOLUTION_CASE_DAYS: ${KNOWLEDGE_EVOLUTION_CASE_DAYS:-3,7,14,21,28}
      DIAGNOSTICS_HISTORY_PATH: /app/diagnostics/diagnostics_history.json
    volumes:
      - ./diagnostics:/app/diagnostics

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile.prod
    restart: unless-stopped
    environment:
      SERVER_API_BASE_URL: http://backend:8000/api
      BACKEND_ADMIN_API_KEY: ${BACKEND_ADMIN_API_KEY:?BACKEND_ADMIN_API_KEY is required}
      FRONTEND_PUBLIC_BASE_URL: ${FRONTEND_PUBLIC_BASE_URL:?FRONTEND_PUBLIC_BASE_URL is required}
      ADMIN_USERNAME: ${ADMIN_USERNAME:?ADMIN_USERNAME is required}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:?ADMIN_PASSWORD is required}
    depends_on:
      - backend

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    environment:
      PUBLIC_SITE_ADDRESS: ${PUBLIC_SITE_ADDRESS:?PUBLIC_SITE_ADDRESS is required}
    ports:
      - "${HTTP_PORT:-80}:80"
      - "${HTTPS_PORT:-443}:443"
    depends_on:
      - frontend
      - backend
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config

volumes:
  caddy_data:
  caddy_config:
"""

    sftp = client.open_sftp()
    
    with sftp.file('/root/ran_ai/docker-compose.prod.yml', 'w') as f:
        f.write(docker_compose_content)
    print("  ✅ Written /root/ran_ai/docker-compose.prod.yml")

    print("Writing .env on ECS...")
    env_content = """# App config
PUBLIC_SITE_ADDRESS=admin.humanburning.ai
FRONTEND_PUBLIC_BASE_URL=https://admin.humanburning.ai
CORS_ALLOWED_ORIGINS=https://admin.humanburning.ai
HTTP_PORT=80
HTTPS_PORT=443

# PostgreSQL (Aliyun RDS)
DATABASE_URL=postgresql+asyncpg://ran_ai:ranAI3621715280@pgm-t4nii55bp757l623.pgsql.singapore.rds.aliyuncs.com:5432/ran_ai

# Admin Portal credentials
ADMIN_USERNAME=admin
ADMIN_PASSWORD=ranAI3621715280
BACKEND_ADMIN_API_KEY=9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c

# AI Services
AI_API_KEY=sk-fc40bfc4e3f346808eef042270dfd888
AI_MODEL=deepseek-v4-pro
AI_DOCUMENT_MODEL=deepseek-v4-pro
AI_TEMPERATURE=0.3
AI_MAX_TOKENS=16000
AI_PROMPT_COST_PER_1M_TOKENS=12.55
AI_COMPLETION_COST_PER_1M_TOKENS=25.11

EMBEDDING_API_KEY=sk-6223d5336f004cec91ea032e473faab2
EMBEDDING_MODEL=text-embedding-v4

# QiWe API integration
QIWEI_ENABLED=true
QIWEI_TOKEN=de3851af-60ae-455b-9eb5-38fb7e71d8c8
QIWEI_GUID=f9d5d5f0-04ae-44b4-99cb-4fc883435b7a
QIWEI_CALLBACK_SECRET=de3851af-60ae-455b-9eb5-38fb7e71d8c8
QIWEI_HANDOFF_AT_USER_ID=1688857375621674

# Qwen API config (for image multimodal analysis)
QWEN_API_KEY=sk-6223d5336f004cec91ea032e473faab2
QWEN_MODEL=qwen3.7-plus
QWEN_PROMPT_COST_UNDER_128K_PER_1M_TOKENS=2
QWEN_COMPLETION_COST_UNDER_128K_PER_1M_TOKENS=8
"""

    with sftp.file('/root/ran_ai/.env', 'w') as f:
        f.write(env_content)
    print("  ✅ Written /root/ran_ai/.env")
    
    sftp.close()
    client.close()
    print("🎉 File configuration completed successfully!")

if __name__ == '__main__':
    main()
