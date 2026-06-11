import paramiko
import sys
import time

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect('43.98.170.61', username='root', password='ranAI3621715280', timeout=10)
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    print("✅ SSH Connected. Restarting backend container...")
    
    # Restart the backend service
    stdin, stdout, stderr = client.exec_command("docker restart ran_ai-backend-1")
    exit_status = stdout.channel.recv_exit_status()
    if exit_status == 0:
        print("  🟢 Backend container restarted. Waiting 15 seconds for migrations and startup...")
    else:
        err = stderr.read().decode().strip()
        print(f"  🔴 Failed to restart backend container: {err}")
        client.close()
        sys.exit(1)
        
    time.sleep(15)
    
    # Check logs
    print("\n🔍 Fetching backend logs...")
    stdin, stdout, stderr = client.exec_command("docker logs ran_ai-backend-1 --tail 50")
    logs = stdout.read().decode()
    print("--- Backend Logs ---")
    print(logs)
    print("--------------------")
    
    # Run health checks
    print("\n🔍 Verifying health endpoints...")
    stdin, stdout, stderr = client.exec_command("docker exec ran_ai-backend-1 python -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').getcode())\"")
    backend_status = stdout.read().decode().strip()
    if backend_status == "200":
        print("  🟢 Backend Health Check passed (HTTP 200).")
    else:
        print(f"  🔴 Backend Health Check failed (HTTP {backend_status or 'Error'}).")
        
    stdin, stdout, stderr = client.exec_command("docker exec ran_ai-frontend-1 node -e \"const http = require('http'); http.get('http://localhost:3000', (res) => console.log(res.statusCode))\"")
    frontend_status = stdout.read().decode().strip()
    if frontend_status in ["200", "302", "307", "404"]:
        print(f"  🟢 Frontend Health Check passed (HTTP {frontend_status}).")
    else:
        print(f"  🔴 Frontend Health Check failed (HTTP {frontend_status or 'Error'}).")
        
    client.close()

if __name__ == '__main__':
    main()
