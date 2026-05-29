import sys
import paramiko

sys.stdout.reconfigure(encoding='utf-8')

import os

def load_env():
    env = {}
    paths = [".env", "../.env"]
    for p in paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        env[key.strip()] = val.strip()
            break
    return env

def main():
    env = load_env()
    ip = env.get("TENCENT_SSH_IP")
    password = env.get("TENCENT_SSH_PASSWORD")
    user = env.get("TENCENT_SSH_USER", "ubuntu")

    if not ip:
        print("❌ 错误: .env 中未找到 TENCENT_SSH_IP!")
        sys.exit(1)

    if not password:
        print("❌ 错误: .env 中未找到 TENCENT_SSH_PASSWORD!")
        sys.exit(1)

    print("=" * 60)
    print("      正在连接腾讯云服务器清理积压的 8080 端口进程      ")
    print("=" * 60)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(ip, port=22, username=user, password=password, timeout=10)
        print("✅ SSH 连接成功！")
        
        # Kill any process occupying port 8080, and reload sshd just in case
        commands = [
            "sudo fuser -k 8080/tcp || true",
            "sudo systemctl reload sshd"
        ]
        
        for cmd in commands:
            print(f"🏃 正在执行远程命令: {cmd}")
            stdin, stdout, stderr = ssh.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0:
                print(f"  ✅ 执行成功")
            else:
                err = stderr.read().decode('utf-8', errors='ignore')
                print(f"  ⚠️ 执行退出码为 {exit_status}: {err.strip()}")
                
        print("🎉 端口进程清理完成！")
    except Exception as e:
        print(f"❌ 远程连接或操作失败: {str(e)}")
    finally:
        ssh.close()
        print("=" * 60)

if __name__ == "__main__":
    main()
