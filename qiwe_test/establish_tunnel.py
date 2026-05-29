import socket
import select
import sys
import threading
import os

# Ensure UTF-8 printing
sys.stdout.reconfigure(encoding='utf-8')

try:
    import paramiko
except ImportError:
    print("❌ 错误: 当前 Python 运行环境未找到 paramiko 库!")
    sys.exit(1)

def handler(chan, host, port):
    sock = socket.socket()
    try:
        sock.connect((host, port))
    except Exception as e:
        print(f"❌ 隧道桥接失败 (无法连接到本地 {host}:{port}): {str(e)}")
        return

    print(f"🤝 隧道桥接成功: 远程请求已成功接入本地服务 {host}:{port}")
    while True:
        r, w, x = select.select([chan, sock], [], [])
        if chan in r:
            data = chan.recv(1024)
            if len(data) == 0:
                break
            sock.sendall(data)
        if sock in r:
            data = sock.recv(1024)
            if len(data) == 0:
                break
            chan.sendall(data)
    chan.close()
    sock.close()
    print("🔌 隧道会话已安全关闭")

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

def reverse_forward_loop(server_port, remote_host, remote_port, transport, remote_ip):
    # 向远程服务器注册端口转发
    transport.request_port_forward("", server_port)
    print("=" * 60)
    print(f"     🚀 腾讯云专属长期 Webhook 穿透隧道已成功建立！     ")
    print(f"👉 远程公网地址: http://{remote_ip}:{server_port}")
    print(f"👉 本地接收地址: http://localhost:{remote_port}")
    print("💡 请将上面的远程公网地址配置在 QiWe 平台的“回调配置”中保存")
    print("=" * 60)
    print("⏳ 正在等待并转发公网流量中...")

    while True:
        chan = transport.accept(1000)
        if chan is None:
            continue
        thr = threading.Thread(target=handler, args=(chan, remote_host, remote_port))
        thr.setDaemon(True)
        thr.start()

def main():
    env = load_env()
    ip = env.get("TENCENT_SSH_IP")
    user = env.get("TENCENT_SSH_USER", "ubuntu")
    password = env.get("TENCENT_SSH_PASSWORD")
    
    if not ip:
        print("❌ 错误: .env 中未找到 TENCENT_SSH_IP!")
        sys.exit(1)
        
    if not password:
        print("❌ 错误: .env 中未找到 TENCENT_SSH_PASSWORD!")
        sys.exit(1)
        
    remote_port = 8080  # 腾讯云开放的接收端口
    local_port = 8000   # 本地 webhook 监听端口 (直接桥接到 Docker backend 8000)
    local_host = "localhost"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(ip, port=22, username=user, password=password, timeout=10)
    except Exception as e:
        print(f"❌ 建立隧道 SSH 连接失败: {str(e)}")
        sys.exit(1)

    transport = client.get_transport()
    # 保持长连接心跳，防止连接闲置断开 (每 30 秒发送一次心跳)
    transport.set_keepalive(30)
    
    try:
        reverse_forward_loop(remote_port, local_host, local_port, transport, ip)
    except KeyboardInterrupt:
        print("\n👋 隧道服务已手动停止。")
    except Exception as e:
        print(f"❌ 隧道连接异常中断: {str(e)}")
    finally:
        client.close()

if __name__ == "__main__":
    main()
