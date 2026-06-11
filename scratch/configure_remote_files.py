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
      AI_MODEL: ${AI_MODEL:-deepseek-v4-flash}
      AI_TEMPERATURE: ${AI_TEMPERATURE:-0.3}
      AI_MAX_TOKENS: ${AI_MAX_TOKENS:-8000}
      AI_TIMEOUT_SECONDS: ${AI_TIMEOUT_SECONDS:-60}
      AI_MAX_CONCURRENT_REQUESTS: ${AI_MAX_CONCURRENT_REQUESTS:-10}
      AI_PROMPT_COST_PER_1M_TOKENS: ${AI_PROMPT_COST_PER_1M_TOKENS:-0}
      AI_COMPLETION_COST_PER_1M_TOKENS: ${AI_COMPLETION_COST_PER_1M_TOKENS:-0}
      EMBEDDING_BASE_URL: ${EMBEDDING_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}
      EMBEDDING_API_KEY: ${EMBEDDING_API_KEY:-}
      EMBEDDING_MODEL: ${EMBEDDING_MODEL:-text-embedding-v4}
      FAQ_VECTOR_DISTANCE_THRESHOLD: ${FAQ_VECTOR_DISTANCE_THRESHOLD:-0.35}
      CASE_VECTOR_DISTANCE_THRESHOLD: ${CASE_VECTOR_DISTANCE_THRESHOLD:-0.45}
      QIWEI_ENABLED: ${QIWEI_ENABLED:-true}
      QIWEI_TOKEN: ${QIWEI_TOKEN:?QIWEI_TOKEN is required}
      QIWEI_GUID: ${QIWEI_GUID:?QIWEI_GUID is required}
      QIWEI_CALLBACK_SECRET: ${QIWEI_CALLBACK_SECRET:?QIWEI_CALLBACK_SECRET is required}
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
PUBLIC_SITE_ADDRESS=admin.humanburning.com
FRONTEND_PUBLIC_BASE_URL=https://admin.humanburning.com
CORS_ALLOWED_ORIGINS=https://admin.humanburning.com
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
AI_MODEL=deepseek-v4-flash
AI_TEMPERATURE=0.3
AI_MAX_TOKENS=16000
AI_PROMPT_COST_PER_1M_TOKENS=1.01
AI_COMPLETION_COST_PER_1M_TOKENS=2.02

EMBEDDING_API_KEY=sk-6223d5336f004cec91ea032e473faab2
EMBEDDING_MODEL=text-embedding-v4

# QiWe API integration
QIWEI_ENABLED=true
QIWEI_TOKEN=de3851af-60ae-455b-9eb5-38fb7e71d8c8
QIWEI_GUID=b0b20ec6-7b6b-409b-9733-a5e0f9e8a6be
QIWEI_CALLBACK_SECRET=de3851af-60ae-455b-9eb5-38fb7e71d8c8
QIWEI_STAFF_SENDERS=蔡浩然,助教,班主任
"""

    with sftp.file('/root/ran_ai/.env', 'w') as f:
        f.write(env_content)
    print("  ✅ Written /root/ran_ai/.env")
    
    sftp.close()
    client.close()
    print("🎉 File configuration completed successfully!")

if __name__ == '__main__':
    main()
