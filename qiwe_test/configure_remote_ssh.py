import sys
import os

# Ensure UTF-8 printing
sys.stdout.reconfigure(encoding='utf-8')

try:
    import paramiko
except ImportError:
    print("❌ 错误: 当前 Python 运行环境未找到 paramiko 库!")
    sys.exit(1)

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

    if not ip:
        print("❌ 错误: .env 中未找到 TENCENT_SSH_IP!")
        sys.exit(1)

    if not password:
        print("❌ 错误: .env 中未找到 TENCENT_SSH_PASSWORD!")
        sys.exit(1)

    print("=" * 60)
    print("      正在连接腾讯云服务器进行 SSH GatewayPorts 自动化配置      ")
    print("=" * 60)
    print(f"服务器地址: {ip}")
    print("-" * 60)

    # 1. 尝试登录 (依次尝试 root 和 ubuntu)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    users_to_try = ["root", "ubuntu"]
    connected_user = None
    
    for user in users_to_try:
        print(f"🔌 正在尝试使用用户名 [{user}] 进行 SSH 连接...")
        try:
            ssh.connect(ip, port=22, username=user, password=password, timeout=10)
            print(f"✅ 使用用户名 [{user}] 登录成功！")
            connected_user = user
            break
        except paramiko.AuthenticationException:
            print(f"❌ 用户名 [{user}] 认证失败（密码错误或该用户禁止远程登录）。")
        except Exception as e:
            print(f"❌ 尝试 [{user}] 登录发生其他异常: {str(e)}")

    if not connected_user:
        print("❌ 所有预设用户名 (root/ubuntu) 均未能成功登录，配置终止。")
        sys.exit(1)

    # 2. 读取 sshd_config
    print("\n📂 正在读取远程服务器的 /etc/ssh/sshd_config 配置文件...")
    
    # 根据用户是否为 root，决定是否加 sudo
    cmd_prefix = "sudo " if connected_user != "root" else ""
    
    # 读文件命令
    read_cmd = f"{cmd_prefix}cat /etc/ssh/sshd_config"
    stdin, stdout, stderr = ssh.exec_command(read_cmd)
    exit_status = stdout.channel.recv_exit_status()
    
    if exit_status != 0:
        err_msg = stderr.read().decode('utf-8', errors='ignore')
        print(f"❌ 读取配置文件失败 (Exit {exit_status}): {err_msg}")
        ssh.close()
        sys.exit(1)
        
    config_content = stdout.read().decode('utf-8', errors='ignore')
    print("✅ 配置文件读取成功！")

    # 3. 检查并修改 GatewayPorts
    print("⚙️ 正在检查 GatewayPorts 配置...")
    modified = False
    
    lines = config_content.split("\n")
    new_lines = []
    found = False
    
    for line in lines:
        stripped = line.strip()
        # 判断 GatewayPorts 是否在这一行中
        if "GatewayPorts" in stripped:
            if stripped.startswith("#"):
                # 如果被注释了
                new_lines.append("GatewayPorts yes")
                found = True
                modified = True
                print("✏️ 发现被注释的 GatewayPorts，已将其激活并设置为 'yes'")
            else:
                # 已经存在，更新为 yes
                new_lines.append("GatewayPorts yes")
                found = True
                modified = True
                print("✏️ 发现已有的 GatewayPorts，已将其值更新为 'yes'")
        else:
            new_lines.append(line)
            
    if not found:
        # 如果未找到，追加到末尾
        new_lines.append("\n# Added by Ran_AI automatic configuration")
        new_lines.append("GatewayPorts yes")
        modified = True
        print("✏️ 未发现 GatewayPorts 配置，已在文件末尾追加 'GatewayPorts yes'")

    # 4. 保存配置文件
    if modified:
        new_content = "\n".join(new_lines)
        
        # 将新内容写入远程文件的安全做法（先写到临时文件，再 sudo mv 覆盖）
        temp_path = "/tmp/sshd_config_temp"
        print(f"💾 正在保存修改到临时文件 {temp_path}...")
        
        # 使用 SFTP 写入临时目录 (因为 /tmp 是所有用户可写的)
        sftp = ssh.open_sftp()
        try:
            with sftp.open(temp_path, "w") as f:
                f.write(new_content)
        except Exception as e:
            print(f"❌ 写入临时文件失败: {str(e)}")
            sftp.close()
            ssh.close()
            sys.exit(1)
        sftp.close()
        
        # 覆盖远程 sshd_config 并设置权限
        print("💾 正在使用管理员权限覆盖 /etc/ssh/sshd_config...")
        mv_cmd = f"{cmd_prefix}cp {temp_path} /etc/ssh/sshd_config && {cmd_prefix}chmod 600 /etc/ssh/sshd_config && {cmd_prefix}rm -f {temp_path}"
        stdin, stdout, stderr = ssh.exec_command(mv_cmd)
        exit_status = stdout.channel.recv_exit_status()
        
        if exit_status != 0:
            err_msg = stderr.read().decode('utf-8', errors='ignore')
            print(f"❌ 覆盖配置文件失败 (Exit {exit_status}): {err_msg}")
            ssh.close()
            sys.exit(1)
        print("✅ 配置文件修改并保存成功！")
    else:
        print("ℹ️ GatewayPorts 配置已是 'yes'，无需修改。")

    # 5. 重启/重载 SSH 服务以生效
    print("\n🔄 正在重载远程服务器上的 SSH 服务以应用更改...")
    commands = [
        f"{cmd_prefix}systemctl reload sshd",
        f"{cmd_prefix}systemctl reload ssh",
        f"{cmd_prefix}service ssh reload",
        f"{cmd_prefix}systemctl restart sshd"
    ]
    
    success = False
    for cmd in commands:
        stdin, stdout, stderr = ssh.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status == 0:
            print(f"✅ 成功执行重载命令: {cmd}")
            success = True
            break
            
    if not success:
        print("⚠️ 尝试执行 SSH 重启命令均未返回成功，建议您手动在服务器上重启 SSH 服务以确保生效。")
    else:
        print("🎉 远程 SSH 服务重载成功！")

    ssh.close()
    print("=" * 60)
    print("🎉 腾讯云服务器 SSH 穿透前置配置已全部圆满配置完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
