import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import sys

# Ensure UTF-8 printing in terminal
sys.stdout.reconfigure(encoding='utf-8')

class QiWeWebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        try:
            payload = json.loads(post_data.decode('utf-8'))
            
            # Send 200 OK immediately
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"")
            
            # Process the data
            if payload.get("code") == 0:
                messages = payload.get("data", [])
                for msg in messages:
                    self.process_message(msg)
            else:
                print(f"⚠️ 收到异常回调数据: {payload.get('msg')}")
                
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            print(f"❌ 解析回调失败: {str(e)}")

    def process_message(self, msg):
        print("\nRAW MSG:", json.dumps(msg, ensure_ascii=False))
        cmd = msg.get("cmd")
        msg_type = msg.get("msgType")
        msg_data = msg.get("msgData", {})
        sender_name = msg_data.get("sender_name") or msg.get("userId")
        timestamp = msg.get("timestamp")

        
        print("\n" + "="*50)
        
        # 1. 账号状态变动 (cmd = 11016)
        if cmd == 11016:
            status_code = msg_data.get("code")
            status_msg = msg_data.get("msg", "未知状态")
            print(f"🔄 【账号状态变动】")
            print(f" 🔹 状态码: {status_code}")
            print(f" 🔹 状态说明: {status_msg}")
            
        # 2. 正常聊天消息 (cmd = 15000)
        elif cmd == 15000:
            chat_id = msg_data.get("chat_id")
            is_group = chat_id is not None
            chat_type = "群聊" if is_group else "单聊"
            
            # 文本消息
            if msg_type in (0, 2):
                content = msg_data.get("content", "")
                print(f"💬 【收到 {chat_type} 文本消息】")
                print(f" 👤 发送人: {sender_name}")
                print(f" 📝 内容: {content}")
                if is_group:
                    print(f" 👥 群聊ID: {chat_id}")
            
            # 图片消息
            elif msg_type in (7, 14, 101):
                img_url = msg_data.get("cdnUrl") or msg_data.get("cloudUrl", "未知链接")
                print(f"🖼️ 【收到 {chat_type} 图片消息】")
                print(f" 👤 发送人: {sender_name}")
                print(f" 🔗 图片链接: {img_url}")
                
            # 文件消息
            elif msg_type in (15, 20, 102):
                file_name = msg_data.get("fileName", "未知文件名")
                file_url = msg_data.get("cdnUrl", "未知链接")
                print(f"📁 【收到 {chat_type} 文件消息】")
                print(f" 👤 发送人: {sender_name}")
                print(f" 📦 文件名: {file_name}")
                print(f" 🔗 下载链接: {file_url}")
                
            # 其他消息类型
            else:
                print(f"✉️ 【收到 {chat_type} 其他消息 (msgType={msg_type})】")
                print(f" 👤 发送人: {sender_name}")
                print(f" 📊 原始数据: {json.dumps(msg_data, ensure_ascii=False)}")
                
        # 3. 系统/群变动通知 (cmd = 15500)
        elif cmd == 15500:
            print(f"⚙️ 【收到系统变动通知】")
            event_map = {
                1001: "群名变更",
                1002: "新增群成员 (有人入群)",
                1003: "移除群成员 (有人被踢)",
                1005: "群成员自己退群",
                1006: "群新增 (创建群)",
                1022: "转让群主",
                1023: "群解散",
                1043: "群管理员变动",
                2160: "聊天标签变动"
            }
            event_desc = event_map.get(msg_type, f"未知系统事件(msgType={msg_type})")
            print(f" 🔹 事件名称: {event_desc}")
            print(f" 🔹 详细数据: {json.dumps(msg_data, ensure_ascii=False)}")
            
        else:
            print(f"🔔 【收到未知大类通知 (cmd={cmd})】")
            print(f" 📊 原始数据: {json.dumps(msg, ensure_ascii=False)}")
            
        print("="*50)

def run(port=8080):
    server_address = ('', port)
    httpd = HTTPServer(server_address, QiWeWebhookHandler)
    print("="*60)
    print(f"       QiWe 独立 Webhook 监听服务器已在端口 {port} 启动       ")
    print(f"👉 监听地址: http://localhost:{port}")
    print("="*60)
    print("⏳ 正在等待 QiWe 平台事件推送...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 监听服务器已停止。")

if __name__ == '__main__':
    port = 8080
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except:
            pass
    run(port)
