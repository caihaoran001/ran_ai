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

    # We can try to query msgSeq starting from 8425400 (which is around our recent message seq)
    # Or query with 0 to see what it returns
    msg_seq = 8425400
    if len(sys.argv) > 1:
        try:
            msg_seq = int(sys.argv[1])
        except:
            pass

    limit = 10
    if len(sys.argv) > 2:
        try:
            limit = int(sys.argv[2])
        except:
            pass

    print("=" * 60)
    print("           QiWe 开放平台同步历史消息接口测试           ")
    print("=" * 60)
    print(f"Token:  {token[:8]}...")
    print(f"GUID:   {guid}")
    print(f"参数:   msgSeq={msg_seq}, limit={limit}")
    print("-" * 60)

    payload = {
        "method": "/msg/syncMsg",
        "params": {
            "guid": guid,
            "msgSeq": msg_seq,
            "limit": limit
        }
    }

    url = "http://manager.qiweapi.com/qiwe/api/qw/doApi"
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode('utf-8'))
            print("✅ 接口响应成功！")
            print(json.dumps(data, indent=4, ensure_ascii=False))
            
            # Print parsed message details if data exists
            msg_list = data.get("data", [])
            if isinstance(msg_list, list) and msg_list:
                print("\n📖 解析后的同步消息列表:")
                for m in msg_list:
                    sender_id = m.get("senderId")
                    sender_name = m.get("senderName") or "未知发送者"
                    msg_type = m.get("msgType")
                    msg_data = m.get("msgData", {})
                    content = msg_data.get("content") or msg_data
                    seq = m.get("seq")
                    room_id = m.get("fromRoomId")
                    
                    chat_type = "群聊" if room_id and room_id != 0 else "私聊"
                    print(f" 🔹 [Seq: {seq}] [{chat_type}] {sender_name}(ID:{sender_id}): {content}")
            else:
                print("\nℹ️ 未同步到任何新消息，这说明您的 msgSeq 可能太新，或者当前队列中暂无更多消息。")
    except Exception as e:
        print(f"❌ 请求发生异常: {str(e)}")

if __name__ == "__main__":
    main()
