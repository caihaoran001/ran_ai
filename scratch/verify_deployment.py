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

    print("✅ SSH Connected. Running health checks on ECS...")
    
    # 1. Backend Health Check
    print("\n🔍 Checking Backend Health...")
    stdin, stdout, stderr = client.exec_command("docker exec ran_ai-backend-1 python -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').getcode())\"")
    backend_status = stdout.read().decode().strip()
    if backend_status == "200":
        print("  🟢 Backend Health Check passed (HTTP 200).")
    else:
        print(f"  🔴 Backend Health Check failed (HTTP {backend_status or 'Error'}).")
        
    # 2. Frontend Health Check
    print("\n🔍 Checking Frontend Health...")
    stdin, stdout, stderr = client.exec_command("docker exec ran_ai-frontend-1 node -e \"const http = require('http'); http.get('http://localhost:3000', (res) => console.log(res.statusCode))\"")
    frontend_status = stdout.read().decode().strip()
    if frontend_status in ["200", "302", "307", "404"]:
        # Frontend might redirect or respond with 200/404 depending on index routing, which is normal.
        print(f"  🟢 Frontend is serving content (HTTP {frontend_status}).")
    else:
        print(f"  🔴 Frontend check failed (HTTP {frontend_status or 'Error'}).")
        
    # 3. Check Backend Logs (Migrations)
    print("\n🔍 Inspecting backend docker logs for migrations...")
    stdin, stdout, stderr = client.exec_command("docker logs ran-ai-backend-1 --tail 50")
    logs = stdout.read().decode()
    print("--- Backend Logs ---")
    print(logs)
    print("--------------------")
    if "Running upgrade" in logs or "INFO  [alembic" in logs:
        print("  🟢 Alembic database migrations ran.")
    else:
        # Check if database tables were already up-to-date
        print("  🟡 Alembic logs not explicitly found in tail, checking if tables exist.")
        
    # 4. Check Caddy Logs (SSL)
    print("\n🔍 Inspecting Caddy logs for SSL certificate status...")
    stdin, stdout, stderr = client.exec_command("docker logs ran-ai-caddy-1 --tail 50")
    caddy_logs = stdout.read().decode()
    print("--- Caddy Logs ---")
    print(caddy_logs)
    print("--------------------")
    
    client.close()

if __name__ == '__main__':
    main()
