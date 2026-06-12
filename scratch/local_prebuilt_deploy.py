"""本地预构建部署脚本：本地编译前端 → 打包 → 上传 ECS → 远程 rebuild → 健康检查。

⚠️ 本脚本只部署【代码】，绝不改动生产配置：
  打包时已排除 .env 和 docker-compose.prod.yml，解压不会覆盖服务器上的生产配置与密钥。
  因此可以放心频繁部署，不会误伤线上配置（即使有人在服务器上手改过 .env）。

要修改【生产配置】（.env 里的数据库/密钥/QiWe guid，或 docker-compose.prod.yml 结构），
请单独运行：  uv run --with paramiko python scratch/configure_remote_files.py
（该脚本会用其内置模板覆盖服务器上的这两个文件——改配置时务必先同步好模板再跑它。）
"""
import paramiko
import sys
import time
import subprocess
import os

def run_local_command(cmd, cwd=None):
    print(f"🏃 Running local command: {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ Local command failed: {res.stderr}")
        sys.exit(1)
    print("  ✅ Success")
    return res.stdout

def main():
    # 1. Compile Next.js locally
    print("🛠️ Step 1: Compiling Next.js frontend locally...")
    frontend_dir = os.path.abspath("frontend")
    run_local_command("npm run build", cwd=frontend_dir)
    
    # 2. Package codebase locally (include standalone output, exclude cache & diagnostics history)
    print("\n📦 Step 2: Packaging codebase locally...")
    tar_cmd = (
        "tar --exclude='frontend/node_modules' --exclude='.venv' --exclude='*.log' --exclude='.git' "
        "--exclude='ran_ai_deploy.tar.gz' --exclude='.codex' --exclude='.antigravitycli' "
        "--exclude='.playwright-mcp' --exclude='.sisyphus' --exclude='.agents' "
        "--exclude='./.env' --exclude='./docker-compose.prod.yml' "
        "--exclude='frontend/.next/cache' --exclude='diagnostics/diagnostics_history.json' "
        "--warning=no-file-changed -czf ran_ai_deploy.tar.gz ."
    )
    res = subprocess.run(tar_cmd, shell=True, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        print(f"❌ Local tar failed (exit code {res.returncode}): {res.stderr}")
        sys.exit(1)
    print("  ✅ Success (warnings ignored)")
    
    # 3. SSH Connect to Aliyun ECS
    print("\n🔗 Step 3: Connecting to Aliyun ECS...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect('43.98.170.61', username='root', password='ranAI3621715280', timeout=10)
    except Exception as e:
        print(f"❌ Failed to connect to ECS: {e}")
        sys.exit(1)
    print("  ✅ Connected!")

    # 4. Upload Archive
    print("\n📤 Step 4: Uploading archive...")
    sftp = client.open_sftp()
    
    local_path = 'ran_ai_deploy.tar.gz'
    remote_path = '/root/ran_ai_deploy.tar.gz'
    
    def progress_callback(transferred, total):
        print(f"  Uploaded {transferred}/{total} bytes ({(transferred/total)*100:.1f}%)", end='\r')
        
    sftp.put(local_path, remote_path, callback=progress_callback)
    print("\n  ✅ Upload finished!")
    sftp.close()

    # 5. Extract and Clear Diagnostics History
    print("\n📂 Step 5: Extracting files and clearing diagnostics history on remote ECS...")
    commands = [
        "mkdir -p /root/ran_ai",
        "tar -xzf /root/ran_ai_deploy.tar.gz -C /root/ran_ai",
        "rm /root/ran_ai_deploy.tar.gz",
        "echo '[]' > /root/ran_ai/diagnostics/diagnostics_history.json"  # Clear the logs to verify updates
    ]
    
    for cmd in commands:
        stdin, stdout, stderr = client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err = stderr.read().decode().strip()
            print(f"  ❌ Command failed ({cmd}): {err}")
            client.close()
            sys.exit(1)
    print("  ✅ Remote extraction and diagnostics clearing completed!")

    # 6. Rebuild & reload containers on ECS.
    #    生产 .env 与 docker-compose.prod.yml 不在本脚本覆盖范围（打包已排除，解压不覆盖），
    #    要改生产配置请单独运行 scratch/configure_remote_files.py。
    print("\n🚀 Step 6: Rebuilding and reloading containers on ECS (Measuring Downtime)...")
    
    # Start timer
    start_time = time.time()
    
    # Run docker-compose command
    cmd = "cd /root/ran_ai && docker compose -f docker-compose.prod.yml up -d --build"
    print(f"  Running remote command: {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd)
    
    # Read exit status to ensure it ran successfully
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        err = stderr.read().decode().strip()
        print(f"  ❌ Docker Compose failed: {err}")
        client.close()
        sys.exit(1)
    
    print("  ✅ Docker Compose reload completed. Monitoring health endpoints...")
    
    # Check health checks in loop
    is_healthy = False
    max_retries = 30
    retry_interval = 1.0 # check every 1 second
    
    for attempt in range(max_retries):
        import urllib.request
        # Check backend health publicly
        try:
            req_b = urllib.request.Request("https://admin.humanburning.com/health")
            with urllib.request.urlopen(req_b, timeout=2) as response:
                b_status = str(response.getcode())
        except Exception as e:
            b_status = str(e.code) if hasattr(e, "code") else "000"
        
        # Check frontend health publicly
        try:
            req_f = urllib.request.Request("https://admin.humanburning.com/login")
            with urllib.request.urlopen(req_f, timeout=2) as response:
                f_status = str(response.getcode())
        except Exception as e:
            f_status = str(e.code) if hasattr(e, "code") else "000"
        
        print(f"  [Ping {attempt + 1}] Backend: {b_status}, Frontend: {f_status}")
        
        if b_status == "200" and f_status in ["200", "302", "307", "404"]:
            is_healthy = True
            break
            
        time.sleep(retry_interval)
        
    end_time = time.time()
    downtime = end_time - start_time
    
    if is_healthy:
        print(f"\n🎉 Deployment updated successfully!")
        print(f"⏱️ Measured service downtime: {downtime:.2f} seconds!")
    else:
        print(f"\n⚠️ Service failed to recover within 30 seconds.")
        # Print logs to debug
        stdin_l, stdout_l, stderr_l = client.exec_command("docker logs ran_ai-backend-1 --tail 30")
        print("--- Backend Logs ---")
        print(stdout_l.read().decode())
        print(stderr_l.read().decode())
        
    client.close()
    
    # Clean up local tarball
    if os.path.exists("ran_ai_deploy.tar.gz"):
        os.remove("ran_ai_deploy.tar.gz")

if __name__ == '__main__':
    main()
