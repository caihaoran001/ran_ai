"""本地预构建部署脚本：本地编译前端 → 打包 → 上传 ECS → 远程 rebuild → 健康检查。

⚠️ 本脚本只部署【代码】，绝不改动生产配置：
  打包时已排除 .env 和 docker-compose.prod.yml，解压不会覆盖服务器上的生产配置与密钥。
  因此可以放心频繁部署，不会误伤线上配置（即使有人在服务器上手改过 .env）。

要修改【生产配置】（.env 里的数据库/密钥/QiWe guid，或 docker-compose.prod.yml 结构），
请单独运行：uv run --with paramiko python scratch/configure_remote_files.py
（compose 取仓库当前文件，.env 取该脚本内模板；运行会覆盖服务器对应文件。）
"""
import paramiko
import sys
import time
import subprocess
import os
import hashlib

ECS_HOST = '43.98.170.61'
ECS_USER = 'root'
ECS_PASSWORD = 'ranAI3621715280'
UPLOAD_CHUNK = 4 * 1024 * 1024
UPLOAD_ATTEMPTS = 8


def connect_ecs():
    """带 keepalive 的连接：长时间跑 build 时主连接不至于被静默掐断。"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ECS_HOST, username=ECS_USER, password=ECS_PASSWORD, timeout=20)
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(15)
    return client


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def remote_sha256(client, remote_path):
    _, stdout, _ = client.exec_command(f'sha256sum {remote_path}', timeout=600)
    out = stdout.read().decode('utf-8', 'replace').split()
    return out[0] if out else ''


def remote_size(client, remote_path):
    _, stdout, _ = client.exec_command(f'stat -c %s {remote_path} 2>/dev/null || echo 0', timeout=60)
    try:
        return int(stdout.read().decode().strip() or 0)
    except ValueError:
        return 0


def upload_resumable(local_path, remote_path, total):
    """分块上传；断了就按远端已有大小续传。返回 True 表示远端大小已与本地一致。"""
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        client = connect_ecs()
        try:
            offset = remote_size(client, remote_path)
            if offset > total:
                print(f"  Remote bigger than local ({offset}>{total}); stale archive, removing")
                client.exec_command(f'rm -f {remote_path}')[1].channel.recv_exit_status()
                offset = 0
            if offset == total:
                return True
            print(f"  Attempt {attempt}: resuming at {offset/1e6:.1f}MB of {total/1e6:.1f}MB")
            sftp = client.open_sftp()
            sftp.get_channel().settimeout(120)
            with open(local_path, 'rb') as src, sftp.open(remote_path, 'ab' if offset else 'wb') as dst:
                dst.set_pipelined(True)
                src.seek(offset)
                sent = offset
                last = time.time()
                while True:
                    block = src.read(UPLOAD_CHUNK)
                    if not block:
                        break
                    dst.write(block)
                    sent += len(block)
                    if time.time() - last > 3:
                        print(f"    {sent/1e6:.1f}/{total/1e6:.1f}MB ({sent*100/total:.0f}%)", end='\r')
                        last = time.time()
            sftp.close()
            print(f"    {total/1e6:.1f}/{total/1e6:.1f}MB (100%)")
            if remote_size(client, remote_path) == total:
                return True
            print("  Size mismatch after write; will resume")
        except Exception as exc:
            print(f"  Upload interrupted ({type(exc).__name__}: {exc}); will resume")
            time.sleep(3)
        finally:
            client.close()
    return False


def run_local_command(cmd, cwd=None):
    print(f"🏃 Running local command: {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ Local command failed: {res.stderr}")
        sys.exit(1)
    print("  ✅ Success")
    return res.stdout

def main():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    archive_path = os.path.join(project_root, "ran_ai_deploy.tar.gz")

    # 0. Capture the git commit being deployed (记录本次上线截止到哪个 commit)。
    #    .git 不进 tar 包，服务器上无 git 历史，故在本地取好版本信息，
    #    随代码一起写到 ECS 的 /root/ran_ai/DEPLOYED_VERSION，方便日后追溯线上版本。
    def _git(cmd):
        r = subprocess.run(cmd, shell=True, cwd=project_root, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    git_sha = _git("git rev-parse HEAD")
    git_branch = _git("git rev-parse --abbrev-ref HEAD")
    git_subject = _git("git log -1 --pretty=%s")
    git_dirty = _git("git status --porcelain")
    deploy_ts = time.strftime("%Y-%m-%d %H:%M:%S %z")
    version_line = (
        f"commit: {git_sha}\nbranch: {git_branch}\nsubject: {git_subject}\n"
        f"deployed_at: {deploy_ts}\nworktree_dirty: {'yes' if git_dirty else 'no'}\n"
    )
    print(f"🏷️ Deploying commit {git_sha[:12]} ({git_branch}) — {git_subject}")
    if git_dirty:
        print("  ⚠️ 警告：工作区有未提交改动，本次上线的代码与该 commit 不完全一致。")

    # 1. Compile Next.js locally
    print("🛠️ Step 1: Compiling Next.js frontend locally...")
    frontend_dir = os.path.join(project_root, "frontend")
    run_local_command("npm run build", cwd=frontend_dir)
    
    # 2. Package codebase locally (include standalone output, exclude cache & diagnostics history)
    print("\n📦 Step 2: Packaging codebase locally...")
    tar_cmd = (
        "tar --exclude='frontend/node_modules' --exclude='.venv' --exclude='__pycache__' "
        "--exclude='*.pyc' --exclude='*.log' --exclude='.git' "
        "--exclude='ran_ai_deploy.tar.gz' --exclude='.codex' --exclude='.antigravitycli' "
        "--exclude='.playwright-mcp' --exclude='.sisyphus' --exclude='.agents' "
        "--exclude='.impeccable' --exclude='docs/wip' "
        # 本地资料/调研目录不参与运行，也不该被送上生产（各几百 KB，体积不是重点）。
        # 包体 ~80MB 的大头是 .next/standalone 里的运行时 node_modules，那是必需的，别动。
        "--exclude='./客户调研' --exclude='./文字记录' --exclude='./第三次参考文件' "
        "--exclude='./第三次参考文件修正版' "
        "--exclude='./.env' --exclude='./docker-compose.prod.yml' "
        "--exclude='frontend/.next/cache' --exclude='diagnostics/diagnostics_history.json' "
        "--warning=no-file-changed -czf ran_ai_deploy.tar.gz ."
    )
    res = subprocess.run(tar_cmd, shell=True, cwd=project_root, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        print(f"❌ Local tar failed (exit code {res.returncode}): {res.stderr}")
        sys.exit(1)
    if not os.path.exists(archive_path):
        print(f"❌ Local tar did not create archive: {archive_path}")
        if res.stderr:
            print(res.stderr)
        sys.exit(1)
    print("  ✅ Success (warnings ignored)")
    
    # 3. SSH Connect to Aliyun ECS
    print("\n🔗 Step 3: Connecting to Aliyun ECS...")
    try:
        client = connect_ecs()
    except Exception as e:
        print(f"❌ Failed to connect to ECS: {e}")
        sys.exit(1)
    print("  ✅ Connected!")

    # 4. Upload Archive（分块续传 + 解压前校验）
    #    这台 ECS 上一次性 sftp.put ~80MB 会静默断流：远端只落一半、不抛错，
    #    脚本会一直干等，而半截包一旦解压就用残缺代码覆盖了线上（已踩两次）。
    #    所以改成按块写、断了按远端已有大小续传，并且 sha256 不一致就中止。
    print("\n📤 Step 4: Uploading archive (resumable, verified)...")
    remote_path = '/root/ran_ai_deploy.tar.gz'
    local_digest = sha256_of(archive_path)
    total_bytes = os.path.getsize(archive_path)
    print(f"  Local {total_bytes/1e6:.1f}MB sha256={local_digest[:16]}…")
    client.close()  # 上传期间用独立连接，避免主连接长时间空转被掐断
    if not upload_resumable(archive_path, remote_path, total_bytes):
        print("❌ Upload failed after retries; remote partial archive left unextracted.")
        sys.exit(1)
    client = connect_ecs()
    remote_digest = remote_sha256(client, remote_path)
    print(f"  Remote sha256={remote_digest[:16]}…")
    if remote_digest != local_digest:
        print("❌ Archive checksum mismatch — refusing to extract (production code untouched).")
        client.close()
        sys.exit(1)
    print("  ✅ Upload verified!")

    # 5. Extract and Clear Diagnostics History
    print("\n📂 Step 5: Extracting files and clearing diagnostics history on remote ECS...")
    import base64
    version_b64 = base64.b64encode(version_line.encode("utf-8")).decode("ascii")
    commands = [
        "mkdir -p /root/ran_ai",
        "tar -xzf /root/ran_ai_deploy.tar.gz -C /root/ran_ai",
        "rm /root/ran_ai_deploy.tar.gz",
        "echo '[]' > /root/ran_ai/diagnostics/diagnostics_history.json",  # Clear the logs to verify updates
        # 记录本次上线版本（base64 传输避免中文提交标题的 shell 转义问题）
        f"echo '{version_b64}' | base64 -d > /root/ran_ai/DEPLOYED_VERSION",
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

    # 6. Build the new images, migrate the business schema, initialize/upgrade
    #    LangGraph checkpoint tables against the existing RDS, then reload
    #    containers. Running both DDL steps before `up -d` means a migration or
    #    permission failure leaves the current production containers untouched.
    #    生产 .env 与 docker-compose.prod.yml 不在本脚本覆盖范围（打包已排除，解压不覆盖），
    #    要改生产配置请单独运行 scratch/configure_remote_files.py。
    print("\n🚀 Step 6: Rebuilding and reloading containers on ECS (Measuring Downtime)...")
    
    # Start timer
    start_time = time.time()
    
    deploy_commands = [
        (
            "Docker Compose build (business services only)",
            "cd /root/ran_ai && docker compose -f docker-compose.prod.yml build backend frontend",
        ),
        (
            "Alembic business schema migration",
            "cd /root/ran_ai && docker compose -f docker-compose.prod.yml run --rm --no-deps backend uv run alembic upgrade head",
        ),
        (
            "LangGraph checkpoint setup",
            "cd /root/ran_ai && docker compose -f docker-compose.prod.yml run --rm --no-deps backend uv run ran-ai-checkpoint-setup",
        ),
        (
            "Docker Compose reload (keep ingress/caddy running)",
            "cd /root/ran_ai && docker compose -f docker-compose.prod.yml up -d --no-deps backend frontend",
        ),
    ]
    for label, cmd in deploy_commands:
        print(f"  Running remote command ({label}): {cmd}")
        stdin, stdout, stderr = client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err = stderr.read().decode().strip()
            print(f"  ❌ {label} failed: {err}")
            client.close()
            sys.exit(1)
    
    print("  ✅ Docker Compose reload completed.")

    # 6.5 清理构建缓存，防止每次 --build 累积的旧缓存层滚雪球撑爆 ECS 磁盘。
    #     只删未被引用的构建缓存，不影响运行中的容器/镜像。
    print("\n🧹 Step 6.5: Pruning stale Docker build cache on ECS...")
    _, prune_out, _ = client.exec_command("docker builder prune -f 2>&1 | tail -1")
    print(f"  {prune_out.read().decode().strip()}")

    print("\n🩺 Monitoring health endpoints...")

    # Check health checks in loop
    is_healthy = False
    max_retries = 30
    retry_interval = 1.0 # check every 1 second
    
    for attempt in range(max_retries):
        import urllib.request
        # Check backend health publicly
        try:
            req_b = urllib.request.Request("https://admin.humanburning.ai/health")
            with urllib.request.urlopen(req_b, timeout=2) as response:
                b_status = str(response.getcode())
        except Exception as e:
            b_status = str(e.code) if hasattr(e, "code") else "000"
        
        # Check frontend health publicly
        try:
            req_f = urllib.request.Request("https://admin.humanburning.ai/login")
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
    if os.path.exists(archive_path):
        os.remove(archive_path)

if __name__ == '__main__':
    main()
