import urllib.request
import json
import os
import sys

# Ensure UTF-8 printing
sys.stdout.reconfigure(encoding='utf-8')

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
    if len(sys.argv) < 2:
        print("❌ 错误: 请提供目标聊天 ID (chat_id) 作为参数!")
        sys.exit(1)
        
    chat_id = sys.argv[1]
    
    # Custom message content if provided
    content = "【燃AI自动回复】收到群聊消息，API 联通测试成功！"
    if len(sys.argv) > 2:
        content = sys.argv[2]

    env = load_env()
    token = env.get("QIWEI_TOKEN")
    guid = env.get("QIWEI_GUID")
    
    if not token or not guid:
        print("❌ 错误: .env 中未找到 QIWEI_TOKEN 或 QIWEI_GUID!")
        sys.exit(1)

    headers = {
        "X-QIWEI-TOKEN": token,
        "Content-Type": "application/json"
    }

    payload = {
        "method": "/msg/sendText",
        "params": {
            "guid": guid,
            "toId": chat_id,
            "content": content
        }
    }

    url = "http://manager.qiweapi.com/qiwe/api/qw/doApi"
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode('utf-8'))
            if data.get("code") == 0:
                print(f"✅ 消息成功发送到群聊 {chat_id}!")
                print("响应:", json.dumps(data, ensure_ascii=False))
            else:
                print(f"❌ 发送失败: {data.get('msg')}")
    except Exception as e:
        print(f"❌ 请求发生异常: {str(e)}")

if __name__ == "__main__":
    main()
